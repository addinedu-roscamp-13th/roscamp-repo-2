"""주행은 **도착**에서 끝난다 — 명령 접수에서 끝나지 않는다.

## 왜 이 파일이 생겼나

`NavigationExec` 은 드라이버 결과(`/fleet_cmd_result`)로 끝났다. 그런데 그 결과는
`ros_bridge.send_nav_goal()` 이 **완료를 안 기다리고** 돌려주는 접수 응답이다.
20초짜리 주행이 BT 상으로는 0.2초 만에 끝난 일이 됐고, 그래서:

- 관제 화면이 주행 내내 `AwaitingCommand` 를 보여줬다 (실측)
- `CommandTimeout` 이 실제 주행이 아니라 접수 왕복만 지켰다
- **도착 없이 끝난 주행을 아무도 못 알아챘다** — 로봇이 6분 40초를 서 있었다 (실측)

`ReturnNavigation` 이 도킹에서 이미 푼 문제와 같다: "명령 접수됨"을 "도착함"으로
믿지 않는다. 도킹은 `is_docked`, 주행은 `robot_pose` 를 근거로 삼는다.
"""
import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.working_actions import NavigationExec

from .fakes import FakeDriver

TOLERANCE = 0.05
RESEND = 10.0
TIMEOUT = 60.0


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def leaf(seed):
    def _make(poll_sequence=("running",), *, clock=None, **blackboard):
        client = seed(**blackboard)
        node = NavigationExec(FakeDriver(poll_sequence), TOLERANCE, RESEND, TIMEOUT,
                              now_fn=clock or _Clock())
        node.setup()
        node.initialise()
        node.test_bb = client       # 리프는 목적지·위치를 READ 로만 잡는다
        return node

    return _make


def _at(x, y):
    return {"x": x, "y": y}


def _to(x, y):
    return {"x": x, "y": y, "yaw": 0.0}


def test_stays_running_while_the_robot_is_still_driving(leaf):
    """접수 응답이 와도 도착 전이면 RUNNING 이다 — 이게 이 클래스의 존재 이유다."""
    node = leaf(["success"], **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    for _ in range(20):
        assert node.update() == Status.RUNNING


def test_succeeds_when_the_robot_reaches_the_target(leaf):
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate",
                   Keys.NAV_TARGET: _to(1.0, 0.0),
                   Keys.ROBOT_POSE: _at(0.0, 0.0)})
    assert node.update() == Status.RUNNING

    node.test_bb.set(Keys.ROBOT_POSE, _at(0.97, 0.0))       # 0.03m — 허용 안
    assert node.update() == Status.SUCCESS
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None, "다음 명령을 받으려면 슬롯을 비운다"


def test_unknown_position_is_not_arrival(leaf):
    """위치를 모르면 도착이 아니다. 모르는 걸 도착으로 치면 로봇을 두고 다음으로 넘어간다."""
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate", Keys.NAV_TARGET: _to(0.0, 0.0)})
    assert node.update() == Status.RUNNING


def test_new_target_flows_through_without_stopping(leaf):
    """목적지가 바뀌면 그대로 이어서 보낸다 — 노드마다 멈추지 않게 하는 지점이다.

    관제는 로봇이 arrive_radius 안에 들어오면 **nav2 가 마지막 몇 cm 를 좁히는 중에**
    다음 노드를 허가한다. 그러니 목적지 변경은 예외가 아니라 정상 흐름이다.
    """
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate",
                   Keys.NAV_TARGET: _to(1.0, 0.0),
                   Keys.ROBOT_POSE: _at(0.0, 0.0)})
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == 1

    node.test_bb.set(Keys.NAV_TARGET, _to(2.0, 0.0))        # 다음 노드
    assert node.update() == Status.RUNNING, "목적지가 바뀌었다고 주행이 끊기면 안 된다"
    assert node.driver.start_count == 2


