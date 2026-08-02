"""FollowExec — the seam through which libi_perception plugs into WORKING.

Kept in its own file so the follow feature can be reviewed and reverted independently of
the rest of the WORKING branch.
"""
import logging

import py_trees
import pytest
from py_trees.common import Status

from libi_modes import registry, tree
from libi_modes.blackboard import Keys
from libi_modes.branches import working
from libi_modes.common.working_actions import FollowExec, UnwiredDriver
from test.fakes import PARAMS, FakeDriver, all_drivers, all_providers


def _walk(node):
    yield node
    for child in getattr(node, "children", []):
        yield from _walk(child)


def _dispatch(root):
    return next(n for n in _walk(root) if n.name == "CommandDispatch")


# ── dispatch ──────────────────────────────────────────────────────────────────

def _gate():
    """도킹 탈출 게이트 대역 — `working.create` 가 필수로 받는다(test_branches 와 같은 이유)."""
    from libi_modes.common import undock
    return undock.create(FakeDriver(), distance_m=0.06, timeout_sec=8.0,
                         retry_max=3, now_fn=lambda: 0.0)

def test_follow_admin_reaches_the_follow_driver(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    nav, arm, follow = FakeDriver(), FakeDriver(), FakeDriver()
    root = working.create(PARAMS, nav, arm, follow, undock_gate=_gate(), clock=lambda: 1.0)
    assert tick(root) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"
    assert follow.started
    assert not nav.started and not arm.started


def test_navigate_does_not_reach_the_follow_driver(seed, tick):
    """The three exec leaves must stay mutually exclusive."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate",
            Keys.NAV_TARGET: {"x": 1.0, "y": 2.0, "yaw": 0.0},
            Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}})
    nav, arm, follow = FakeDriver(), FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, follow, undock_gate=_gate(), clock=lambda: 1.0))
    assert nav.started and not follow.started


def test_perform_action_does_not_reach_the_follow_driver(seed, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "perform_action"})
    nav, arm, follow = FakeDriver(), FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, follow, undock_gate=_gate(), clock=lambda: 1.0))
    assert arm.started and not follow.started


def test_follow_session_end_clears_active_command(seed, read, tick):
    """A finished follow must release the slot so the adapter can dispatch again."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(),
                          FakeDriver(["success"]), clock=lambda: 1.0, undock_gate=_gate())
    tick(root)
    assert read(Keys.ACTIVE_COMMAND) is None


def test_follow_session_end_takes_the_robot_out_of_working(seed, read, tick):
    """추종이 끝나면 WORKING 도 끝나야 한다 — 길잡이의 도착 처리와 같은 이유다.

    슬롯만 비우면 dispatch Selector 가 `AwaitingCommand` 로 떨어져 로봇이 WORKING 에
    눌러앉는다. 그러면 `CommandTimeout` 이 120초 뒤 ERROR 로 보낼 때까지 아무도 이 상태를
    못 벗어난다.

    화면에서는 이렇게 드러났다(실측 2026-07-28): 패널은 "WORKING 을 벗어남"으로 추종 종료를
    판정하는데 그 일이 영영 안 일어나 `m_following` 이 true 로 남고, **관리자 추종이 두
    번째부터 "이미 추종 중입니다" 로 막혔다.** 추종을 시킨 건 패널이라 FMS 는 세션이
    끝났는지 모른다 — 배달처럼 `task_done` 을 대신 보내줄 주체가 없다.
    """
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(),
                          FakeDriver(["success"]), clock=lambda: 1.0, undock_gate=_gate())
    tick(root)
    # 성공은 Sequence 가 끝까지 가므로 RequestTransition 이 같은 tick 에 적용하고
    # NEXT_MODE 를 지운다 — 결과는 CURRENT_MODE 에서 본다 (test_guide_exec 와 같다).
    assert read(Keys.CURRENT_MODE) == "PATROL", "추종이 끝났는데 WORKING 에 갇혔다"


