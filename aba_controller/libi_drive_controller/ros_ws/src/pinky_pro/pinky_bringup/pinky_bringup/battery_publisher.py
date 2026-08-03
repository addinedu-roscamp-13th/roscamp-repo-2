"""배터리 전압·퍼센트 발행.

## [2026-07-30] 값이 튀는 것을 잡았다

실측(5초 주기): `6.78 → 6.35 → 6.55 → 6.69 → 6.76 → 6.64 → 6.78 → 6.64 → 5.88` V.
같은 배터리에서 0.9V 가 튄다. ADC 노이즈가 아니라 **모터 부하 시 전압 강하**다.
`Battery.get_voltage()` 가 ADC 20번을 평균내지만 그 20번은 수십 ms 안에 끝나서, 수백 ms
짜리 부하 강하는 그 평균에 그대로 들어온다.

고친 것 두 가지:

1. **사이클마다 전압을 한 번만 읽는다.** 예전에는 타이머가 둘이고 각자 `get_voltage()` 를
   불렀다(퍼센트 쪽은 `battery_percentage()` 안에서 또 읽었다). 그래서 `battery/voltage` 와
   `battery/percent` 가 **서로 다른 I2C 읽기**에서 나왔고, 부하 구간에서 두 값이 어긋났다
   (실측: 로그 6.55V, percent 25.4% ≒ 7.0V). 이제 한 표본으로 둘을 만든다.
2. **표본을 창에 모아 중앙값을 낸다** (`voltage_filter.VoltageFilter`). 이동평균이 아니라
   중앙값인 이유는 그 파일 주석에 있다 — 요약하면 순간 강하는 무시하고 **지속적 하락은
   그대로 통과**시켜야 저전압 판단이 흐려지지 않기 때문이다.

## [2026-07-30 저녁] 그래도 게이지가 떨려서 창을 넓히고 사표대를 걸었다

실측(창 5): `6.68 / 6.69 / 6.71 / 6.73 / 6.75` V — 전압은 ±0.035V 로 잘 잡혔는데
**퍼센트가 16%~23% 사이를 춤췄다.** 원인은 필터가 아니라 **눈금 폭**이다:
`battery_percentage` 가 6.5~7.8V(=1.3V)를 0~100% 에 펴므로 **0.01V 가 약 0.77%p** 다.

그래서 두 손잡이를 같이 돌렸다 — 자세한 근거는 `voltage_filter.py` 주석에 있다.
  · `reject_run`(k) **3** → 창 `2k+1` = 7 (5초 주기에서 35초).
    창을 직접 주지 않는 이유: `2k+1` 이어야 "길이 k 이하의 강하를 버린다"가 성립한다.
  · `voltage_deadband` **0.03V** (≈2.3%p) — 이만큼 안 벌어지면 직전 값을 유지한다

⚠️ 사표대는 **직전 출력**과 비교하므로 천천히 내려가는 진짜 방전은 누적되어 결국 따라간다.
   숨기는 게 아니라 늦추는 것이다.

## [2026-07-30 저녁] 저전압 경고가 여기로 왔다

예전엔 `bringup.py` 가 `battery/voltage` 를 구독해 5초마다 `LOW BATTERY WARNING` 을 찍었다.
지웠다. 이유 셋:
  · 임계 6.8V 가 PinkyPro 보일러플레이트 유산인데, 이 팩의 **실측 전 구간이 6.8V 이하**라
    (5.88~6.78V) **항상 참**이었다 — 늘 켜져 있는 경고는 경고가 아니다
  · 5초마다 찍혀서 진짜 경고(`cmd_vel` 워치독)를 로그에서 묻었다
  · 전압을 소유한 곳은 여기다. 판단도 여기 있어야 한다

지금은 **퍼센트 기준**으로 여기서 낸다(전압 임계는 `pinky_battery.empty_voltage` 하나뿐).
`throttle_duration_sec` 로 1분에 한 번만 찍는다.

`window` 는 파라미터다. 실물에서 맞춘다.
필터가 아직 표본을 못 채웠거나 I2C 가 실패하면 **아무것도 발행하지 않는다** — 모르는 값을
0 으로 내보내면 관제가 "배터리 없음"으로 읽는다.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

from .pinky_battery import Battery
from .voltage_filter import VoltageFilter


class BatteryPublisher(Node):
    def __init__(self):
        super().__init__('battery_publisher')

        self.battery = Battery()

        self.percentage_publisher = self.create_publisher(
            Float32,
            'battery/percent',
            10
        )

        self.voltage_publisher = self.create_publisher(
            Float32,
            'battery/voltage',
            10
        )

        self.timer_period = float(
            self.declare_parameter('publish_period_sec', 5.0).value)
        # 창 크기가 아니라 **k = 몇 표본까지 지속되는 강하를 순간으로 볼 것인가** 를 준다.
        # 창은 `2k+1` 로 유도된다 — 근거는 voltage_filter.py 머리말.
        # k=3, 5초 주기 → 창 7 = 35초. 모터 가속 강하가 15초까지 이어져도 버린다.
        reject_run = int(self.declare_parameter('reject_run', 3).value)
        deadband = float(self.declare_parameter('voltage_deadband', 0.03).value)
        #: 이 퍼센트 이하면 경고한다. FMS 의 자동 복귀 임계와 같은 값이어야 한다
        #  (aba_fms_service/backend/app/fsm_model.py:104 `battery <= 15%`).
        #  다르면 "경고는 안 떴는데 로봇이 복귀한다" 가 된다.
        self._low_percent = float(self.declare_parameter('low_percent', 15.0).value)
        self._filter = VoltageFilter(reject_run=reject_run, deadband=deadband)
        self.get_logger().info(
            f"battery_publisher — {self.timer_period}s 주기, 중앙값 창 "
            f"{self._filter.window}개(=2×{reject_run}+1, "
            f"≈{self.timer_period * self._filter.window:.0f}초), "
            f"사표대 {deadband}V, 저전압 경고 {self._low_percent}%")

        # ⚠️ 타이머 **하나**다. 둘로 두면 전압과 퍼센트가 다른 표본에서 나온다(위 주석 1번).
        self.timer = self.create_timer(self.timer_period, self.publish_callback)

    def publish_callback(self):
        raw = self.battery.get_voltage()          # 사이클당 I2C 읽기 1회
        if raw is None:
            self.get_logger().warning("배터리 I2C 읽기 실패 — 이번 주기는 건너뛴다")
        voltage = self._filter.push(raw)
        if voltage is None:
            return                                # 아직 유효 표본이 없다 — 지어내지 않는다

        percent = self.battery.battery_percentage(voltage)
        if percent is None:
            return

        self.voltage_publisher.publish(Float32(data=float(voltage)))
        self.percentage_publisher.publish(Float32(data=float(percent)))

        if percent <= self._low_percent:
            # 1분에 한 번만. 5초마다 찍으면 진짜 경고(cmd_vel 워치독 등)가 로그에 묻힌다 —
            # 그게 bringup 의 옛 저전압 경고를 지운 이유다(머리말 참고).
            self.get_logger().warning(
                f"저전압 {percent:.1f}% ({voltage:.2f}V) — {self._low_percent:.0f}% 이하면 "
                f"FMS 가 자동 복귀를 건다",
                throttle_duration_sec=60.0)


def main(args=None):
    rclpy.init(args=args)

    publisher = BatteryPublisher()

    try:
        rclpy.spin(publisher)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