def test_same_target_is_re_driven_after_the_resend_window(leaf):
    """도착 못 한 채 시간이 지나면 같은 목적지로 다시 몬다.

    nav2 주행은 도착 없이 끝날 수 있다(ABORTED, 또는 선점 순간 직전 목표의 완료가
    새 목표의 완료로 보고되는 경우). 재전송을 **BT 가** 하는 이유는, 도착했는지
    아는 게 BT 뿐이기 때문이다 — FMS 가 시간만 보고 보내면 정상 주행에도 끼어든다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    assert node.driver.start_count == 1

    clock.t = RESEND - 0.1
    node.update()
    assert node.driver.start_count == 1, "정상 주행 중에 끼어들면 nav2 목표가 선점된다"

    clock.t = RESEND
    node.update()
    assert node.driver.start_count == 2


def test_motion_stall_watchdog_recovers_before_long_arrival_timeout(leaf):
    clock = _Clock()
    node = leaf(clock=clock,
                **{Keys.ACTIVE_COMMAND: "navigate",
                   Keys.NAV_TARGET: _to(1.0, 0.0),
                   Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.recovery_stall_sec = 5.0
    node.update()
    clock.t = 4.9
    node.update()
    assert node.driver.start_count == 1
    clock.t = 5.0
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == 2


def test_gives_up_after_the_arrive_timeout(leaf):
    """영영 못 가는 것과 아직 가는 중인 것을 구별한다.

    FAILURE 를 내야 dispatch Selector 가 흘러가고, 새 명령이 안 오면
    CommandTimeout 이 로봇을 ERROR 로 데려간다. 조용히 서 있는 게 제일 나쁘다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    clock.t = TIMEOUT
    assert node.update() == Status.RUNNING, "첫 watchdog 만료는 자동 복구해야 한다"
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) == "navigate"


def test_retries_a_lost_goal_then_fails_loudly(leaf):
    """접수 유실이 반복되면 무한 대기하지 않고 최종 실패한다."""
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    for _ in range(3):
        clock.t += TIMEOUT
        assert node.update() == Status.RUNNING
    clock.t += TIMEOUT
    assert node.update() == Status.FAILURE
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None


def test_arrive_timeout_restarts_on_a_new_target(leaf):
    """긴 주행이 중간 노드를 지날 때마다 시간이 다시 시작돼야 한다.

    안 그러면 노드가 많은 다리는 도착도 못 했는데 타임아웃에 걸린다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    clock.t = TIMEOUT - 1
    node.test_bb.set(Keys.NAV_TARGET, _to(2.0, 0.0))
    node.update()

    clock.t = TIMEOUT + 1
    assert node.update() == Status.RUNNING, "새 목적지부터 다시 센다"


def test_target_without_coordinates_releases_the_slot(leaf):
    node = leaf(**{Keys.ACTIVE_COMMAND: "navigate"})
    assert node.update() == Status.FAILURE
    assert node.driver.start_count == 0
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None


def test_other_commands_fall_through(leaf):
    """자기 명령이 아니면 즉시 FAILURE — Selector 가 다음 처리기로 넘어가는 방식이다."""
    node = leaf(**{Keys.ACTIVE_COMMAND: "perform_action"})
    assert node.update() == Status.FAILURE
    assert node.driver.start_count == 0


def test_halted_time_does_not_count_toward_the_arrive_timeout():
    """사람을 기다리며 서 있던 시간은 "가다가 못 갔다" 에 안 들어간다.

    안 그러면 60초를 넘겨 FAILURE → CommandTimeout → ERROR 로 가고,
    fleet_node 가 is_immobile("ERROR") 로 보아 **이 로봇 자체를 영구 장애물**로
    표시한다. 사람을 기다리다 스스로 장애물이 되는 경로다.
    """
    import py_trees
    from py_trees.common import Access, Status

    from libi_modes.blackboard import Keys
    from libi_modes.common.working_actions import NavigationExec

    class Clock:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            return self.t

    class Driver:
        def start(self, args=None):
            pass

        def poll(self):
            return "running"

        def stop(self):
            pass

    clock = Clock()
    leaf = NavigationExec(Driver(), arrive_tolerance=0.05, arrive_resend_sec=30.0,
                          arrive_timeout_sec=60.0, now_fn=clock)
    leaf.setup()
    bb = py_trees.blackboard.Client(name="t")
    for k in (Keys.ACTIVE_COMMAND, Keys.NAV_TARGET, Keys.ROBOT_POSE):
        bb.register_key(key=k, access=Access.WRITE)
    bb.set(Keys.ACTIVE_COMMAND, "navigate")
    bb.set(Keys.NAV_TARGET, {"x": 5.0, "y": 0.0})
    bb.set(Keys.ROBOT_POSE, {"x": 0.0, "y": 0.0, "yaw": 0.0})

    assert leaf.update() == Status.RUNNING
    leaf.pause_arrive_timer(True)          # 사람 때문에 정지
    clock.t = 300.0                         # 5분을 서 있었다
    leaf.pause_arrive_timer(False)          # 다시 출발
    clock.t = 310.0
    assert leaf.update() == Status.RUNNING, "멈춰 있던 300초가 시계를 먹었다"


def test_halted_drive_does_not_resend_the_goal(leaf):
    """[Critical] 사람 때문에 멈춘 동안 `arrive_resend_sec` 이 지나도 goal 을 다시
    내면 안 된다 — 다시 내면 nav2 가 새 goal 을 받아 로봇이 사람 쪽으로 다시
    출발한다(정지가 안 유지된다). 되돌리면(재전송 분기를 pause 검사보다 앞에 두면)
    이 시험이 반드시 빨개져야 한다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    assert node.driver.start_count == 1

    node.pause_arrive_timer(True)
    for t in (RESEND, RESEND * 2, RESEND * 5):
        clock.t = t
        assert node.update() == Status.RUNNING
    assert node.driver.start_count == 1, "멈춰 있는 동안 goal 을 다시 내면 안 된다"