def test_follow_failure_also_leaves_working(seed, read, tick):
    """실패도 같다. 사람을 놓쳐 세션이 접혀도 WORKING 에 남으면 안 된다.

    실패를 그대로 올리면 Selector 가 `AwaitingCommand` 로 떨어져 같은 tick 에
    `RequestTransition` 까지 못 간다. FollowExec 은 순찰 전이를 예약한 뒤 SUCCESS 로
    끝내야 한다.
    """
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(),
                          FakeDriver(["failure"]), clock=lambda: 1.0, undock_gate=_gate())
    tick(root)
    assert read(Keys.CURRENT_MODE) == "PATROL", "세션이 접혔으면 WORKING 에 갇히면 안 된다"


# ── structure ─────────────────────────────────────────────────────────────────

def test_follow_exec_precedes_awaiting_command():
    """Running("AwaitingCommand") always succeeds, so anything after it is unreachable."""
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), FakeDriver(),
                          clock=lambda: 1.0, undock_gate=_gate())
    names = [c.name for c in _dispatch(root).children]
    assert names[-1] == "AwaitingCommand"
    assert names.index("FollowExec") < names.index("AwaitingCommand")


def test_registry_wires_the_follow_driver():
    drivers = all_drivers()
    branches = registry.build_branches(PARAMS, drivers)
    leaf = next(n for n in _walk(branches["WORKING"]) if isinstance(n, FollowExec))
    assert leaf.driver is drivers["follow"]


def test_tree_still_builds_and_ticks_with_follow_wired(seed, read):
    root = tree.build_root(PARAMS, all_drivers(), all_providers())
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert read(Keys.CURRENT_MODE) == "WORKING"


# ── unwired deployment ────────────────────────────────────────────────────────

def test_tree_builds_without_a_follow_driver():
    """A robot with no libi_perception must still get a valid mission tree."""
    drivers = all_drivers()
    del drivers["follow"]
    branches = registry.build_branches(PARAMS, drivers)
    assert any(isinstance(n, FollowExec) for n in _walk(branches["WORKING"]))


def test_unwired_follow_does_not_kill_the_tick(seed, tick):
    """An exception here would unwind out of rclpy.spin() and kill the whole mission node,
    taking PATROL, RETURNING and ERROR handling down with one unwired command."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, undock_gate=_gate(), clock=lambda: 1.0)
    tick(root)      # must not raise


@pytest.fixture
def unwired_logs(caplog):
    """`UnwiredDriver` 가 남기는 로그를 잡는다. **`caplog` 만으로는 못 잡는다.**

    ROS 워크스페이스를 source 하면 `launch.logging` 이 `logging.setLoggerClass()` 로
    전역 로거 클래스를 `LaunchLogger` 로 바꾼다. 그 클래스는 **기본 `propagate=False`**
    라, 이후 만들어지는 모든 로거가 root 로 레코드를 올리지 않는다. `caplog` 의 핸들러는
    root 에 붙으므로 영영 빈 채로 남는다(레코드는 핸들러를 못 찾아 `logging.lastResort`
    로 stderr 에만 찍힌다 — 시험 출력의 "Captured stderr call" 이 그것이다).

    실측(2026-07-31): `logging.getLoggerClass()` → `launch.logging.LaunchLogger`,
    아무 이름의 새 로거나 `propagate=False`. 워크스페이스를 안 source 하면 True 라
    이 시험이 환경에 따라 붙었다 떨어졌다 했다.

    여기서 보려는 것은 "드라이버가 로그를 남기는가"지 ROS 의 로깅 배선이 아니다.
    그래서 root 를 거치지 않고 **그 로거에 직접** 붙는다.

    ⚠️ [2026-08-02] **`propagate` 도 꺼야 한다 — 안 그러면 같은 레코드를 두 번 센다.**

    위 사정은 ROS 워크스페이스를 **source 한** 환경 이야기다. 안 한 환경에서는
    `propagate` 가 True 라, 여기서 직접 붙인 핸들러로 한 번 잡히고 **root 로 올라가
    caplog 의 root 핸들러에 또 한 번** 잡힌다. 그래서 "한 번만 찍히나" 를 세는
    시험이 환경에 따라 1 이 되기도 2 가 되기도 했다(실측: ROS 미소싱에서 2).

    붙이는 동안만 끄고 원래 값으로 되돌린다 — 이 시험이 보려는 것은 드라이버가
    로그를 몇 번 남기는가지, 로깅 배선이 아니다.
    """
    lg = logging.getLogger("libi_modes.common.working_actions")
    prev_propagate = lg.propagate
    lg.propagate = False
    lg.addHandler(caplog.handler)
    lg.setLevel(logging.ERROR)
    yield caplog
    lg.removeHandler(caplog.handler)
    lg.propagate = prev_propagate


def test_unwired_follow_is_logged(seed, tick, unwired_logs):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, undock_gate=_gate(), clock=lambda: 1.0)
    tick(root)
    assert "follow_admin" in unwired_logs.text


def test_unwired_follow_logs_once_not_every_tick(seed, tick, unwired_logs):
    """The tree ticks at 20 Hz — repeating this would bury the log."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    driver = UnwiredDriver("follow_admin")
    for _ in range(10):
        driver.start()
        driver.poll()
    assert unwired_logs.text.count("follow_admin") == 1


