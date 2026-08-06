"""One test per edge of the transition box, plus the edges that must NOT exist."""
import math

import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common import undock
from libi_modes.branches import (
    charging, error, idle, interacting, patrol, returning, security_patrol, working,
)
from test.fakes import PARAMS, FakeDoneDriver, FakeDriver, all_drivers


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


def _gate():
    """도킹 탈출 게이트 대역. 세 주행 브랜치가 **필수**로 받는다.

    기본값을 안 준 이유: 배선을 빠뜨리면 그 경로로 나갈 때 nav2 가 "경로 없음"으로
    실패하는데 증상이 도킹과 멀리 떨어져 나타난다. 조립 단계에서 터지는 편이 낫다.
    """
    return undock.create(FakeDriver(), distance_m=0.06, timeout_sec=8.0,
                         retry_max=3, now_fn=lambda: 0.0)


# ── PATROL ────────────────────────────────────────────────────────────────────

_PATROLLING = {Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
               Keys.ACTIVE_COMMAND: "navigate",
               Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
               Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}}


def test_patrol_keeps_driving(seed, read, tick):
    seed(**_PATROLLING)
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver, undock_gate=_gate())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert driver.started


def test_patrol_nav_never_self_completes(seed, tick):
    """Even a driver reporting success means "lap done" — patrol is endless."""
    seed(**_PATROLLING)
    assert tick(patrol.create(PARAMS, FakeDriver(["success"]), undock_gate=_gate())) == Status.RUNNING


def test_patrol_waits_between_nodes_without_failing(seed, read, tick):
    """다음 노드 허가를 기다리는 동안에도 순회 브랜치는 살아 있어야 한다.

    관제(fleet_node)가 노드를 하나씩 허가하므로 명령이 비는 순간이 정상적으로 생긴다.
    그때 FAILURE 를 내면 Parallel 이 무너져 브랜치가 매 tick 재진입하고, 주행이
    처음부터 다시 시작돼 로봇이 제자리에서 덜컹거린다.
    """
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0})   # 명령 없음
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver, undock_gate=_gate())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert not driver.started, "목적지도 없이 nav2 를 부르면 안 된다"


def test_patrol_arrival_is_not_the_end(seed, read, tick):
    """한 노드에 도착해도 순회는 계속된다 — 도착은 '한 노드 지났다'는 뜻이다."""
    seed(**{**_PATROLLING, Keys.ROBOT_POSE: {"x": 1.0, "y": 0.0}})      # 이미 목적지
    assert tick(patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "PATROL"
    assert read(Keys.ACTIVE_COMMAND) is None, "다음 노드를 받으려면 슬롯을 비운다"


def test_patrol_low_battery_returns_and_stops_motors(seed, read, tick):
    seed(**{**_PATROLLING, Keys.BATTERY_PERCENT: 10.0})
    driver = FakeDriver()
    assert tick(patrol.create(PARAMS, driver, undock_gate=_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "RETURNING"
    assert driver.stopped, "motors must be halted before the transition"


def test_patrol_task_assigned_to_working(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "task_assigned"})
    assert tick(patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_patrol_ui_touch_to_interacting(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "ui_touch"})
    assert tick(patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "INTERACTING"


def test_patrol_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "stop_request"})
    assert tick(patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


# ── SECURITY_PATROL ───────────────────────────────────────────────────────────

def test_security_patrol_keeps_patrolling(seed, read, tick):
    """야간 순찰은 1바퀴로 끝나지 않는다 — 한 노드에 도착해도 계속 순찰(RUNNING)하며
    IDLE 로 스스로 나가지 않는다(PATROL 과 같은 지속 순찰). 그래서 야간 내내 상태를 문다."""
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.ACTIVE_COMMAND: "navigate",
            Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
            Keys.ROBOT_POSE: {"x": 1.0, "y": 0.0}})       # 이미 목적지
    assert tick(security_patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"
    assert read(Keys.ACTIVE_COMMAND) is None, "다음 노드를 받으려면 슬롯을 비운다"


def test_security_patrol_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "stop_request"})
    assert tick(security_patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_security_patrol_ignores_task_assignment(seed, read, tick):
    """Night duty is not interruptible by day work."""
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "task_assigned"})
    assert tick(security_patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"


def test_security_patrol_ignores_ui_touch(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "ui_touch"})
    assert tick(security_patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"


def test_security_patrol_low_battery_returns(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 10.0})
    assert tick(security_patrol.create(PARAMS, FakeDriver(), undock_gate=_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "RETURNING"


# ── INTERACTING ───────────────────────────────────────────────────────────────

def test_interacting_holds_session(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.UI_LAST_TOUCH_AT: 0.0})
    assert tick(interacting.create(PARAMS, clock=lambda: 5.0)) == Status.RUNNING


def test_interacting_timeout_to_patrol(seed, read, tick):
    """20초 동안 아무도 안 만지면 PATROL 로 돌아간다.

    ⚠️ 시계를 **흐르게** 해야 한다. 예전에는 `clock=lambda: 25.0` 한 방으로 성공을
    기대했는데, `UiSessionTimer.initialise` 가 **진입 시각을 latch** 하도록 바뀌면서
    (진입하자마자 튕기던 버그의 수정) 그 방식은 elapsed 가 항상 0 이다. 고정 시계로
    타임아웃을 시험하는 것은 그 수정 자체를 되돌리라는 요구가 된다.
    """
    now = [100.0]
    seed(**{Keys.CURRENT_MODE: "INTERACTING", Keys.UI_LAST_TOUCH_AT: 0.0})
    root = interacting.create(PARAMS, clock=lambda: now[0])
    assert tick(root) == Status.RUNNING          # 진입 tick — 여기서 진입 시각이 latch 된다
    now[0] += 25.0
    assert tick(root) == Status.SUCCESS
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
    assert tick(working.create(PARAMS, nav, arm, undock_gate=_gate(), clock=lambda: 1.0)) == Status.RUNNING
    assert nav.started and not arm.started


def test_working_rejects_navigate_without_a_target(seed, read, tick):
    """목적지 없는 주행 명령은 붙들지 않고 놓는다.

    붙들고 RUNNING 으로 있으면 로봇이 아무 데도 안 가면서 "주행 중"으로 보인다 —
    그게 정확히 이 프로젝트에서 시간을 가장 많이 버린 실패 모양이다.
    """
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    nav, arm = FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, undock_gate=_gate(), clock=lambda: 1.0))
    assert not nav.started
    assert read(Keys.ACTIVE_COMMAND) is None, "실행할 수 없는 명령은 슬롯을 비워야 한다"


