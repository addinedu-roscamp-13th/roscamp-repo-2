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

def test_follow_admin_reaches_the_follow_driver(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    nav, arm, follow = FakeDriver(), FakeDriver(), FakeDriver()
    root = working.create(PARAMS, nav, arm, follow, clock=lambda: 1.0)
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
    tick(working.create(PARAMS, nav, arm, follow, clock=lambda: 1.0))
    assert nav.started and not follow.started


def test_perform_action_does_not_reach_the_follow_driver(seed, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "perform_action"})
    nav, arm, follow = FakeDriver(), FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, follow, clock=lambda: 1.0))
    assert arm.started and not follow.started


def test_follow_session_end_clears_active_command(seed, read, tick):
    """A finished follow must release the slot so the adapter can dispatch again."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(),
                          FakeDriver(["success"]), clock=lambda: 1.0)
    tick(root)
    assert read(Keys.ACTIVE_COMMAND) is None


# ── structure ─────────────────────────────────────────────────────────────────

def test_follow_exec_precedes_awaiting_command():
    """Running("AwaitingCommand") always succeeds, so anything after it is unreachable."""
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), FakeDriver(),
                          clock=lambda: 1.0)
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
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, clock=lambda: 1.0)
    tick(root)      # must not raise


def test_unwired_follow_is_logged(seed, tick, caplog):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, clock=lambda: 1.0)
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
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), None, clock=lambda: 1.0)
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
                          clock=lambda: now["t"])

    assert tick(root) == Status.RUNNING          # follow failed, slot released
    assert read(Keys.CURRENT_MODE) == "WORKING"

    now["t"] = PARAMS["working"]["command_timeout_sec"] + 1.0
    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_unwired_driver_stop_is_a_noop():
    UnwiredDriver("follow_admin").stop()      # tearing down what never started is fine
