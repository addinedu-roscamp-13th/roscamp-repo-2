"""FSM 상태 토픽을 구독해 LED 색상·패턴을 출력하는 노드.

이 파일은 얇은 껍데기다 — 패턴 계산은 patterns.py, 매핑은 state_led_config.py,
상태·타임아웃 판정은 led_state_model.py 가 맡으며 셋 다 ROS·하드웨어 없이 테스트된다.
여기서는 rclpy 배선과 실제 LED 쓰기만 한다.

## CPU 를 쓰지 않는 방식 (핵심)

예전엔 **20 Hz 타이머가 항상 돌았다.** 상태가 안 바뀌어도 초당 20번 프레임을 계산하고
직전 프레임과 비교했다. LED 는 대부분의 시간을 solid 로 보내는데, 그 동안 이 노드는
아무 일도 안 하면서 계속 깨어났다. 같은 Pi 에서 nav2 와 YOLO 가 도는 걸 생각하면
공짜가 아니다.

지금은 **사건에 반응만 한다**:

    solid 상태  →  타이머를 아예 만들지 않는다. 한 번 쓰고 끝. 평상시 CPU 점유 0.
    blink 상태  →  그 상태에 있는 동안만 period/2 주기 타이머. 나가면 destroy.

깜빡임은 ON/OFF 두 프레임뿐이라 매 tick 계산할 것도 없다 — 타이머가 토글만 한다.

타임아웃(상태 토픽 두절) 감시도 상시 타이머가 아니라, 상태를 받을 때마다 **하나짜리
타이머를 다시 거는** 방식이다. 토픽이 살아 있으면 그 타이머는 영원히 발화하지 않는다.

주의 1 (블로킹 금지): pinkyled.LED 의 color_wipe / theater_chase / rainbow / rainbowCycle /
theaterChaseRainbow 는 내부에서 time.sleep() 루프를 돈다. 콜백에서 호출하면 rclpy.spin()
전체가 멈춰 다른 콜백이 하나도 실행되지 않는다. 절대 쓰지 않는다.

주의 2 (LED 점유): rpi_ws281x 는 한 프로세스만 스트립을 소유할 수 있다. 같은 패키지의
led_server 와 이 노드를 동시에 띄울 수 없다. 둘 중 하나만 실행할 것 (README 참조).

주의 3 (root): pinkyled 모듈은 import 시점에 root 가 아니면 sudo 로 자기 자신을 재실행한다.
따라서 이 노드도 결국 root 권한으로 동작하며, 개발 PC 에서는 import 자체가 불가능하다.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from pinky_led.led_state_model import LedStateModel
from pinky_led.pinkyled import LED
from pinky_led.state_led_config import load


class StateLedNode(Node):
    def __init__(self):
        super().__init__("state_led")
        self.declare_parameter("config_path", "")
        self.declare_parameter("state_topic", "fsm_state")

        config_path = self.get_parameter("config_path").value
        if not config_path:
            raise RuntimeError("config_path parameter is required (path to led_state_map.yaml)")

        self.config = load(config_path)
        self.model = LedStateModel(self.config)
        self.led = LED(num=self.config.num_pixels)

        self._blink_timer = None      # 깜빡이는 동안만 존재한다
        self._timeout_timer = None    # 상태 수신 때마다 다시 걸린다
        self._blink_on = True
        self._shown = None            # 마지막으로 쓴 (상태, on/off)

        topic = self.get_parameter("state_topic").value
        self.create_subscription(String, topic, self._on_state, 10)
        self._arm_timeout()
        self._apply()                 # 부팅 직후에도 한 번은 그린다 (검은 스트립 방지)
        self.get_logger().info(
            f"state_led ready — topic '{topic}' (렌더 루프 없음: solid 는 타이머 0개)"
        )

    # ── 상태 수신 ────────────────────────────────────────────────────────────

    def _now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def _on_state(self, msg):
        before = self.model.active_state
        self.model.on_state(msg.data.strip().upper(), self._now())
        self._arm_timeout()
        # 같은 상태를 20 Hz 로 재발행해도 다시 그리지 않는다 — 바뀔 때만 일한다.
        if self.model.active_state != before:
            self._apply()

    # ── 출력 ─────────────────────────────────────────────────────────────────

    def _apply(self):
        """현재 상태에 맞춰 타이머를 다시 잡고 한 프레임 쓴다."""
        style = self.model.current_style()
        self._cancel_blink()
        self._blink_on = True          # 상태 진입은 항상 ON 부터 (위상 초기화)
        self._write(style, on=True)
        if style.blinks:
            # duty 50% — period 의 절반마다 토글하면 ON/OFF 가 같은 길이가 된다.
            self._blink_timer = self.create_timer(style.period_sec / 2.0, self._toggle)

    def _toggle(self):
        self._blink_on = not self._blink_on
        self._write(self.model.current_style(), on=self._blink_on)

    def _write(self, style, *, on):
        key = (style.state, on)
        if key == self._shown:
            return                     # 같은 그림을 두 번 쓰지 않는다 (SPI 트래픽도 비용이다)
        frame = self.model.frame(style, on=on)
        for index, color in enumerate(frame):
            self.led.set_pixel(index, color)
        self.led.show()
        self._shown = key

    def _cancel_blink(self):
        if self._blink_timer is not None:
            self.destroy_timer(self._blink_timer)   # 이탈 시 반드시 없앤다
            self._blink_timer = None

    # ── 상태 두절 감시 ───────────────────────────────────────────────────────

    def _arm_timeout(self):
        """상태를 받을 때마다 타임아웃 타이머를 다시 건다.

        상시 감시 타이머를 두지 않는 이유: 토픽이 살아 있는 동안 그 타이머는 매번
        "아직 안 끊겼네"만 확인하고 돌아간다. 정상 상황에서 100% 헛돈다.
        """
        if self._timeout_timer is not None:
            self.destroy_timer(self._timeout_timer)
        self._timeout_timer = self.create_timer(
            self.config.state_timeout_sec, self._on_timeout
        )

    def _on_timeout(self):
        if self.model.mark_stale():
            self.get_logger().warn(
                f"상태 토픽 {self.config.state_timeout_sec:.1f}초 두절 — 두절 표시로 전환"
            )
            self._apply()


def main(args=None):
    rclpy.init(args=args)
    node = StateLedNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.led.clear()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
