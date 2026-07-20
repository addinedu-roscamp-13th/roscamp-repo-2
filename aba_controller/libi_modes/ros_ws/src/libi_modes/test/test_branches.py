"""One test per edge of the transition box, plus the edges that must NOT exist."""
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.branches import (
    charging, error, idle, interacting, patrol, returning, security_patrol, working,
)
from test.fakes import PARAMS, FakeArmDriver, FakeDriver


# ── CHARGING ──────────────────────────────────────────────────────────────────

def test_charging_waits_below_ready(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 35.0})
    assert tick(charging.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_charging_to_idle_at_ready(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 41.0})
    assert tick(charging.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_charging_fault_to_error(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 10.0, Keys.FAULT: True})
    assert tick(charging.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


# ── IDLE ──────────────────────────────────────────────────────────────────────

def test_idle_docked_and_charged_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 85.0, Keys.IS_DOCKED: True})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_idle_undocked_high_battery_stays(seed, read, tick):
    """A robot stopped by an operator is undocked; without the dock guard a stale-high
    reading would silently restart patrol."""
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 95.0, Keys.IS_DOCKED: False})
    assert tick(idle.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_idle_undocked_low_battery_returns(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 10.0, Keys.IS_DOCKED: False})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "RETURNING"


def test_idle_docked_low_battery_does_not_return(seed, read, tick):
    """Already on the charger — "returning" would be a no-op drive cycle."""
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 10.0, Keys.IS_DOCKED: True})
    assert tick(idle.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_idle_task_assigned_to_working(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 50.0,
            Keys.IS_DOCKED: True, Keys.LAST_COMMAND: "task_assigned"})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_idle_security_patrol_request(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 50.0,
            Keys.IS_DOCKED: True, Keys.LAST_COMMAND: "security_patrol_request"})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"


def test_idle_resume_request_frees_stopped_robot(seed, read, tick):
    """The documented escape for a stopped, undocked robot."""
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 50.0,
            Keys.IS_DOCKED: False, Keys.LAST_COMMAND: "resume_request"})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


# ── PATROL ────────────────────────────────────────────────────────────────────

def test_patrol_keeps_driving(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0})
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert driver.started


def test_patrol_nav_never_self_completes(seed, tick):
    """Even a driver reporting success means "lap done" — patrol is endless."""
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0})
    assert tick(patrol.create(PARAMS, FakeDriver(["success"]))) == Status.RUNNING


def test_patrol_low_battery_returns_and_stops_motors(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 10.0})
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "RETURNING"
    assert driver.stopped, "motors must be halted before the transition"


def test_patrol_task_assigned_to_working(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "task_assigned"})
    assert tick(patrol.create(PARAMS, FakeDriver())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_patrol_ui_touch_to_interacting(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "ui_touch"})
    assert tick(patrol.create(PARAMS, FakeDriver())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "INTERACTING"


def test_patrol_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "stop_request"})
    assert tick(patrol.create(PARAMS, FakeDriver())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


# ── SECURITY_PATROL ───────────────────────────────────────────────────────────

def test_security_patrol_one_lap_then_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0})
    assert tick(security_patrol.create(PARAMS, FakeDriver(["success"]))) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_security_patrol_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "stop_request"})
    assert tick(security_patrol.create(PARAMS, FakeDriver())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_security_patrol_ignores_task_assignment(seed, read, tick):
    """Night duty is not interruptible by day work."""
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "task_assigned"})
    assert tick(security_patrol.create(PARAMS, FakeDriver())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"


def test_security_patrol_ignores_ui_touch(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "ui_touch"})
    assert tick(security_patrol.create(PARAMS, FakeDriver())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"


def test_security_patrol_low_battery_returns(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 10.0})
    assert tick(security_patrol.create(PARAMS, FakeDriver())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "RETURNING"


# ── INTERACTING ───────────────────────────────────────────────────────────────

def test_interacting_holds_session_and_locks(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.UI_LAST_TOUCH_AT: 0.0})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.RUNNING
    assert read(Keys.DRIVE_LOCK) is True
    assert read(Keys.ARM_LOCK) is True, "a visitor at the panel is inside the arm's radius"


def test_interacting_timeout_to_patrol_and_unlocks(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.UI_LAST_TOUCH_AT: 0.0})
    assert tick(interacting.create(PARAMS, clock=lambda: 25.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert read(Keys.DRIVE_LOCK) is False
    assert read(Keys.ARM_LOCK) is False, "a missed release leaves the robot frozen"


def test_interacting_ui_close_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.LAST_COMMAND: "ui_close"})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_interacting_task_assigned_to_working_unlocks(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.LAST_COMMAND: "task_assigned"})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"
    assert read(Keys.DRIVE_LOCK) is False


def test_interacting_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.LAST_COMMAND: "stop_request"})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


# ── WORKING ───────────────────────────────────────────────────────────────────

def test_working_dispatches_navigate(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    nav, arm = FakeDriver(), FakeDriver()
    assert tick(working.create(PARAMS, nav, arm, clock=lambda: 1.0)) == Status.RUNNING
    assert nav.started and not arm.started


def test_working_dispatches_dock_to_navigation(seed, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "dock"})
    nav, arm = FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, clock=lambda: 1.0))
    assert nav.started and not arm.started