def test_working_dispatches_perform_action_to_arm(seed, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "perform_action"})
    nav, arm = FakeDriver(), FakeDriver()
    tick(working.create(PARAMS, nav, arm, undock_gate=_gate(), clock=lambda: 1.0))
    assert arm.started and not nav.started


def test_working_waits_with_no_command(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 0.0})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: 1.0)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_command_timeout_to_error(seed, read, tick):
    """WORKING has no battery exit, so without this the robot is stuck for good."""
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 0.0})
    now = {"t": 0.0}
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: now["t"])
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
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate())   # real time.monotonic
    assert tick(root, times=3) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_timeout_window_resets_on_each_command(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.COMMAND_RECEIVED_AT: 150.0})
    now = {"t": 200.0}
    root = working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: now["t"])
    assert tick(root) == Status.RUNNING, "only 50s since the last command"
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_timeout_does_not_fire_while_executing(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate",
            Keys.COMMAND_RECEIVED_AT: 0.0})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: 200.0)) == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_working_task_done_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: "task_done"})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: 1.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_working_task_failed_to_patrol(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: "task_failed"})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: 1.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "PATROL"


def test_working_stop_request_to_idle(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.LAST_COMMAND: "stop_request"})
    assert tick(working.create(PARAMS, FakeDriver(), FakeDriver(), undock_gate=_gate(), clock=lambda: 1.0)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_working_clears_active_command_when_done(seed, read, tick):
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    tick(working.create(PARAMS, FakeDriver(["success"]), FakeDriver(), undock_gate=_gate(), clock=lambda: 1.0))
    assert read(Keys.ACTIVE_COMMAND) is None, "next command can't be dispatched otherwise"


# ── RETURNING ─────────────────────────────────────────────────────────────────
# [2026-07-27] 한 leaf(ReturnNavigation) → 5단계 시퀀스로 바뀌었다.
# [2026-07-30] 뒷캠 ArUco 정밀 주차로 재편했다.
# [2026-08-03] ④가 센서 중립이 됐다 — `dock_sensor` 가 ArUco/라이다 중 고른다.
#   GoToParkingEntrance → FaceApproachYaw → ReleaseNav → DockApproach → DockNudge → DockSettle
#   없앤 것: GoToParking(nav2 로 주차장 정점) · TurnAround(180°)
# 팔 홈복귀는 없앴다(이 로봇에 팔이 없다 — 사용자 결정).


class _Clock:
    """앞으로만 가는 시험용 시계. `DockSettle` 이 시간으로 넘어가므로 필요하다."""

    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t


def _returning(clock=None, **over):
    """5단계 복귀 브랜치. 좌표·드라이버를 갈아끼울 수 있게 감싼다."""
    d = all_drivers()
    d.update(over)
    return returning.create(
        PARAMS,
        entrance_driver=d["return_entrance"],
        rotate_driver=d["return_rotate"],
        nav_release_driver=d["return_nav_release"],
        dock_driver=d["return_dock"],
        back_cam_driver=d["return_back_cam"],
        nudge_driver=d["return_nudge"],
        entrance_xy=d["return_entrance_xy"],
        entrance_yaw=d["return_entrance_yaw"],
        clock=clock or _Clock())


def test_returning_drives_to_the_entrance_first(seed, tick):
    """5단계의 첫 동작은 주차장 **입구** 주행이다."""
    entrance = FakeDriver()
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.ROBOT_POSE: {"x": 5.0, "y": 5.0}})
    tick(_returning(return_entrance=entrance))
    assert entrance.started is True