def test_resumed_drive_sends_the_goal_again(leaf):
    """반대쪽 실패도 막는다 — 정지가 풀리면 goal 이 반드시 다시 나가야 한다.

    안 그러면 사람이 비켜도 로봇이 영영 안 간다.
    """
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    node.update()
    node.pause_arrive_timer(True)
    clock.t = RESEND * 5
    node.update()
    assert node.driver.start_count == 1     # 멈춰 있는 동안은 그대로

    node.pause_arrive_timer(False)
    assert node.update() == Status.RUNNING
    assert node.driver.start_count == 2, "정지가 풀리면 goal 을 다시 내야 한다"


# ── 절대 상한 (2026-08-06, codex P1) ──────────────────────────────────────
#
# `arrive_timeout_sec` 은 상한이 아니었다 — `_retry_or_give_up` 이 재시도마다
# `_target_at` 을 새로 주므로 실효 상한이 `arrive_timeout_sec × (재시도+1)` 로
# **곱해진다.** "총시간 워치독이 받아 준다"는 전제가 그래서 성립하지 않았다.
# 그 상한을 재시도와 무관한 **하나의 숫자**로 뺀다.

def _bare_leaf(clock, **kw):
    """블랙보드까지 직접 세운 리프 — 생성자 인자를 바꿔야 할 때 쓴다."""
    import py_trees
    from py_trees.common import Access

    from libi_modes.blackboard import Keys as K
    from libi_modes.common.working_actions import NavigationExec

    class Driver:
        def start(self, args=None):
            pass

        def poll(self):
            return "running"

        def stop(self):
            pass

    node = NavigationExec(Driver(), arrive_tolerance=0.05, arrive_resend_sec=30.0,
                          arrive_timeout_sec=60.0, now_fn=clock, **kw)
    node.setup()
    bb = py_trees.blackboard.Client(name="hard")
    for k in (K.ACTIVE_COMMAND, K.NAV_TARGET, K.ROBOT_POSE):
        bb.register_key(key=k, access=Access.WRITE)
    bb.set(K.ACTIVE_COMMAND, "navigate")
    bb.set(K.NAV_TARGET, {"x": 5.0, "y": 0.0})
    bb.set(K.ROBOT_POSE, {"x": 0.0, "y": 0.0, "yaw": 0.0})
    node.test_bb = bb
    return node


def test_hard_timeout_is_not_extended_by_retries():
    """재시도가 아무리 많아도 절대 상한을 넘겨 붙들지 못한다.

    재시도 상한을 크게 줘서 "재시도 소진"으로 끝나는 길을 막아 둔다 — 그래야
    끝내는 것이 **절대 상한**임이 드러난다.
    """
    clock = _Clock()
    node = _bare_leaf(clock, recovery_retry_max=99, hard_timeout_sec=100.0)
    assert node.update() == Status.RUNNING
    clock.t = 99.0
    assert node.update() == Status.RUNNING, "아직 상한 안이다"
    clock.t = 101.0
    assert node.update() == Status.FAILURE, "재시도가 남았다고 상한을 넘겨 붙들었다"
    assert node.test_bb.get(Keys.ACTIVE_COMMAND) is None


