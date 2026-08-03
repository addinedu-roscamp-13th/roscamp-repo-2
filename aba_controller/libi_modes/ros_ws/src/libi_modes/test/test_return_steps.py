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
    AbsorbFailure, AlreadyDocked, DockSettle, _YawStep, create_return_steps, wrap_angle,
)

from .fakes import FakeDoneDriver, FakeDriver, FakeYawDriver

ENTRANCE = (0.6, 0.0)
#: 회전 단계 시험에서 로봇을 놓아 둘 아무 좌표. 회전은 위치와 무관하다.
SOMEWHERE = (0.0, 0.0)
#: 접근 자세(절대각). config/params.yaml `returning.approach_yaw_rad` 와 같은 뜻.
APPROACH_YAW = 0.0


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _steps(clock=None, **drivers):
    d = dict(entrance_driver=FakeDriver(), rotate_driver=FakeYawDriver(),
             nav_release_driver=FakeDoneDriver(),
             dock_driver=FakeDoneDriver(), nudge_driver=FakeDoneDriver())
    d.update(drivers)
    return create_return_steps(
        entrance_xy=ENTRANCE, approach_yaw=APPROACH_YAW,
        tolerance=0.05, resend_sec=10.0, timeout_sec=60.0,
        yaw_tolerance_rad=0.15, retry_max=3, settle_sec=1.0,
        now_fn=clock or _Clock(), **d)


def _tick(node):
    node.setup_with_descendants()
    node.initialise()
    return node.tick_once() or node.status


# ── 순서 ────────────────────────────────────────────────────────────────────

def test_six_steps_in_order():
    names = [s.decorated.name for s in _steps()]
    assert names == ["GoToParkingEntrance", "FaceApproachYaw", "ReleaseNav",
                     "DockApproach", "DockNudge", "DockSettle"]


def test_nav_is_released_before_the_external_docker_takes_over():
    """①은 x·y 만 보고 성공하고 성공한 단계는 stop 을 안 보낸다 — 입구 nav2 목표가 살아
    있는 채로 정밀 도킹 접근이 시작될 수 있다. 예전엔 `GoToParking` 이 새 goal 로 선점해
    줬는데 그 단계가 없어졌다. 그래서 넘기기 **직전에** 놓는 단계가 있어야 한다."""
    names = [s.decorated.name for s in _steps()]
    assert names.index("ReleaseNav") < names.index("DockApproach")


def test_every_step_is_failure_absorbing():
    """하나라도 안 감싸면 그 단계 실패가 Parallel 을 죽인다."""
    assert all(isinstance(s, AbsorbFailure) for s in _steps())


def test_dock_and_nudge_are_separate_leaves():
    """④는 `dock_sensor` 가 고른 드라이버가, ⑤는 여기가 한다. 한 leaf 로 합치면 어느
    쪽이 실패했는지 화면에서 안 보이고, 구현을 갈아끼울 자리도 사라진다."""
    steps = _steps()
    assert steps[3].decorated is not steps[4].decorated


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


# ── 안정화 대기 ─────────────────────────────────────────────────────────────
#
# [2026-07-30] `AlignDock`(is_docked 대기) → `DockSettle`(시간 대기). `is_docked` 는
# 주차장 정점 반경 0.12m 판정이라 ④⑤를 지난 뒤에는 이미 참이고, 그래서 아무것도
# 검증하지 못했다. 검증하는 척하는 게이트는 없느니만 못하다.

def test_dock_settle_holds_until_the_time_passes(seed, tick):
    """즉시 SUCCESS 를 내면 개루프 후진의 관성이 남은 채 CHARGING 이 선언된다."""
    seed()
    assert tick(DockSettle(1.0, _Clock())) == Status.RUNNING


def test_dock_settle_succeeds_after_the_wait(seed):
    seed()
    clock = _Clock()
    node = DockSettle(1.0, clock)
    node.setup()
    node.initialise()
    node.tick_once()
    clock.t = 1.5
    node.tick_once()
    assert node.status == Status.SUCCESS


def test_dock_settle_declares_the_dock(seed, read):
    """대기가 끝나면 **도킹을 선언한다** — `state_io` 가 이 값을 `/is_docked` 로 낸다.

    위치로 판정하던 `dock_confirm.py` 가 `pi.sh` 에서 빠져 아무도 그 토픽을 안 낸다.
    선언이 없으면 `UndockNotNeeded` 가 None 을 "도킹 아님"으로 읽어 탈출을 건너뛰고,
    벽에서 7cm 지점에서 nav2 가 경로를 못 낸다.
    """
    seed()
    clock = _Clock()
    node = DockSettle(1.0, clock)
    node.setup()
    node.initialise()
    node.tick_once()
    assert read(Keys.DOCK_DECLARED) in (None, False)   # 대기 중에는 아직 아니다
    clock.t = 1.5
    node.tick_once()
    assert node.status == Status.SUCCESS
    assert read(Keys.DOCK_DECLARED) is True