def _walk_the_steps(root, tick, clock):
    """각 단계의 목표에 로봇을 실제로 데려다 놓으며 시퀀스를 끝까지 민다.

    도착 판정이 **실좌표 거리**라, pose 를 안 옮기면 첫 단계에서 영원히 RUNNING 이다
    (그게 이 설계의 요점이다 — 명령 수락을 도착으로 치지 않는다).

    ③ReleaseNav·④DockApproach·⑤DockNudge 는 대역 드라이버가 즉시 성공하므로 pose
    조작이 없다. ⑥DockSettle 만 시계를 요구한다."""
    entrance = (0.6, 0.0)
    poses = [
        {"x": entrance[0], "y": entrance[1], "yaw": math.pi},  # ① 입구 도착 (자세는 아직)
        {"x": entrance[0], "y": entrance[1], "yaw": 0.0},      # ② 접근 자세로 돌아섬
    ]
    status = None
    for pose in poses:
        py_trees.blackboard.Blackboard.set(Keys.ROBOT_POSE, pose)
        status = tick(root)
    return status


def test_returning_reaches_charging_after_the_settle(seed, read, tick):
    """③④⑤를 지나 ⑥ 안정화 대기가 끝나면 CHARGING 을 선언한다."""
    clock = _Clock()
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    root = _returning(clock=clock)
    _walk_the_steps(root, tick, clock)
    clock.t += PARAMS["returning"]["settle_sec"]
    status = None
    for _ in range(3):
        status = tick(root)
        if read(Keys.CURRENT_MODE) != "RETURNING":
            break        # 전이가 일어난 tick 을 본다 — 더 돌면 IsMode 가 떨어진다
    assert status == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_returning_holds_through_the_settle(seed, read, tick):
    """안정화 대기가 안 끝났으면 CHARGING 으로 안 넘어간다.

    즉시 넘기면 개루프 후진의 관성이 남은 채로 "충전 중"이 선언된다."""
    clock = _Clock()
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    root = _returning(clock=clock)
    status = _walk_the_steps(root, tick, clock)
    assert status == Status.RUNNING
    assert read(Keys.CURRENT_MODE) == "RETURNING"


def test_returning_runs_dock_then_nudge_in_order(seed, tick):
    """④가 성공하기 전에는 ⑤가 시작되지 않는다.

    두 단계가 겹치면 로봇이 아직 정밀 도킹 접근 중인데 개루프 후진이 겹쳐
    거리가 통째로 틀어진다. 순서가 이 재편의 전부다."""
    dock, nudge = FakeDriver(["running", "success"]), FakeDoneDriver()
    clock = _Clock()
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    root = _returning(clock=clock, return_dock=dock, return_nudge=nudge)
    _walk_the_steps(root, tick, clock)
    assert dock.started is True
    assert nudge.started is False, "정밀 도킹 접근이 끝나기 전에 후진이 시작됐다"
    tick(root)                       # ④ success → ⑤ 시작
    assert nudge.started is True


def test_returning_releases_nav_before_the_dock_approach(seed, tick):
    """nav2 목표를 놓기 전에 정밀 도킹이 시작되면, 죽은 입구 goal 이 그 접근과
    바퀴를 두고 다툰다(codex 리뷰 2026-07-30)."""
    release, dock = FakeDriver(["running", "success"]), FakeDriver()
    clock = _Clock()
    seed(**{Keys.CURRENT_MODE: "RETURNING", Keys.IS_DOCKED: False})
    root = _returning(clock=clock, return_nav_release=release, return_dock=dock)
    _walk_the_steps(root, tick, clock)
    assert release.started is True
    assert dock.started is False, "nav2 를 놓기 전에 정밀 도킹 접근이 시작됐다"


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
    for _ in range(PARAMS["working"].get("recovery_retry_max", 3) + 1):
        tick(root)      # 내부 watchdog 재시도 소진 뒤 AbsorbFailure가 흡수
    assert read(Keys.DOCK_RETRY_COUNT) == 1
    assert read(Keys.FAULT) is False