def test_hard_timeout_defaults_to_the_old_effective_bound():
    """기본값은 **지금 동작을 안 바꾼다** — 암묵적 곱셈을 숫자 하나로 옮겼을 뿐이다."""
    node = _bare_leaf(_Clock(), recovery_retry_max=3)
    assert node.hard_timeout_sec == 60.0 * 4


def test_hard_timeout_restarts_on_a_new_target():
    """노드가 많은 다리는 중간 노드를 지날 때마다 예산이 새로 생겨야 한다."""
    clock = _Clock()
    node = _bare_leaf(clock, hard_timeout_sec=100.0)
    node.update()
    clock.t = 99.0
    node.test_bb.set(Keys.NAV_TARGET, {"x": 6.0, "y": 0.0})
    node.update()
    clock.t = 150.0
    assert node.update() == Status.RUNNING, "새 목적지부터 다시 세야 한다"


def test_waiting_for_a_person_does_not_eat_the_hard_budget():
    """⚠️ 사람을 기다린 시간이 상한을 먹으면, 조금만 오래 기다려도 ERROR 로 가고
    fleet_node 가 이 로봇을 **영구 장애물**로 표시한다."""
    clock = _Clock()
    node = _bare_leaf(clock, hard_timeout_sec=100.0)
    assert node.update() == Status.RUNNING
    node.pause_arrive_timer(True)
    clock.t = 300.0                     # 5분을 서 있었다
    node.pause_arrive_timer(False)
    clock.t = 310.0
    assert node.update() == Status.RUNNING, "서 있던 300초가 절대 상한을 먹었다"


# ── 대기열 중에는 도착 시계를 안 돌린다 (2026-08-06) ──────────────────────
#
# 실행 층은 현재 waypoint 를 끝낸 뒤에 다음 목표를 보낸다. 그 동안은 **출발도 안 한**
# 상태라, 도착 시계를 돌리면 "가다가 못 갔다"로 오인해 재시도를 소진한다.

def _bb_write(node, key, value):
    import py_trees
    from py_trees.common import Access
    c = py_trees.blackboard.Client(name=f"w-{key}")
    c.register_key(key=key, access=Access.WRITE)
    c.set(key, value)


def test_queued_time_does_not_count_toward_the_arrive_timeout(leaf):
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    assert node.update() == Status.RUNNING
    _bb_write(node, "nav_phase", "queued")
    for t in range(1, 200):                 # 200초를 대기열에서 보낸다
        clock.t = float(t)
        assert node.update() == Status.RUNNING
    # ⚠️ RUNNING 만 보면 아무것도 검증 못 한다 — 대기 분기를 꺼도 재시도(3회)가
    #    각각 시계를 리셋해 200초 안에는 어차피 FAILURE 가 안 난다. **재시도가 한 번도
    #    안 일어났는지**를 본다. 그게 "시계가 안 돌았다"의 직접 증거다.
    assert node._recovery_retries == 0, "대기 시간이 도착 시계를 먹어 워치독이 돌았다"
    _bb_write(node, "nav_phase", "driving")
    clock.t += 1.0
    assert node.update() == Status.RUNNING


def test_queued_time_still_counts_toward_the_hard_timeout():
    """⚠️ 절대 상한은 **어떤 사유로도 안 늘어난다.**

    대기열이 안 빠지는 버그가 나면 오히려 이것만이 이 다리를 끊는다.
    """
    clock = _Clock()
    node = _bare_leaf(clock, recovery_retry_max=99, hard_timeout_sec=100.0)
    assert node.update() == Status.RUNNING
    _bb_write(node, "nav_phase", "queued")
    clock.t = 101.0
    assert node.update() == Status.FAILURE, "대기가 절대 상한까지 늘렸다"


def test_unknown_nav_phase_behaves_like_before(leaf):
    """옛 robot_agent · 브릿지 다운이면 값이 안 온다 — 그때는 예전처럼 돌아야 한다.
    모름을 대기로 읽으면 시계가 영영 안 돌아 더 나쁘다."""
    clock = _Clock()
    node = leaf(clock=clock, **{Keys.ACTIVE_COMMAND: "navigate",
                                Keys.NAV_TARGET: _to(1.0, 0.0),
                                Keys.ROBOT_POSE: _at(0.0, 0.0)})
    _bb_write(node, "nav_phase", None)
    node.update()
    clock.t = TIMEOUT * 4 + 1
    assert node.update() == Status.FAILURE, "값이 없으면 예전처럼 시계가 돌아야 한다"