def test_unwired_follow_releases_the_command_slot(seed, read, tick):
    """Failing the command must clear active_command, or dispatch stays wedged on it."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, undock_gate=_gate(), clock=lambda: 1.0)
    tick(root)
    assert read(Keys.ACTIVE_COMMAND) is None


def test_unwired_follow_returns_to_patrol_not_a_dead_node(seed, read, tick):
    """추종 실행기가 없어서 실패해도 WORKING 에 남겨 CommandTimeout 을 기다리지 않는다."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None,
                          clock=lambda: 0.0, undock_gate=_gate())

    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_unwired_driver_stop_is_a_noop():
    UnwiredDriver("follow_admin").stop()      # tearing down what never started is fine


# ── 유지 시간(manual_hold_sec)이 종료 전이를 삼키던 회귀 (2026-08-02) ─────────
# 실측 로그(Pi 02:25:45):
#   [WARN] follow_admin 실패: 추종 실패 — 대상을 놓쳤습니다
#   [WARN] 전이 요청이 적용되지 않았다: WORKING -> PATROL
# 패널이 추종을 켤 때 state_io 가 HOLD_UNTIL=+300초를 찍는데, 추종이 그 안에 끝나면
# RequestTransition 이 거부하고 WORKING 브랜치에서는 그 전이가 **유실**된다.

def test_follow_end_beats_the_manual_hold(seed, read, tick):
    """유지 시간이 남아 있어도 추종 종료 전이는 통과해야 한다."""
    import time as _t
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin",
            Keys.HOLD_UNTIL: _t.monotonic() + 300.0})     # 패널이 방금 눌렀다
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None,
                          clock=lambda: 0.0, undock_gate=_gate())
    tick(root)
    assert read(Keys.CURRENT_MODE) == "PATROL", \
        "유지 시간이 추종 종료 전이를 삼켰다 — WORKING 에 갇힌다"


def test_follow_end_marks_the_transition_as_commanded(seed, read, tick):
    """유지 시간을 뚫는 근거는 `COMMANDED_MODE` 표시다 — 그게 실제로 세워지는지."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None,
                          clock=lambda: 0.0, undock_gate=_gate())
    tick(root)
    # RequestTransition 이 적용하면서 지운다 — 적용됐다는 것이 곧 표시가 있었다는 뜻이다.
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert read(Keys.COMMANDED_MODE) is None
