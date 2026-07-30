"""One test per edge of the transition box, plus the edges that must NOT exist."""
import math

import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.branches import (
    charging, error, idle, interacting, patrol, returning, security_patrol, working,
)
from test.fakes import PARAMS, FakeDriver, all_drivers


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


def test_idle_undocked_high_battery_auto_patrols(seed, read, tick):
    """Dock gate on the >=charged check is dropped until docking is defined, so an undocked
    robot at high battery now auto-patrols (see IdleBranch docstring)."""
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 95.0, Keys.IS_DOCKED: False})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


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

_PATROLLING = {Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
               Keys.ACTIVE_COMMAND: "navigate",
               Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
               Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}}


def test_patrol_keeps_driving(seed, read, tick):
    seed(**_PATROLLING)
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert driver.started


def test_patrol_nav_never_self_completes(seed, tick):
    """Even a driver reporting success means "lap done" — patrol is endless."""
    seed(**_PATROLLING)
    assert tick(patrol.create(PARAMS, FakeDriver(["success"]))) == Status.RUNNING


def test_patrol_waits_between_nodes_without_failing(seed, read, tick):
    """다음 노드 허가를 기다리는 동안에도 순회 브랜치는 살아 있어야 한다.

    관제(fleet_node)가 노드를 하나씩 허가하므로 명령이 비는 순간이 정상적으로 생긴다.
    그때 FAILURE 를 내면 Parallel 이 무너져 브랜치가 매 tick 재진입하고, 주행이
    처음부터 다시 시작돼 로봇이 제자리에서 덜컹거린다.
    """
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0})   # 명령 없음
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert not driver.started, "목적지도 없이 nav2 를 부르면 안 된다"


def test_patrol_arrival_is_not_the_end(seed, read, tick):
    """한 노드에 도착해도 순회는 계속된다 — 도착은 '한 노드 지났다'는 뜻이다."""
    seed(**{**_PATROLLING, Keys.ROBOT_POSE: {"x": 1.0, "y": 0.0}})      # 이미 목적지
    assert tick(patrol.create(PARAMS, FakeDriver())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert read(Keys.ACTIVE_COMMAND) is None, "다음 노드를 받으려면 슬롯을 비운다"


def test_patrol_low_battery_returns_and_stops_motors(seed, read, tick):
    seed(**{**_PATROLLING, Keys.BATTERY_PERCENT: 10.0})
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

def test_security_patrol_keeps_patrolling(seed, read, tick):
    """야간 순찰은 1바퀴로 끝나지 않는다 — 한 노드에 도착해도 계속 순찰(RUNNING)하며
    IDLE 로 스스로 나가지 않는다(PATROL 과 같은 지속 순찰). 그래서 야간 내내 상태를 문다."""
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.ACTIVE_COMMAND: "navigate",
            Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
            Keys.ROBOT_POSE: {"x": 1.0, "y": 0.0}})       # 이미 목적지
    assert tick(security_patrol.create(PARAMS, FakeDriver())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"
    assert read(Keys.ACTIVE_COMMAND) is None, "다음 노드를 받으려면 슬롯을 비운다"


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

def test_interacting_holds_session(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.UI_LAST_TOUCH_AT: 0.0})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.RUNNING


def test_interacting_timeout_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.UI_LAST_TOUCH_AT: 0.0})
    assert tick(interacting.create(PARAMS, clock=lambda: 25.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_interacting_ui_close_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.LAST_COMMAND: "ui_close"})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_interacting_task_assigned_to_working(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.LAST_COMMAND: "task_assigned"})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_interacting_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.LAST_COMMAND: "stop_request"})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


# ── WORKING ───────────────────────────────────────────────────────────────────

def test_working_dispatches_navigate(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate",
            Keys.NAV_TARGET: {"x": 1.0, "y": 2.0, "yaw": 0.0},
            Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}})
    nav, arm = FakeDriver(), FakeDriver()
    assert tick(working.create(PARAMS, nav, arm, clock=lambda: 1.0)) == Status.RUNNING
    assert nav.started and not arm.started


def test_working_rejects_navigate_without_a_target(seed, read, tick):
    """목적지 없는 주행 명령은 붙들지 않고 놓는다.

    붙들고 RUNNING 으로 있으면 로봇이 아무 데도 안 가면서 "주행 중"으로 보인다 —
    그게 정확히 이 프로젝트에서 시간을 가장 많이 버린 실패 모양이다.
    """
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    nav, arm = FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, clock=lambda: 1.0))
    assert not nav.started
    assert read(Keys.ACTIVE_COMMAND) is None, "실행할 수 없는 명령은 슬롯을 비워야 한다"


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
# [2026-07-27] 한 leaf(ReturnNavigation) → 5단계 시퀀스로 바뀌었다.
#   GoToParkingEntrance → FaceParking → GoToParking → TurnAround → AlignDock
# 팔 홈복귀는 없앴다(이 로봇에 팔이 없다 — 사용자 결정).