def test_working_dispatches_perform_action_to_arm(seed, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "perform_action"})
    nav, arm = FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, clock=lambda: 1.0))
    assert arm.started and not nav.started


def test_working_waits_with_no_command(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 0.0})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: 1.0)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_command_timeout_to_error(seed, read, tick):
    """WORKING has no battery exit, so without this the robot is stuck for good."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 0.0})
    now = {"t": 0.0}
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: now["t"])
    assert tick(root) == Status.RUNNING, "grace period starts on entry, not at epoch"
    now["t"] = 200.0
    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_working_does_not_time_out_immediately_on_entry(seed, read, tick):
    """Regression: measuring only from command_received_at (0.0 until the adapter first
    writes it) against a monotonic clock reading system uptime fired instantly, sending a
    freshly-assigned robot straight to ERROR."""
    import time
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 0.0})
    root = working.create(PARAMS, FakeDriver(), FakeDriver())   # real time.monotonic
    assert tick(root, times=3) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_timeout_window_resets_on_each_command(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 150.0})
    now = {"t": 200.0}
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: now["t"])
    assert tick(root) == Status.RUNNING, "only 50s since the last command"
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_timeout_does_not_fire_while_executing(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate",
            Keys.COMMAND_RECEIVED_AT: 0.0})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: 200.0)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_task_done_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: "task_done"})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: 1.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_working_task_failed_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: "task_failed"})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: 1.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_working_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: "stop_request"})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), clock=lambda: 1.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_working_clears_active_command_when_done(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    tick(working.create(PARAMS, FakeDriver(["success"]), FakeDriver(), clock=lambda: 1.0))
    assert read(Keys.ACTIVE_COMMAND) is None, "next command can't be dispatched otherwise"


# ── RETURNING ─────────────────────────────────────────────────────────────────

def test_returning_homes_arm_before_driving(seed, tick):
    seed(**{Keys.CURRENT_MODE: "RETURNING"})
    arm, dock = FakeArmDriver(), FakeDriver()
    tick(returning.create(PARAMS, arm, dock))
    assert arm.went_home, "arm pose is unknown at boot; driving first risks a collision"


def test_returning_docked_to_charging(seed, read, tick):
    """dock_driver 의 success 는 명령 접수일 뿐 — is_docked(실제 도킹 확인, 예: 충전소 위치
    +yaw 근접)가 별도로 True 여야 CHARGING 으로 넘어간다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: True})
    assert tick(returning.create(PARAMS, FakeArmDriver(), FakeDriver(["success"]))) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_returning_dock_command_accepted_but_not_yet_confirmed_stays_returning(seed, read, tick):
    """도킹 명령이 성공 응답을 받아도, is_docked 가 아직 False(또는 미수신=None)면
    자동으로 CHARGING 에 진입하지 않는다 — send_nav_goal 은 완료 대기 없이 리턴한다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    assert tick(returning.create(PARAMS, FakeArmDriver(), FakeDriver(["success"]))) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "RETURNING"


def test_returning_retries_dock_without_faulting(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.DOCK_RETRY_COUNT: 0})
    assert tick(returning.create(PARAMS, FakeArmDriver(), FakeDriver(["failure"]))) == Status.RUNNING
    assert read(Keys.DOCK_RETRY_COUNT) == 1
    assert read(Keys.FAULT) is False


def test_returning_faults_after_retries_exhausted(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.DOCK_RETRY_COUNT: 2})
    root = returning.create(PARAMS, FakeArmDriver(), FakeDriver(["failure"]))
    tick(root)
    assert read(Keys.DOCK_RETRY_COUNT) == 3
    assert read(Keys.FAULT) is True, "sets fault so the branch's own FaultDetected sees it"


def test_returning_reaches_error_when_retries_exhausted(seed, read, tick):
    """The nav leaf raises fault and stays RUNNING rather than failing, so the sibling
    watchdog sees the fault on the same tick and routes to ERROR. Returning FAILURE here
    would abort the Parallel first and the transition would never happen."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.DOCK_RETRY_COUNT: 2})
    root = returning.create(PARAMS, FakeArmDriver(), FakeDriver(["failure"]))
    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_returning_ignores_stop_request(seed, read, tick):
    """Below 15% and away from the charger — stopping here means going flat."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.LAST_COMMAND: "stop_request"})
    tick(returning.create(PARAMS, FakeArmDriver(), FakeDriver()))
    assert read(Keys.CURRENT_MODE) == "RETURNING"


# ── ERROR ─────────────────────────────────────────────────────────────────────

def test_error_holds_without_recovery(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "ERROR", Keys.LAST_COMMAND: None})
    assert tick(error.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_error_recovered_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "ERROR", Keys.LAST_COMMAND: "recovered"})
    assert tick(error.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_error_does_not_self_rescue_on_low_battery(seed, read, tick):
    """An unexplained fault must not resume autonomous motion, even to reach a charger."""
    seed(**{Keys.CURRENT_MODE: "ERROR", Keys.BATTERY_PERCENT: 5.0, Keys.IS_DOCKED: False})
    assert tick(error.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "ERROR"
