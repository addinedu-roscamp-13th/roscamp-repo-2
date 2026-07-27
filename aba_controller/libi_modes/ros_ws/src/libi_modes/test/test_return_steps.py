"""복귀 5단계 leaf 와 실패 흡수 데코레이터.

`Parallel` 은 자식 하나가 FAILURE 를 내면 **정책과 무관하게** 즉시 실패한다. 그래서
단계 하나가 실패해도 시퀀스 밖으로 FAILURE 가 새어 나가면 안 된다 — 형제
`FaultDetected` 가 그 fault 를 ERROR 전이로 바꿀 tick 조차 없이 브랜치가 죽는다.
"""
import math

import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.return_steps import (
    AbsorbFailure, AlignDock, AlreadyDocked, create_return_steps, wrap_angle,
)

from .fakes import FakeDriver, FakeYawDriver

ENTRANCE = (0.6, 0.0)
PARKING = (0.0, 0.0)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _steps(clock=None, **drivers):
    d = dict(entrance_driver=FakeDriver(), dock_driver=FakeDriver(),
             rotate_driver=FakeYawDriver())
    d.update(drivers)
    return create_return_steps(
        entrance_xy=ENTRANCE, parking_xy=PARKING,
        tolerance=0.05, resend_sec=10.0, timeout_sec=60.0,
        yaw_tolerance_rad=0.15, retry_max=3, dock_confirm_sec=90.0,
        now_fn=clock or _Clock(), **d)


def _tick(node):
    node.setup_with_descendants()
    node.initialise()
    return node.tick_once() or node.status


# ── 순서 ────────────────────────────────────────────────────────────────────

def test_five_steps_in_order():
    names = [s.decorated.name for s in _steps()]
    assert names == ["GoToParkingEntrance", "FaceParking", "GoToParking",
                     "TurnAround", "AlignDock"]


def test_every_step_is_failure_absorbing():
    """하나라도 안 감싸면 그 단계 실패가 Parallel 을 죽인다."""
    assert all(isinstance(s, AbsorbFailure) for s in _steps())


def test_face_and_align_are_separate_leaves():
    """ArUco 로직만 나중에 갈아끼우도록 자리를 분리해 둔다."""
    steps = _steps()
    assert steps[1].decorated is not steps[4].decorated


# ── 실패 흡수 ────────────────────────────────────────────────────────────────

class _Always(py_trees.behaviour.Behaviour):
    def __init__(self, status):
        super().__init__(name=f"Always[{status}]")
        self._status = status

    def update(self):
        return self._status


def test_failure_becomes_running(seed, tick):
    seed()
    node = AbsorbFailure(_Always(Status.FAILURE), retry_max=3)
    assert tick(node) == Status.RUNNING


def test_success_passes_through(seed, tick):
    seed()
    assert tick(AbsorbFailure(_Always(Status.SUCCESS), retry_max=3)) == Status.SUCCESS


def test_running_passes_through(seed, tick):
    seed()
    assert tick(AbsorbFailure(_Always(Status.RUNNING), retry_max=3)) == Status.RUNNING


def test_retry_count_is_published(seed, read, tick):
    seed()
    node = AbsorbFailure(_Always(Status.FAILURE), retry_max=3)
    tick(node)
    assert read(Keys.DOCK_RETRY_COUNT) == 1


def test_fault_only_after_retries_exhausted(seed, read, tick):
    seed()
    node = AbsorbFailure(_Always(Status.FAILURE), retry_max=3)
    node.setup_with_descendants()
    node.initialise()
    for i in range(3):
        node.tick_once()
        if i < 2:
            assert read(Keys.FAULT) is not True, f"{i+1}회차에 이미 fault"
    assert read(Keys.FAULT) is True


def test_still_running_after_fault(seed, tick):
    """소진돼도 FAILURE 를 내면 안 된다 — Parallel 이 먼저 죽는다."""
    seed()
    node = AbsorbFailure(_Always(Status.FAILURE), retry_max=1)
    assert tick(node) == Status.RUNNING


# ── 도킹 확인 ───────────────────────────────────────────────────────────────

def test_align_dock_waits_for_confirmation(seed, tick):
    """즉시 SUCCESS 를 내면 로봇이 충전소에 닿지도 않은 채 CHARGING 을 선언한다."""
    seed(**{Keys.IS_DOCKED: False})
    assert tick(AlignDock(90.0, _Clock())) == Status.RUNNING


def test_align_dock_succeeds_when_docked(seed, tick):
    seed(**{Keys.IS_DOCKED: True})
    assert tick(AlignDock(90.0, _Clock())) == Status.SUCCESS


