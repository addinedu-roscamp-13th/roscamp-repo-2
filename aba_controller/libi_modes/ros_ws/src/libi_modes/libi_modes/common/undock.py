"""도킹 자세에서 빠져나온다. **nav2 가 경로를 낼 수 있는 곳까지만** 민다.

## 왜 필요한가 — 물리가 아니라 지도상 판정이다

`pinky_navigation/params/nav2_params.yaml`:

    robot_radius 0.06 + footprint_padding 0.03 = inscribed 0.088
    inflation_radius 0.09     (0.088 미만이면 nav2 가 SIGABRT 로 죽는다 — 그 파일 주석에
                               실측 기록이 있다)
    → 벽에서 9cm 안쪽은 전부 cost 253(통행불가). 그 밖은 0. 감쇠 구간이 사실상 없다.

복귀가 끝나면 로봇 **끝단**이 벽에서 3cm 다(사용자 설계 — USB 커넥터 돌출까지 더하면
딱 1cm 여유). 그러면 **중심**은 3 + robot_radius(6) = **9cm**, 정확히 그 경계다.
AMCL 오차(±2~3cm)와 격자(2cm)가 얹히면 대부분 안쪽으로 떨어지고, 그러면 global planner 가
**시작 격자를 통행불가로 보고 경로를 아예 못 만든다.**

로봇이 물리적으로 못 나가는 게 아니다. 그러니 **nav2 를 안 쓰고 먼저 빠져나오면** 사라진다.
6cm 밀면 중심이 15cm 가 되어 inflation 밖이다.

## ⚠️ 놓을 자리 제약 — 잠긴 상태에서는 안 움직인다

`cmd_vel_dock` 은 twist_mux priority **120**, FSM 잠금은 **150** 이다.
`MOTION_LOCKED_STATES = {IDLE, INTERACTING, ERROR, CHARGING}`(`ros/state_io.py`) 이므로
**CHARGING 이나 IDLE 에 이 leaf 를 넣으면 조용히 아무 일도 안 일어난다.**
잠기지 않은 브랜치(PATROL·WORKING·SECURITY_PATROL)의 **주행을 내기 전**이어야 한다.

## ⚠️ 시간만 재면 안 된다 — 실제로 갔는지를 본다

`NudgeDriver` 는 시간 기반이라 바퀴가 헛돌아도 "6cm 갔다"고 성공한다. 그러면 곧바로
nav2 planner 실패가 재현되는데, **원인은 여기 있고 증상은 저기서 난다** — 가장 찾기
어려운 모양이다. 그래서 이 leaf 는 `robot_pose` 로 실제 이동량을 확인한다.
(직진 구간이라 시작점 대비 직선거리로 충분하다. 옆으로 미끄러질 일이 없다.)
"""
import math

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class UndockNotNeeded(py_trees.behaviour.Behaviour):
    """밀 필요가 없으면 SUCCESS — 평소에는 이 한 줄로 끝난다.

    `Selector` 의 첫 자식이라, 참이면 `Undock` 이 아예 안 돈다.

    ## ⚠️ `is_docked` 만 보면 안 된다 — 원샷 래치가 필요하다

    `is_docked` 는 주차장 정점 **반경 0.12m** 판정이다(`dock_confirm.py`). 6cm 밀고 나와도
    여전히 참이라, 브랜치 루트가 `memory=False` 인 이 자리에서는 **매 tick 다시 6cm 를
    민다** — 순회를 시작하기도 전에 로봇이 계속 앞으로 간다.

    그렇다고 반경을 벗어나는 것으로 판정하면 반대 실패가 난다: AMCL 오차로 먼저 거짓이
    되면 충전소를 충분히 못 벗어난 채 건너뛰고, 그러면 nav2 가 경로를 못 낸다.

    그래서 위치가 아니라 **사실**을 기억한다.

    ## ⚠️ 래치를 여기서 풀지 않는다 — 인스턴스가 셋이다

    처음엔 이 leaf 가 `is_docked` 의 거짓→참 전이를 보고 래치를 풀었다. 틀렸다:
    게이트는 **주행 브랜치마다 별개 인스턴스**다(`registry._undock`, py_trees 노드는
    부모를 하나만 갖는다). PATROL→WORKING 으로 옮기면 그쪽 인스턴스는 `_was_docked` 가
    False 인 채로 시작해 **멀쩡한 래치를 지우고 또 6cm 를 민다.**

    래치는 도킹이 **실제로 끝나는 한 곳**에서만 푼다 — `DockSettle`
    (`common/return_steps.py`). 여기는 읽기만 한다.
    """

    def __init__(self, name: str = "UndockNotNeeded"):
        super().__init__(name=name)

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.IS_DOCKED, access=Access.READ)
        self.blackboard.register_key(key=Keys.UNDOCK_DONE, access=Access.READ)

    def update(self):
        if not bb.get(self.blackboard, Keys.IS_DOCKED):
            return Status.SUCCESS
        return Status.SUCCESS if bb.get(self.blackboard, Keys.UNDOCK_DONE) else Status.FAILURE


