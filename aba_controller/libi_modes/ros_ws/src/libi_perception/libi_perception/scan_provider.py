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
    """Caches the latest /scan as a 360-entry, degree-indexed list (front = index 0).

    ## [2026-07-30] 변환을 콜백에서 게터로 옮겼다 — 추종을 안 할 때 공짜가 된다

    실측: 순회만 도는데 `follow_node` 가 코어의 **14%** 를 쓰고 있었다. 타이머(20Hz)가
    아니라 **여기가 원인**이었다. 예전에는 `_cb` 가 스캔이 올 때마다 곧바로
    `to_degree_indexed` 를 돌렸다 — 그건 광선을 **전부 파이썬 루프로** 도는 함수다.
    sllidar C1 DenseBoost 는 스캔당 1000+ 포인트고 10Hz 라, 세션이 하나도 없어도
    초당 1만 번 넘는 파이썬 반복 + `math.degrees` 를 계속 돌고 있었다.

    소비자는 `ControlLoop` 하나뿐이고 그건 **세션이 있을 때만** 존재한다. 그래서
    콜백은 참조만 잡아 두고(공짜), 변환은 `get()` 이 불릴 때 한다.

      · 세션 없음 → `get()` 을 아무도 안 부른다 → 변환 0회
      · 세션 있음 → 스캔 한 장당 최대 1회 (캐시). 제어 루프가 20Hz 로 물어도
        스캔이 10Hz 면 변환은 10회다 — 예전과 같거나 적다.

    ⚠️ 구독 자체는 그대로 둔다. 세션마다 만들고 없애면 그게 더 위험하다(rclpy 에서
       콜백 안 엔티티 생성·소멸은 실행기 상태를 흔든다). 남는 비용은 DDS 수신과
       역직렬화뿐이고, 그건 C 레벨이라 위 파이썬 루프보다 훨씬 싸다.
    """

    def __init__(self, node, topic):
        self._msg = None
        self._cache = None
        node.create_subscription(LaserScan, topic, self._cb,
                                 qos_profile_sensor_data)

    def _cb(self, msg):
        # 참조만 잡는다. 변환은 소비자가 요구할 때 — 위 주석 참고.
        self._msg = msg
        self._cache = None

    def get(self):
        if self._cache is None and self._msg is not None:
            self._cache = to_degree_indexed(self._msg)
        return self._cache if self._cache is not None else []
