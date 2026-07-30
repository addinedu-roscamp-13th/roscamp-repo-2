"""원본 /scan 전방 섹터의 최소거리 — 유일한 근접 보호다.

필터된 스캔(scan_filtered)을 쓰지 않는 이유: 그쪽은 min_range 0.05 로 5cm 미만을
제거한다. 근접 감시에는 그 구간이 필요하다.
"""
import math
import time

from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


class ScanWatch:
    """.front_m 전방 최소거리(관측 전 None) · .ready 첫 수신 여부 · .age() 마지막 수신 후 경과.

    .ready 가 필요한 이유: front_m 은 관측 전엔 None 인데, 상태기계는 front_m=None 을
    '근접 정보 없음'으로 취급한다. 그래서 /scan 이 아직 안 들어온 상태를 front_m 만으로는
    구분할 수 없고, 그 상태에서 움직이면 근접 안전장치가 꺼진 채로 도는 셈이 된다.

    .age() 가 따로 필요한 이유: ready 는 '한 번 받았다'일 뿐이다. 라이다가 죽으면
    마지막 값이 영원히 남아, 보호가 켜진 것처럼 보이는 채로 실제로는 꺼진다.
    """

    def __init__(self, node, topic: str = "/scan", half_angle_deg: float = 15.0,
                 forward_deg: float = 0.0):
        self.front_m = None
        self.ready = False
        self.valid = False          # 마지막 스캔에 쓸 만한 관측이 하나라도 있었나
        self.frame_id = None        # 스캔이 어느 좌표계인지 — 전방 가정 확인용
        self._last_t = None
        self._half = math.radians(half_angle_deg)
        self._forward = math.radians(forward_deg)
        node.create_subscription(LaserScan, topic, self._on_scan, qos_profile_sensor_data)

    def age(self) -> float:
        """마지막 스캔이 들어온 뒤 흐른 시간(초). 한 번도 없으면 무한대.

        '한 번 받았다'와 '지금 살아 있다'는 다른 말이다. 라이다가 죽거나 DDS 가
        끊기면 마지막 거리값이 영원히 남아, 근접 보호가 켜진 것처럼 보인 채로 꺼진다.
        """
        return float("inf") if self._last_t is None else time.monotonic() - self._last_t

    def _on_scan(self, msg) -> None:
        best = None
        seen_valid = False
        for i, r in enumerate(msg.ranges):
            # LaserScan 규약:
            #   range_min~range_max 안의 유한값 = 실제 측정
            #   +inf                          = 그 범위 안에 아무것도 없음(정상)
            #   NaN, 범위 밖 유한값            = 무효
            # inf 를 무효로 세면 탁 트인 공간을 라이다 고장으로 오판한다.
            if math.isinf(r) and r > 0:
                seen_valid = True
                continue
            if not math.isfinite(r) or r < msg.range_min or r > msg.range_max:
                continue
            seen_valid = True
            a = msg.angle_min + i * msg.angle_increment - self._forward
            a = math.atan2(math.sin(a), math.cos(a))
            if abs(a) <= self._half and (best is None or r < best):
                best = r
        # front_m=None 은 두 가지 뜻이 될 수 있다: (a) 전방이 트여 있다(정상),
        # (b) 라이다가 쓰레기만 뿜는다(고장). valid 로 그 둘을 갈라 준다.
        self.valid = seen_valid
        self.frame_id = getattr(getattr(msg, "header", None), "frame_id", None)
        self.front_m = best
        self._last_t = time.monotonic()
        self.ready = True
