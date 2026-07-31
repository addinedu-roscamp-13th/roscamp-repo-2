"""도킹 자세 탈출 — 게이트와 이동 검증.

벽에서 9cm 안쪽은 nav2 costmap 이 통행불가(253)라 planner 가 시작 격자에서 경로를
못 낸다. 도킹이 끝나면 로봇 중심이 정확히 그 경계(9cm)에 선다. 그래서 주행하는
브랜치는 **주행을 내기 전에** 밀고 나와야 한다.

여기서 지키는 것 셋:

  ① 평소(도킹 아님)에는 **아무 일도 안 한다** — 순회할 때마다 앞으로 밀면 안 된다
  ② 한 번 나오면 다시 안 민다 — `is_docked` 는 반경 0.12m 판정이라 나온 뒤에도 참이다
  ③ **실제로 갔는지 본다** — 시간만 재면 바퀴가 헛돌아도 성공이 되고, 그러면 증상이
     한참 뒤 nav2 "경로 없음"으로 나타나 원인을 못 찾는다
"""
import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.undock import Undock, UndockNotNeeded, create

from test.fakes import FakeDriver


class _Clock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t


def _at(x, y=0.0):
    return {"x": x, "y": y, "yaw": 0.0}


# ── 게이트 ──────────────────────────────────────────────────────────────────

def test_not_docked_passes_through(seed, tick):
    """평소다. 순회·배달마다 로봇이 앞으로 밀리면 안 된다."""
    seed(**{Keys.IS_DOCKED: False})
    assert tick(UndockNotNeeded()) == Status.SUCCESS


def test_docked_needs_undock(seed, tick):
    seed(**{Keys.IS_DOCKED: True})
    assert tick(UndockNotNeeded()) == Status.FAILURE


def test_latch_stops_a_second_push(seed, tick):
    """`is_docked` 는 주차장 정점 **반경 0.12m** 판정이라 6cm 나와도 여전히 참이다.

    브랜치 루트가 `memory=False` 라, 래치가 없으면 매 tick 다시 6cm 를 민다 —
    순회를 시작하기도 전에 로봇이 계속 앞으로 간다.
    """
    seed(**{Keys.IS_DOCKED: True, Keys.UNDOCK_DONE: True})
    assert tick(UndockNotNeeded()) == Status.SUCCESS


def test_latch_is_cleared_by_dock_settle_not_here(seed, tick):
    """래치 해제는 **도킹이 끝나는 곳**(`DockSettle`)의 일이다.

    게이트는 주행 브랜치마다 별개 인스턴스라(py_trees 노드는 부모를 하나만 갖는다),
    여기서 전이를 보고 풀면 PATROL→WORKING 으로 옮길 때 그쪽 인스턴스가 멀쩡한
    래치를 지우고 또 민다. 그래서 이 leaf 는 **읽기만** 한다.
    """
    from libi_modes.common.return_steps import DockSettle

    seed(**{Keys.IS_DOCKED: True, Keys.UNDOCK_DONE: True})
    assert tick(UndockNotNeeded()) == Status.SUCCESS, "읽기만 — 래치를 안 건드린다"

    clock = _Clock()
    settle = DockSettle(1.0, clock)
    settle.setup()
    settle.initialise()
    settle.tick_once()
    clock.t = 1.5
    settle.tick_once()
    assert settle.status == Status.SUCCESS
    assert tick(UndockNotNeeded()) == Status.FAILURE, "새로 도킹했으니 다시 나와야 한다"


def test_gate_never_touches_the_driver_when_not_docked(seed, tick):
    """게이트가 도킹 여부를 안 보면 평소 주행마다 명령이 나간다."""
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: False})
    assert tick(create(driver, distance_m=0.06, timeout_sec=8.0,
                       retry_max=3, now_fn=_Clock())) == Status.SUCCESS
    assert driver.started is False


# ── 이동 ────────────────────────────────────────────────────────────────────

def test_pushes_and_waits(seed, tick):
    """명령은 즉시 내되, 갔다고 치지는 않는다."""
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    assert tick(Undock(driver, 0.06, 8.0, _Clock())) == Status.RUNNING
    assert driver.started is True


