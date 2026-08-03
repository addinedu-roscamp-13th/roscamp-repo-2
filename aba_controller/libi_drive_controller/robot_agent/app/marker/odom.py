"""/odom 에서 누적 방위각, 누적 경로 길이, 최신 위치를 뽑는다.

시간 기반 회전 추정을 쓰지 않는 이유: 같은 시간을 돌아도 바닥 재질과 배터리 잔량에
따라 회전량이 달라진다. 20° 스텝을 6번 쌓으면 그 오차가 누적돼 스윕이 원점으로
돌아오지 않는다.
"""
import math
import time

from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data


def _yaw_from_quat(q) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


class OdomTracker:
    """.yaw_deg 누적(언랩, 도, 좌회전 +) · .forward_m 헤딩에 투영한 부호 있는 전진
    거리(m) · .ready 첫 /odom 수신 여부.

    거리 판정에 누적 경로 길이나 시작점 대비 직선거리를 쓰지 않는 이유: 전자는 제자리
    진동으로도 늘고, 후자는 **옆으로 밀린 거리**까지 진행으로 센다. 앞으로 간 만큼만
    세야 벽 앞 10cm 가 맞는다.

    .ready 는 `drive` 모드가 근접 안전장치(scan) 없이 움직이기 시작하는 것을 막는 데
    쓰인다 — 첫 메시지가 오기 전엔 yaw_deg/forward_m 이 0 인 채로 '정상'
    처럼 보이기 때문에, 값만 보고는 아직 수신 전인지 구분할 수 없다.
    """

    def __init__(self, node, topic: str = "/odom"):
        self.yaw_deg = 0.0
        self.forward_m = 0.0
        self.ready = False
        self._last_t = None
        self._prev_yaw = None
        self._prev_xy = None
        node.create_subscription(Odometry, topic, self._on_odom, qos_profile_sensor_data)

    def age(self) -> float:
        """마지막 odom 이 들어온 뒤 흐른 시간(초). 한 번도 없으면 무한대."""
        return float("inf") if self._last_t is None else time.monotonic() - self._last_t

    def _on_odom(self, msg) -> None:
        p = msg.pose.pose.position
        yaw = _yaw_from_quat(msg.pose.pose.orientation)
        if self._prev_yaw is not None:
            d = yaw - self._prev_yaw
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            self.yaw_deg += math.degrees(d)
        self._prev_yaw = yaw
        if self._prev_xy is not None:
            dx, dy = p.x - self._prev_xy[0], p.y - self._prev_xy[1]
            # 현재 헤딩에 투영 → 옆으로 밀린 성분은 빠지고, 뒤로 간 만큼은 차감된다.
            self.forward_m += dx * math.cos(yaw) + dy * math.sin(yaw)
        self._prev_xy = (p.x, p.y)
        self._last_t = time.monotonic()
        self.ready = True
