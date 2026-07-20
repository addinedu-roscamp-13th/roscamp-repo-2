import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.topics2bb import Topics2BB


# ── IsMode ────────────────────────────────────────────────────────────────────

def test_is_mode_matches(seed, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE"})
    assert tick(IsMode("IDLE")) == Status.SUCCESS


def test_is_mode_rejects_other_mode(seed, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE"})
    assert tick(IsMode("WORKING")) == Status.FAILURE


def test_is_mode_survives_unset_current_mode(tick):
    """Boot ordering: a branch can tick before current_mode has ever been written."""
    assert tick(IsMode("IDLE")) == Status.FAILURE


def test_is_mode_disabled_branch_fails_even_when_mode_matches(seed, tick):
    """[디버그] 잠긴 상태는 current_mode 가 맞아도 FAILURE → Selector 가 건너뜀."""
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.DISABLED_BRANCHES: {"IDLE"}})
    assert tick(IsMode("IDLE")) == Status.FAILURE


def test_is_mode_other_disabled_does_not_affect_this_branch(seed, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.DISABLED_BRANCHES: {"RETURNING"}})
    assert tick(IsMode("IDLE")) == Status.SUCCESS


def test_is_mode_empty_disabled_is_normal(seed, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.DISABLED_BRANCHES: set()})
    assert tick(IsMode("PATROL")) == Status.SUCCESS


# ── RequestTransition ─────────────────────────────────────────────────────────

def test_request_transition_applies_and_clears(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.NEXT_MODE: "PATROL"})
    assert tick(RequestTransition()) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert read(Keys.NEXT_MODE) is None, "next_mode must be consumed or it re-fires next tick"


def test_request_transition_fails_without_target(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.NEXT_MODE: None})
    assert tick(RequestTransition()) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_request_transition_does_not_refire_on_second_tick(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.NEXT_MODE: "PATROL"})
    node = RequestTransition()
    tick(node, times=2)
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert node.status == Status.FAILURE, "second tick has nothing to apply"


# ── FaultDetected ─────────────────────────────────────────────────────────────

def test_fault_detected_sets_error(seed, read, tick):
    seed(**{Keys.FAULT: True})
    assert tick(FaultDetected()) == Status.SUCCESS
    assert read(Keys.NEXT_MODE) == "ERROR"


def test_fault_detected_quiet_when_no_fault(seed, read, tick):
    seed(**{Keys.FAULT: False})
    assert tick(FaultDetected()) == Status.FAILURE
    assert read(Keys.NEXT_MODE) is None


# ── BatteryCheck ──────────────────────────────────────────────────────────────

def test_battery_check_ge_threshold(seed, read, tick):
    seed(**{Keys.BATTERY_PERCENT: 85.0})
    assert tick(BatteryCheck(">=", 80, "PATROL")) == Status.SUCCESS
    assert read(Keys.NEXT_MODE) == "PATROL"


def test_battery_check_below_threshold_is_quiet(seed, read, tick):
    seed(**{Keys.BATTERY_PERCENT: 35.0})
    assert tick(BatteryCheck(">=", 40, "IDLE")) == Status.FAILURE
    assert read(Keys.NEXT_MODE) is None


def test_battery_check_le_threshold(seed, tick):
    seed(**{Keys.BATTERY_PERCENT: 10.0})
    assert tick(BatteryCheck("<=", 15, "RETURNING")) == Status.SUCCESS


def test_battery_check_dock_guard_blocks_when_undocked(seed, tick):
    """80% while NOT docked must not auto-start patrol — a stopped robot reads high but
    is not at the charger."""
    seed(**{Keys.BATTERY_PERCENT: 95.0, Keys.IS_DOCKED: False})
    assert tick(BatteryCheck(">=", 80, "PATROL", require_docked=True)) == Status.FAILURE


def test_battery_check_dock_guard_blocks_when_docked(seed, tick):
    """10% while docked must not trigger return — it is already charging."""
    seed(**{Keys.BATTERY_PERCENT: 10.0, Keys.IS_DOCKED: True})
    assert tick(BatteryCheck("<=", 15, "RETURNING", require_docked=False)) == Status.FAILURE


def test_battery_check_unknown_battery_is_quiet(tick):
    assert tick(BatteryCheck("<=", 15, "RETURNING")) == Status.FAILURE


def test_battery_check_rejects_bad_operator():
    with pytest.raises(ValueError):
        BatteryCheck("==", 50, "IDLE")


# ── CommandListener ───────────────────────────────────────────────────────────

def test_command_listener_maps_and_consumes(seed, read, tick):
    seed(**{Keys.LAST_COMMAND: "task_assigned"})
    assert tick(CommandListener({"task_assigned": "WORKING"})) == Status.SUCCESS
    assert read(Keys.NEXT_MODE) == "WORKING"
    assert read(Keys.LAST_COMMAND) is None, "command must be consumed or it re-fires"


def test_command_listener_ignores_unmapped_command(seed, read, tick):
    """An unmapped command is left in place — a later branch may be its real recipient."""
    seed(**{Keys.LAST_COMMAND: "ui_touch"})
    assert tick(CommandListener({"task_assigned": "WORKING"})) == Status.FAILURE
    assert read(Keys.LAST_COMMAND) == "ui_touch"


def test_command_listener_quiet_with_no_command(seed, tick):
    seed(**{Keys.LAST_COMMAND: None})
    assert tick(CommandListener({"task_assigned": "WORKING"})) == Status.FAILURE


# ── Topics2BB ─────────────────────────────────────────────────────────────────

def test_topics2bb_writes_values_and_stays_running(read, tick):
    node = Topics2BB({
        "battery_percent": lambda: 42.0,
        "is_docked": lambda: True,
        "fault": lambda: False,
        "last_command": lambda: "task_assigned",
        "ui_last_touch_at": lambda: 3.5,
    })
    assert tick(node) == Status.RUNNING
    assert read(Keys.BATTERY_PERCENT) == 42.0
    assert read(Keys.IS_DOCKED) is True
    assert read(Keys.LAST_COMMAND) == "task_assigned"
    assert read(Keys.UI_LAST_TOUCH_AT) == 3.5


def test_topics2bb_rereads_providers_every_tick(tick):
    calls = {"n": 0}

    def battery():
        calls["n"] += 1
        return 50.0

    tick(Topics2BB({"battery_percent": battery}), times=3)
    assert calls["n"] == 3, "providers must be polled per tick, not cached"


def test_topics2bb_rejects_unknown_provider():
    with pytest.raises(ValueError):
        Topics2BB({"not_a_real_key": lambda: 1})