def test_dock_settle_ignores_is_docked(seed, tick):
    """도킹 신호가 이미 참이어도 대기를 건너뛰지 않는다 — 그 신호는 위치 판정이다."""
    seed(**{Keys.IS_DOCKED: True})
    assert tick(DockSettle(1.0, _Clock())) == Status.RUNNING


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

def test_face_approach_yaw_turns_180_from_the_current_heading(seed):
    """[2026-07-30 사용자 지정] **지금 보고 있는 방향에서 반 바퀴**다.

    ①이 끝나면 로봇은 충전소를 바라보고 서 있다. 거기서 180° 돌면 등이 충전소를
    향하고, 그래야 뒷캠이 마커를 본다.

    고정 절대각도, 충전소 좌표 atan2 도 아니다 — 둘 다 실기에서 틀렸다
    (`_approach_yaw` 주석 참고). 위치와 무관하게 **입력 헤딩에만** 달려 있어야 한다."""
    for start, expect in ((math.pi, 0.0), (0.0, math.pi), (1.0, 1.0 - math.pi)):
        seed(**{Keys.ROBOT_POSE: {"x": 9.9, "y": -3.3, "yaw": start}})
        rot = FakeYawDriver()
        step = _steps(rotate_driver=rot)[1]
        step.setup_with_descendants()
        step.initialise()
        step.tick_once()
        assert abs(wrap_angle(rot.last_yaw - expect)) < 1e-6, f"{start} → {rot.last_yaw}"


def test_face_approach_yaw_needs_a_heading(seed):
    """yaw 를 모르면 반 바퀴가 어디인지 알 수 없다 — 회전 명령을 내면 안 된다."""
    seed(**{Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}})          # yaw 없음
    rot = FakeYawDriver()
    step = _steps(rotate_driver=rot)[1]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()
    assert rot.started is False


def test_yaw_target_is_fixed_once(seed):
    """매 tick 다시 계산하면 돌면서 목표도 따라 움직여 영원히 안 닿는다.

    지금 접근 각도는 상수라 이 결함이 안 드러난다. 그래도 `_YawStep` 자체의 성질이므로
    **pose 를 따라가는 yaw_fn** 으로 직접 시험한다 — 여기를 상수 전용으로 두면
    다음 사람이 pose 기반 각도를 붙였을 때 조용히 되살아난다."""
    seed(**{Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0, "yaw": 0.0}})
    rot = FakeYawDriver()
    step = _YawStep("Chasing", rot, lambda pose: pose["yaw"] + math.pi,
                    0.15, 60.0, _Clock())
    step.setup()
    step.initialise()
    step.tick_once()
    first = rot.last_yaw
    py_trees.blackboard.Blackboard.set(
        Keys.ROBOT_POSE, {"x": 0.0, "y": 0.0, "yaw": 1.0})
    step.tick_once()
    assert rot.last_yaw == first


def test_yaw_step_succeeds_within_tolerance(seed, tick):
    seed(**{Keys.ROBOT_POSE: {"x": SOMEWHERE[0], "y": SOMEWHERE[1], "yaw": math.pi}})
    step = _steps()[1]
    step.setup_with_descendants()
    step.initialise()
    step.tick_once()                       # 목표는 APPROACH_YAW(0)
    py_trees.blackboard.Blackboard.set(
        Keys.ROBOT_POSE, {"x": SOMEWHERE[0], "y": SOMEWHERE[1], "yaw": APPROACH_YAW})
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


# ── 선점 시 주행 취소 ────────────────────────────────────────────────────────

def test_goal_step_stops_the_drive_when_preempted(seed):
    """상위가 이 단계를 끊었는데 nav2 가 계속 달리면, 화면상 ERROR 인 로봇이
    주차장으로 계속 간다."""
    seed(**{Keys.ROBOT_POSE: {"x": 5.0, "y": 5.0}})
    driver = FakeDriver()
    step = _steps(entrance_driver=driver)[0].decorated
    step.setup()
    step.initialise()
    step.tick_once()
    step.stop(Status.INVALID)
    assert driver.stopped is True


def test_goal_step_does_not_stop_when_it_succeeded(seed):
    """정상 종료까지 stop 을 보내면 다음 단계의 주행을 끊을 수 있다."""
    seed(**{Keys.ROBOT_POSE: {"x": ENTRANCE[0], "y": ENTRANCE[1]}})
    driver = FakeDriver()
    step = _steps(entrance_driver=driver)[0].decorated
    step.setup()
    step.initialise()
    step.tick_once()
    step.stop(Status.SUCCESS)
    assert driver.stopped is False


def test_yaw_step_stops_the_rotation_when_preempted(seed):
    seed(**{Keys.ROBOT_POSE: {"x": SOMEWHERE[0], "y": SOMEWHERE[1], "yaw": math.pi}})
    rot = FakeYawDriver()
    step = _steps(rotate_driver=rot)[1].decorated
    step.setup()
    step.initialise()
    step.tick_once()
    step.stop(Status.INVALID)
    assert rot.stopped is True