def _returning(**over):
    """5단계 복귀 브랜치. 좌표·드라이버를 갈아끼울 수 있게 감싼다."""
    d = all_drivers()
    d.update(over)
    return returning.create(
        PARAMS,
        entrance_driver=d["return_entrance"],
        dock_driver=d["return_dock"],
        rotate_driver=d["return_rotate"],
        entrance_xy=d["return_entrance_xy"],
        parking_xy=d["return_parking_xy"],
        clock=lambda: 0.0)


def test_returning_drives_to_the_entrance_first(seed, tick):
    """5단계의 첫 동작은 주차장 **입구** 주행이다."""
    entrance = FakeDriver()
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.ROBOT_POSE: {"x": 5.0, "y": 5.0}})
    tick(_returning(return_entrance=entrance))
    assert entrance.started is True


def _walk_the_steps(root, tick, seed, *, docked):
    """각 단계의 목표에 로봇을 실제로 데려다 놓으며 시퀀스를 끝까지 민다.

    도착 판정이 **실좌표 거리**라, pose 를 안 옮기면 첫 단계에서 영원히 RUNNING 이다
    (그게 이 설계의 요점이다 — 명령 수락을 도착으로 치지 않는다)."""
    entrance, parking = (0.6, 0.0), (0.0, 0.0)
    poses = [
        {"x": entrance[0], "y": entrance[1], "yaw": 0.0},   # ① 입구 도착
        {"x": entrance[0], "y": entrance[1], "yaw": math.pi},  # ② 주차장 쪽을 봄
        {"x": parking[0], "y": parking[1], "yaw": math.pi},  # ③ 주차장 도착
        {"x": parking[0], "y": parking[1], "yaw": 0.0},      # ④ 180° 돌아섬
    ]
    status = None
    for pose in poses:
        py_trees.blackboard.Blackboard.set(Keys.ROBOT_POSE, pose)
        status = tick(root)
    py_trees.blackboard.Blackboard.set(Keys.IS_DOCKED, docked)
    for _ in range(3):
        status = tick(root)
        if py_trees.blackboard.Blackboard.get(Keys.CURRENT_MODE) != "RETURNING":
            break        # 전이가 일어난 tick 을 본다 — 더 돌면 IsMode 가 떨어진다
    return status


def test_returning_docked_to_charging(seed, read, tick):
    """마지막 단계(AlignDock)가 **실제 도킹 확인**(is_docked)을 요구한다.

    이 확인을 빼면 로봇이 충전소에 닿지도 않은 채 CHARGING 을 선언한다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    status = _walk_the_steps(_returning(), tick, seed, docked=True)
    assert status == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_returning_without_dock_confirmation_stays_returning(seed, read, tick):
    """도착만으로는 부족하다 — is_docked 가 없으면 CHARGING 으로 안 넘어간다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    status = _walk_the_steps(_returning(), tick, seed, docked=False)
    assert status == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "RETURNING"


def test_returning_step_failure_never_returns_failure(seed, tick):
    """Parallel 은 자식 하나가 FAILURE 면 즉시 실패한다. 그러면 형제 FaultDetected 가
    fault 를 ERROR 로 바꿀 tick 조차 없이 브랜치가 죽는다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING"})       # ROBOT_POSE 없음 → 도착 판정 불가
    root = _returning(return_entrance=FakeDriver(["failure"]))
    assert tick(root) != Status.FAILURE


def test_returning_retries_before_faulting(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "RETURNING"})
    root = _returning(return_entrance=FakeDriver(["failure"] * 5))
    tick(root)          # 1회차: goal 을 낸다(아직 poll 안 함)
    tick(root)          # 2회차: poll → failure → 흡수되어 재시도
    assert read(Keys.DOCK_RETRY_COUNT) == 1
    assert read(Keys.FAULT) is False


def test_returning_reaches_error_when_retries_exhausted(seed, read, tick):
    """재시도를 다 쓰면 fault 를 세우고 **RUNNING** 을 유지한다 — 같은 tick 에
    형제 watchdog 이 그 fault 를 보고 ERROR 로 보낸다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING"})
    root = _returning(return_entrance=FakeDriver(["failure"] * 20))
    status = None
    for _ in range(PARAMS["returning"]["dock_retry_max"] * 2 + 2):
        status = tick(root)
        if read(Keys.CURRENT_MODE) == "ERROR":
            break            # 전이가 일어난 그 tick 을 본다 (더 돌면 IsMode 가 떨어진다)
    assert read(Keys.FAULT) is True
    assert status == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_returning_ignores_stop_request(seed, read, tick):
    """Below 15% and away from the charger — stopping here means going flat."""
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.LAST_COMMAND: "stop_request"})
    tick(_returning())
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
