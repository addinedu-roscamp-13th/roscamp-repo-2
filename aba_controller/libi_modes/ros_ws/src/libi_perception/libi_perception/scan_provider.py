import math
import time

from sensor_msgs.msg import LaserScan
from rclpy.qos import qos_profile_sensor_data


def to_degree_indexed(msg, flip_180: bool = False):
    """LaserScan → 360칸 리스트. index i = 정면 기준 i도(반시계 +). 값 0.0 = 데이터 없음.

    `lidar_avoidance.apply_avoidance` 는 "index 0 이 정면, 한 칸이 1도"를 가정한다.
    LaserScan 은 그런 보장이 없다 — sllidar A1 은 `angle_min = -pi` 라 index 0 이 **후방**이다.
    그대로 넘기면 전방 감속 구간이 뒤를 보고, 좌우 회피가 반대로 걸린다.

    변환은 각도 메타데이터(`angle_min`/`angle_increment`)가 실제로 존재하는 여기서 한 번만
    한다. 아래로 내려보내면 모든 소비자가 같은 실수를 반복할 자리가 생긴다.

    ## ⚠️ [2026-08-02] `flip_180` — 라이다가 **거꾸로 장착**돼 있다

    실측: 로봇 **뒤**를 손으로 막았는데 **앞으로 못 갔다.** 즉 드라이버가 내보내는
    0° 가 로봇의 뒤를 가리킨다. 하드웨어 장착 방향이라 코드로만 맞출 수 있다.

    참조 구현(`arte_libi_perception/follower_perception/scripts/lidar_avoid.py`)도
    같은 이유로 `flip_180` 을 갖고 있다. 거기서는 섹터를 각각 180° 반대편과 맞바꾸는데,
    여기서는 **각도에 180 을 더하는 것**으로 같은 일을 한 번에 한다 — 섹터를 나누기
    **전**에 좌표계를 바로잡는 편이 나중에 아크를 바꿔도 안 어긋난다.

    앞↔뒤와 좌↔우가 **동시에** 뒤집히는 것이 맞다. 180° 회전은 두 축을 같이 뒤집는다.
    """
    out = [0.0] * 360
    if not msg.ranges or msg.angle_increment == 0.0:
        return out
    offset = 180 if flip_180 else 0
    for i, r in enumerate(msg.ranges):
        if not math.isfinite(r) or r <= 0.0:
            continue                      # inf/nan/0 = 측정 실패. 0.0(데이터 없음)으로 남긴다.
        deg = int(round(math.degrees(msg.angle_min + i * msg.angle_increment)) + offset) % 360
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

    ## [2026-07-31] 오래된 스캔은 없는 것으로 돌려준다

    예전에는 마지막으로 받은 스캔을 **영원히** 최신인 것처럼 돌려줬다. 라이다가
    멈춰도 소비자는 그 사실을 알 방법이 없어, 몇 분 전 그림을 보고 회피 판단을 했다.
    `max_age` 를 넘기면 빈 리스트를 돌려준다 — 그러면 `apply_avoidance` 가 전진을
    막는다. 시간은 **수신 시각**(monotonic)으로 잰다. 메시지 stamp 가 아니라 그것이
    "센서가 지금도 말하고 있나"에 답하는 값이다.
    """

    def __init__(self, node, topic, max_age=0.0, now=time.monotonic, flip_180=False):
        self._msg = None
        self._cache = None
        self._at = None
        self._max_age = float(max_age)
        self._now = now
        #: 라이다가 거꾸로 달려 있으면 True — `to_degree_indexed` 머리말 참고.
        self._flip_180 = bool(flip_180)
        self._stale = False               # 경고를 상태 전이에서 한 번만 찍으려고
        get_logger = getattr(node, 'get_logger', None)
        self._log = get_logger() if get_logger is not None else None
        node.create_subscription(LaserScan, topic, self._cb,
                                 qos_profile_sensor_data)

    def _cb(self, msg):
        # 참조만 잡는다. 변환은 소비자가 요구할 때 — 위 주석 참고.
        self._msg = msg
        self._cache = None
        self._at = self._now()
        if self._stale:
            self._stale = False
            if self._log is not None:
                self._log.info('LiDAR 스캔이 돌아왔습니다 — 전진 차단을 풉니다')

    def _is_stale(self):
        if self._max_age <= 0 or self._at is None:
            return False                  # 검사 꺼짐, 또는 아직 한 장도 안 받음
        return (self._now() - self._at) > self._max_age

    def get(self):
        if self._is_stale():
            # 조용히 빈 값을 주면 "왜 안 가지"의 원인이 안 남는다. 상태가 바뀔 때
            # 한 번만 찍는다 — 20Hz 로 부르는 자리라 매번 찍으면 로그가 묻힌다.
            if not self._stale:
                self._stale = True
                if self._log is not None:
                    self._log.warning(
                        f'LiDAR 스캔이 {self._max_age}s 넘게 안 옵니다 — '
                        f'전방을 못 보므로 전진을 막습니다')
            return []
        if self._cache is None and self._msg is not None:
            self._cache = to_degree_indexed(self._msg, self._flip_180)
        return self._cache if self._cache is not None else []
