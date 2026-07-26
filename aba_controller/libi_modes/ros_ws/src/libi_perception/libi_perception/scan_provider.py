import math

from sensor_msgs.msg import LaserScan
from rclpy.qos import qos_profile_sensor_data


def to_degree_indexed(msg):
    """LaserScan → 360칸 리스트. index i = 정면 기준 i도(반시계 +). 값 0.0 = 데이터 없음.

    `lidar_avoidance.apply_avoidance` 는 "index 0 이 정면, 한 칸이 1도"를 가정한다.
    LaserScan 은 그런 보장이 없다 — sllidar A1 은 `angle_min = -pi` 라 index 0 이 **후방**이다.
    그대로 넘기면 전방 감속 구간이 뒤를 보고, 좌우 회피가 반대로 걸린다.

    변환은 각도 메타데이터(`angle_min`/`angle_increment`)가 실제로 존재하는 여기서 한 번만
    한다. 아래로 내려보내면 모든 소비자가 같은 실수를 반복할 자리가 생긴다.
    """
    out = [0.0] * 360
    if not msg.ranges or msg.angle_increment == 0.0:
        return out
    for i, r in enumerate(msg.ranges):
        if not math.isfinite(r) or r <= 0.0:
            continue                      # inf/nan/0 = 측정 실패. 0.0(데이터 없음)으로 남긴다.
        deg = int(round(math.degrees(msg.angle_min + i * msg.angle_increment))) % 360
        # 한 칸에 여러 샘플이 겹치면 가까운 쪽을 남긴다 — 회피는 보수적인 쪽이 옳다.
        if out[deg] == 0.0 or r < out[deg]:
            out[deg] = r
    return out


class ScanProvider:
    """Caches the latest /scan as a 360-entry, degree-indexed list (front = index 0)."""

    def __init__(self, node, topic):
        self._ranges = []
        node.create_subscription(LaserScan, topic, self._cb,
                                 qos_profile_sensor_data)

    def _cb(self, msg):
        self._ranges = to_degree_indexed(msg)

    def get(self):
        return self._ranges