class Undock(py_trees.behaviour.Behaviour):
    """`distance_m` 만큼 앞으로 밀고, **실제로 갔는지 pose 로 확인한다.**

    드라이버는 `cmd_vel_dock` 으로 정속을 내는 `NudgeDriver`(속도 부호 양수)다.
    시간이 다 되어도 이동량이 모자라면 FAILURE — 감싼 `AbsorbFailure` 가 재시도하고,
    소진되면 fault → ERROR 로 간다. 조용히 성공하는 것보다 낫다.
    """

    def __init__(self, driver, distance_m: float, timeout_sec: float, now_fn,
                 name: str = "Undock"):
        super().__init__(name=name)
        self.driver = driver
        self.distance_m = float(distance_m)
        self.timeout_sec = float(timeout_sec)
        self._now = now_fn
        self._started_at = None
        self._origin = None
        self._sent = False

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ROBOT_POSE, access=Access.READ)
        self.blackboard.register_key(key=Keys.UNDOCK_DONE, access=Access.WRITE)

    def initialise(self):
        self._started_at = None
        self._origin = None
        self._sent = False

    def _moved(self) -> float | None:
        pose = bb.get(self.blackboard, Keys.ROBOT_POSE)
        if pose is None:
            return None
        if self._origin is None:
            self._origin = (pose["x"], pose["y"])
            return 0.0
        return math.hypot(pose["x"] - self._origin[0], pose["y"] - self._origin[1])

    def update(self):
        now = self._now()
        if self._started_at is None:
            self._started_at = now
        moved = self._moved()
        if moved is not None and moved >= self.distance_m:
            self.blackboard.set(Keys.UNDOCK_DONE, True)
            return Status.SUCCESS
        if not self._sent:
            self.driver.start()
            self._sent = True
        elif self.driver.poll() == "failure":
            return Status.FAILURE
        if now - self._started_at >= self.timeout_sec:
            # 명령은 냈는데 안 갔다. 바퀴 헛돎이거나 무언가에 걸린 것이다.
            # 여기서 성공으로 넘기면 nav2 가 "경로 없음"으로 실패하고, 그때는 원인이
            # 안 보인다.
            return Status.FAILURE
        return Status.RUNNING

    def terminate(self, new_status):
        if self._sent and new_status != Status.SUCCESS:
            self.driver.stop()
        self._sent = False


def create(driver, *, distance_m, timeout_sec, retry_max, now_fn,
           name: str = "UndockOrSkip") -> py_trees.behaviour.Behaviour:
    """`NotDocked` 로 감싼 undock 게이트. 주행 브랜치의 **주행 앞**에 둔다."""
    from libi_modes.common.return_steps import AbsorbFailure

    return py_trees.composites.Selector(
        name=name,
        memory=False,
        children=[
            UndockNotNeeded(),
            AbsorbFailure(Undock(driver, distance_m, timeout_sec, now_fn), retry_max),
        ],
    )