def test_returning_reaches_error_when_retries_exhausted(seed, read, tick):
    """재시도를 다 쓰면 fault 를 세우고 **RUNNING** 을 유지한다 — 같은 tick 에
    형제 watchdog 이 그 fault 를 보고 ERROR 로 보낸다."""
    seed(**{Keys.CURRENT_MODE: "RETURNING"})
    root = _returning(return_entrance=FakeDriver(["failure"] * 20))
    status = None
    for _ in range(PARAMS["returning"]["dock_retry_max"] * 4 + 12):
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


# ── dock_sensor 전환 (2026-08-03) ──────────────────────────────────────────────

import pytest


@pytest.mark.parametrize("sensor,expect_nudge", [("lidar", 0.0), ("aruco", 0.03)])
def test_rollback_is_a_single_parameter(sensor, expect_nudge):
    """두 값을 같이 바꿔야 하는 계약은 조용한 실패의 씨앗이다.
    ⑤의 거리는 `dock_sensor` 하나에서 유도돼야 한다."""
    ret = {"nudge_distance_m": 0.03}
    nudge = 0.0 if sensor == "lidar" else float(ret.get("nudge_distance_m", 0.03))
    assert nudge == expect_nudge


def test_main_derives_nudge_distance_from_dock_sensor_in_source():
    """위 시험은 유도 **공식**만 시험 대상과 별개로 다시 계산해 맞춰 본다 —
    `main.py` 의 실제 코드는 한 글자도 보지 않는다. `main.py` 는 rclpy 없이 인스턴스화할
    수 없어(`FsmNode.__init__` 이 노드 생성자를 바로 부른다) 여기서 그 값을 계산해
    보는 시험을 못 쓴다. 그래서 실제 배선이 두 값으로 되돌아가도(거부된 설계) 이
    시험만 봐서는 초록이다 — 소스를 직접 대조해 그 간극을 메운다.

    브리프의 지적대로("Step 3b 의 코드를 눈으로 대조할 것") 눈이 아니라 AST 로 본다:
    `nudge_distance` 대입이 `dock_sensor == "lidar"` 삼항식이고, 참 분기가 정확히
    `0.0` 이며, `NudgeDriver(distance_m=...)` 가 그 변수를 그대로 받는지 확인한다.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "libi_modes" / "main.py").read_text()
    tree = ast.parse(src)

    assign = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "nudge_distance" for t in n.targets)),
        None)
    assert assign is not None, "main.py 에 nudge_distance 대입이 없다"
    assert isinstance(assign.value, ast.IfExp), (
        "nudge_distance 가 dock_sensor 삼항식으로 유도되지 않는다 — "
        "params.yaml 에 별도 값을 또 둔 것은 아닌지 확인할 것")

    cond = ast.unparse(assign.value.test)
    assert "dock_sensor" in cond and "lidar" in cond, \
        f"조건이 dock_sensor == 'lidar' 가 아니다: {cond}"
    assert ast.literal_eval(assign.value.body) == 0.0, \
        "라이다 분기가 정확히 0.0 이 아니다 — 되돌리기 트랩의 핵심 값"

    nudge_driver_kw = next(
        (kw.value for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
         and n.func.id == "NudgeDriver"
         for kw in n.keywords if kw.arg == "distance_m"),
        None)
    assert nudge_driver_kw is not None, "return_nudge 드라이버 생성부를 못 찾았다"
    assert (isinstance(nudge_driver_kw, ast.Name)
            and nudge_driver_kw.id == "nudge_distance"), (
        "NudgeDriver(distance_m=...) 가 위에서 유도한 nudge_distance 변수를 그대로 "
        "쓰지 않는다 — 다시 계산하면 유도가 두 갈래로 갈라진다")


def test_dock_leaf_is_sensor_neutral():
    """leaf 이름이 센서를 박아 두면, 라이다가 도는데 화면엔 ArUco 라고 뜬다."""
    from libi_modes.common.return_steps import create_return_steps
    from .fakes import FakeDoneDriver, FakeDriver, FakeYawDriver

    steps = create_return_steps(
        entrance_driver=FakeDriver(), rotate_driver=FakeYawDriver(),
        nav_release_driver=FakeDoneDriver(), dock_driver=FakeDoneDriver(),
        nudge_driver=FakeDoneDriver(), entrance_xy=(0.6, 0.0),
        tolerance=0.1, resend_sec=1.0, timeout_sec=30.0,
        yaw_tolerance_rad=0.15, retry_max=3)
    names = [s.decorated.name for s in steps]
    assert "DockApproach" in names
    assert not any("Aruco" in n for n in names)