def test_align_dock_times_out(seed):
    seed(**{Keys.IS_DOCKED: False})
    clock = _Clock()
    node = AlignDock(10.0, clock)
    node.setup()
    node.initialise()
    node.tick_once()
    clock.t = 20.0
    node.tick_once()
    assert node.status == Status.FAILURE


def test_already_docked_short_circuits(seed, tick):
    """충전소에 놓인 채 부팅하면 입구까지 나갔다 되돌아오면 안 된다."""
    seed(**{Keys.IS_DOCKED: True})
    assert tick(AlreadyDocked()) == Status.SUCCESS


def test_not_docked_falls_through(seed, tick):
    seed(**{Keys.IS_DOCKED: False})
    assert tick(AlreadyDocked()) == Status.FAILURE


# ── 주행 단계 ───────────────────────────────────────────────────────────────

def test_goal_step_sends_then_waits(seed, tick):
    """명령 수락(ack)을 도착으로 치지 않는다 — send_nav_goal 은 완료를 안 기다린다."""
    seed(**{Keys.ROBOT_POSE: {"x": 5.0, "y": 5.0}})
    driver = FakeDriver(["success"])
    step = _steps(entrance_driver=driver)[0]
    assert tick(step) == Status.RUNNING
    assert driver.started is True


def test_goal_step_succeeds_on_real_arrival(seed, tick):
    seed(**{Keys.ROBOT_POSE: {"x": ENTRANCE[0], "y": ENTRANCE[1]}})
    assert tick(_steps()[0]) == Status.SUCCESS


def test_goal_step_without_pose_never_claims_arrival(seed, tick):
    """위치를 모르는 것을 도착으로 치면 안 된다."""
    seed()
    assert tick(_steps()[0]) == Status.RUNNING


# ── 회전 단계 ───────────────────────────────────────────────────────────────

def test_face_parking_aims_at_the_parking_spot(seed):
    """입구에서 주차장(-x 방향)을 보므로 목표 yaw 는 pi 근처다."""
    seed(**{Keys.ROBOT_POSE: {"x": ENTRANCE[0], "y": ENTRANCE[1], "yaw": 0.0}})
    rot = FakeYawDriver()
    step = _steps(rotate_driver=rot)[1]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()
    assert abs(abs(rot.last_yaw) - math.pi) < 1e-6


def test_turn_around_targets_the_opposite_heading(seed):
    seed(**{Keys.ROBOT_POSE: {"x": PARKING[0], "y": PARKING[1], "yaw": 0.0}})
    rot = FakeYawDriver()
    step = _steps(rotate_driver=rot)[3]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()
    assert abs(abs(rot.last_yaw) - math.pi) < 1e-6


def test_yaw_target_is_fixed_once(seed):
    """매 tick 다시 계산하면 돌면서 목표도 따라 움직여 영원히 안 닿는다."""
    seed(**{Keys.ROBOT_POSE: {"x": PARKING[0], "y": PARKING[1], "yaw": 0.0}})
    rot = FakeYawDriver()
    step = _steps(rotate_driver=rot)[3]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()
    first = rot.last_yaw
    py_trees.blackboard.Blackboard.set(
        Keys.ROBOT_POSE, {"x": PARKING[0], "y": PARKING[1], "yaw": 1.0})
    step.tick_once()
    assert rot.last_yaw == first


def test_yaw_step_succeeds_within_tolerance(seed, tick):
    seed(**{Keys.ROBOT_POSE: {"x": PARKING[0], "y": PARKING[1], "yaw": math.pi}})
    step = _steps()[3]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()                       # 목표는 0(=pi 반대) 근처
    py_trees.blackboard.Blackboard.set(
        Keys.ROBOT_POSE, {"x": PARKING[0], "y": PARKING[1], "yaw": 0.0})
    step.tick_once()
    assert step.status == Status.SUCCESS


def test_rotate_driver_not_called_without_pose(seed):
    """좌표 없이 goal 을 내면 실행 층이 args['x'] 에서 죽는다."""
    seed()
    rot = FakeYawDriver()
    step = _steps(rotate_driver=rot)[1]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()
    assert rot.started is False


# ── 각도 감기 ───────────────────────────────────────────────────────────────

def test_wrap_angle_keeps_the_short_way():
    """안 감으면 179° 와 -179° 가 358° 차이로 보인다."""
    assert abs(wrap_angle(math.radians(358))) < math.radians(3)
    assert abs(wrap_angle(-math.radians(358))) < math.radians(3)