def test_succeeds_on_real_movement(seed, read):
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(driver, 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()                                        # 기준점 기록 + 명령
    py_trees.blackboard.Blackboard.set(Keys.ROBOT_POSE, _at(0.07))
    node.tick_once()
    assert node.status == Status.SUCCESS
    assert read(Keys.UNDOCK_DONE) is True, "래치를 세워야 두 번 안 민다"
    assert read(Keys.DOCK_DECLARED) is False, \
        "선언을 안 내리면 다음 복귀에서 AlreadyDocked 가 낡은 True 를 보고 주행을 건너뛴다"


def test_partial_movement_is_not_enough(seed):
    """5cm 만 가면 중심이 14cm — 겨우 벗어난다. 목표를 채울 때까지 계속 민다."""
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(driver, 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()
    py_trees.blackboard.Blackboard.set(Keys.ROBOT_POSE, _at(0.03))
    node.tick_once()
    assert node.status == Status.RUNNING


def test_timeout_without_movement_fails(seed, read):
    """**바퀴가 헛돌았다.** 여기서 성공으로 넘기면 nav2 가 "경로 없음"으로 실패하고,
    그때는 원인이 도킹에서 멀리 떨어져 있어 안 보인다."""
    driver = FakeDriver()
    clock = _Clock()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(driver, 0.06, 8.0, clock)
    node.setup()
    node.initialise()
    node.tick_once()
    clock.t = 9.0
    node.tick_once()
    assert node.status == Status.FAILURE
    assert read(Keys.UNDOCK_DONE) is not True, "안 갔는데 래치를 세우면 영영 안 민다"


def test_driver_rejection_fails_fast(seed):
    """실행 층이 거부했다(링크 끊김 등). timeout 까지 기다릴 이유가 없다."""
    driver = FakeDriver(["failure"])
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(driver, 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()                                        # 명령 발행
    node.tick_once()                                        # poll → failure
    assert node.status == Status.FAILURE


def test_no_pose_does_not_claim_arrival(seed, tick):
    """위치를 모르는 것을 도착으로 치면 안 된다 — `_GoalStep` 과 같은 규칙이다."""
    seed(**{Keys.IS_DOCKED: True})                          # ROBOT_POSE 없음
    assert tick(Undock(FakeDriver(), 0.06, 8.0, _Clock())) == Status.RUNNING


def test_preemption_stops_the_push(seed):
    """상위가 끊었는데 명령이 남으면 로봇이 계속 앞으로 간다."""
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(driver, 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()
    node.stop(Status.INVALID)
    assert driver.stopped is True


def test_success_does_not_stop(seed):
    """정상 종료까지 stop 을 보내면 뒤따르는 주행을 끊을 수 있다."""
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(driver, 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()
    py_trees.blackboard.Blackboard.set(Keys.ROBOT_POSE, _at(0.07))
    node.tick_once()
    node.stop(Status.SUCCESS)
    assert driver.stopped is False


# ── 실패 흡수 ────────────────────────────────────────────────────────────────

def test_failure_never_escapes_the_gate(seed):
    """이 게이트는 브랜치 루트 Sequence 안에 있다. FAILURE 가 새어 나가면 브랜치가
    통째로 죽어 그 상태에서 아무것도 못 한다."""
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    gate = create(FakeDriver(["failure"] * 9), distance_m=0.06, timeout_sec=8.0,
                  retry_max=3, now_fn=_Clock())
    gate.setup_with_descendants()
    gate.initialise()
    for _ in range(6):
        gate.tick_once()
        assert gate.status != Status.FAILURE


def test_exhausted_retries_pass_through_so_the_watchdog_can_see_the_fault(seed, read):
    """**소진되면 SUCCESS 다.** RUNNING 이 아니다 (codex 리뷰 2026-07-30).

    `AbsorbFailure` 는 소진 시 RUNNING 을 유지한다 — 그쪽은 `Parallel` **안**이라
    형제 `FaultDetected` 가 같은 tick 에 fault 를 보기 때문이다. 이 게이트는 주행
    Parallel **앞**이라, RUNNING 을 유지하면 시퀀스가 여기서 막혀 그 Parallel 이
    영영 tick 되지 않는다 — fault 를 세워 놓고도 **ERROR 로 못 간다.**

        바퀴 헛돎 → 재시도 소진 → fault=True → 게이트 RUNNING
          → FaultDetected 가 한 번도 안 돌아 → PATROL 에 멈춘 채 영원히 재시도
    """
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    gate = create(FakeDriver(["failure"] * 9), distance_m=0.06, timeout_sec=8.0,
                  retry_max=3, now_fn=_Clock())
    gate.setup_with_descendants()
    gate.initialise()
    seen = []
    for _ in range(6):
        gate.tick_once()
        seen.append(gate.status)
    assert Status.SUCCESS in seen, "소진 뒤 통과시켜야 watchdog 이 fault 를 본다"
    assert read(Keys.FAULT) is True


def test_sideways_drift_is_not_forward_progress(seed):
    """시작점 대비 **직선거리**를 쓰면 옆으로 밀린 거리와 AMCL 재국소화 점프까지
    전진으로 센다 — 벽에서 안 나왔는데 성공으로 판정하고, 그러면 nav2 가 "경로 없음"
    으로 실패한다. 진행 방향 성분만 세야 맞다(codex 리뷰 2026-07-30)."""
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: _at(0.0)})
    node = Undock(FakeDriver(), 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()                                        # 기준: x=0, yaw=0
    # 옆(y)으로 10cm — 직선거리는 0.10 이라 옛 계산이면 성공했다
    py_trees.blackboard.Blackboard.set(Keys.ROBOT_POSE, {"x": 0.0, "y": 0.10, "yaw": 0.0})
    node.tick_once()
    assert node.status == Status.RUNNING, "옆으로 민 것을 전진으로 세면 안 된다"


# ── 블랙보드 등록 누락 (2026-07-30 실기 사고) ────────────────────────────────

def test_unregistered_key_does_not_kill_the_node(capsys):
    """py_trees 는 등록 안 된 키에 **KeyError 가 아니라 AttributeError** 를 낸다.

    실측(2026-07-30): 복귀 ②단계가 처음 실제로 도는 순간
    `client 'fsm_node' does not have read/write access to '/robot_pose'` 로
    **FSM 노드가 통째로 죽었다.** 그 순간 로봇은 판단 주체를 잃는다.

    죽이지 않되 조용히 넘어가지도 않는다 — 등록 누락은 "아직 값이 없음"이 아니라
    프로그래밍 실수이므로, 키마다 한 번 크게 남긴다.
    """
    import py_trees
    from libi_modes import blackboard as bb

    bb._WARNED.clear()
    client = py_trees.blackboard.Client(name="no_keys_registered")
    assert bb.get(client, Keys.ROBOT_POSE) is None, "노드를 죽이면 안 된다"
    assert "등록 안 된 키" in capsys.readouterr().out


def test_pose_without_yaw_never_claims_forward_progress(seed):
    """`robot_pose` 에 yaw 가 없으면 **전진량을 셀 수 없다** — 갔다고 치면 안 된다.

    실측(2026-07-30): `providers._on_pose` 가 `{x, y}` 만 실어서 복귀 ②(`_YawStep`)가
    "로봇은 다 돌았는데 BT 는 모른 채" 60초 timeout → 재시도 → ERROR 로 갔다.
    `Undock` 의 헤딩 투영도 같은 값을 쓰므로 같은 함정에 빠진다.
    yaw 가 없을 때 **0 을 반환하면 안 되고**(진전 없음으로 오해), RUNNING 이어야 한다.
    """
    driver = FakeDriver()
    seed(**{Keys.IS_DOCKED: True, Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}})   # yaw 없음
    node = Undock(driver, 0.06, 8.0, _Clock())
    node.setup()
    node.initialise()
    node.tick_once()
    assert node.status == Status.RUNNING
