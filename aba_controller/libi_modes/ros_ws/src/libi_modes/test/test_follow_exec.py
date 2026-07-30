"""FollowExec — the seam through which libi_perception plugs into WORKING.

Kept in its own file so the follow feature can be reviewed and reverted independently of
the rest of the WORKING branch.
"""
import logging

import py_trees
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

    실패는 Selector 가 `AwaitingCommand` 로 떨어져 같은 tick 에 `RequestTransition` 까지
    못 간다. 그래서 여기서는 예약값(NEXT_MODE)만 확인한다 — 길잡이의 요청자 이탈 시험과
    같은 구조다.
    """
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(),
                          FakeDriver(["failure"]), clock=lambda: 1.0, undock_gate=_gate())
    tick(root)
    assert read(Keys.NEXT_MODE) == "PATROL", "세션이 접혔으면 WORKING 에 갇히면 안 된다"


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


def test_unwired_follow_is_logged(seed, tick, caplog):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, undock_gate=_gate(), clock=lambda: 1.0)
    with caplog.at_level(logging.ERROR):
        tick(root)
    assert "follow_admin" in caplog.text


def test_unwired_follow_logs_once_not_every_tick(seed, tick, caplog):
    """The tree ticks at 20 Hz — repeating this would bury the log."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    driver = UnwiredDriver("follow_admin")
    with caplog.at_level(logging.ERROR):
        for _ in range(10):
            driver.start()
            driver.poll()
    assert caplog.text.count("follow_admin") == 1


def test_unwired_follow_releases_the_command_slot(seed, read, tick):
    """Failing the command must clear active_command, or dispatch stays wedged on it."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, undock_gate=_gate(), clock=lambda: 1.0)
    tick(root)
    assert read(Keys.ACTIVE_COMMAND) is None


def test_unwired_follow_ends_in_error_not_a_dead_node(seed, read, tick):
    """The survivable path end to end: the command fails, dispatch falls through to
    AwaitingCommand, and CommandTimeout carries the robot to ERROR — stopped and
    diagnosable rather than a dead node.

    Driven through working.create because that is where a clock can be injected;
    tree.build_root does not thread one through, and with real monotonic time the
    120 s timeout would never elapse inside a test.
    """
    now = {"t": 0.0}
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None,
                          clock=lambda: now["t"], undock_gate=_gate())

    assert tick(root) == Status.RUNNING          # follow failed, slot released
    assert read(Keys.CURRENT_MODE) == "WORKING"

    now["t"] = PARAMS["working"]["command_timeout_sec"] + 1.0
    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_unwired_driver_stop_is_a_noop():
    UnwiredDriver("follow_admin").stop()      # tearing down what never started is fine
