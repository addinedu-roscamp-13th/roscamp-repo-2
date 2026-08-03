"""이동 실행기. ROS 없이 발행된 twist 와 가짜 pose 적분 모형만 본다.

⚠️ 시간이 아니라 **odom(pose_fn)** 으로 도달을 판정한다(revision.md R1). 시계 대신
가짜 로봇을 둔다: 발행된 (lin, ang) 을 받아 `x += lin*dt*cos(yaw)`, `yaw += ang*dt` 로
적분하고, `pose_fn` 은 그 누적 상태를 돌려준다. `test_speed_change_does_not_change_the_distance`
가 시간 기반으로의 되돌림을 잡는 시험이다 — 속도를 바꿔도 도달 거리가 같아야 한다.
"""
import math

from app.core.backup_runner import MoveExecutor, record_return_targets
from app.core.shelf_dock import unlock_payload
from app.shelf.geometry import Move, wrap_pi


class FakeRobot:
    """twist 를 받아 pose 를 적분하는 가짜 차동구동 로봇. dt 는 시뮬레이션 스텝일 뿐,
    MoveExecutor 는 이 값을 전혀 모른다 — 그저 pose_fn() 의 (x, y, yaw) 변화만 본다."""

    def __init__(self, dt: float = 0.01):
        self.dt = dt
        self.x = self.y = self.yaw = 0.0

    def publish(self, lin: float, ang: float) -> None:
        self.x += lin * self.dt * math.cos(self.yaw)
        self.y += lin * self.dt * math.sin(self.yaw)
        self.yaw = wrap_pi(self.yaw + ang * self.dt)

    def pose(self):
        return (self.x, self.y, self.yaw)


def _drive_steps(moves, cancel=None, max_ticks=20000, **kw):
    """`run_steps` 를 끝까지(최종 상태 튜플까지) 돌리고 (sent, robot, result) 를 돌려준다."""
    robot = FakeRobot()
    sent = []

    def publish(lin, ang):
        sent.append((lin, ang))
        robot.publish(lin, ang)

    ex = MoveExecutor(publish_twist=publish, pose_fn=robot.pose, **kw)
    gen = ex.run_steps(moves, cancel=cancel)
    result = None
    for _ in range(max_ticks):
        item = next(gen, "__EXHAUSTED__")
        if item == "__EXHAUSTED__":
            break
        if isinstance(item, tuple):
            result = item
    return sent, robot, result


def test_drive_stops_within_tolerance_of_the_target_distance():
    sent, robot, result = _drive_steps([Move("drive", 0.2)],
                                       drive_speed=0.08, drive_tol=0.005)
    assert result == (True, "")
    assert abs(robot.x - 0.2) <= 0.005 + 1e-9
    assert sent[-1] == (0.0, 0.0)


def test_turn_stops_within_tolerance_of_the_target_angle():
    sent, robot, result = _drive_steps([Move("turn", 1.0)],
                                       turn_speed=0.4, turn_tol=0.02)
    assert result == (True, "")
    assert abs(robot.yaw - 1.0) <= 0.02 + 1e-9
    assert sent[-1] == (0.0, 0.0)


def test_speed_change_does_not_change_the_distance():
    """시간 기반이면 이 시험이 빨개진다 — 되돌림 확인은 이걸로 한다."""
    fast_sent, fast_robot, fast_result = _drive_steps(
        [Move("drive", 0.2)], drive_speed=0.08, drive_tol=0.005)
    slow_sent, slow_robot, slow_result = _drive_steps(
        [Move("drive", 0.2)], drive_speed=0.04, drive_tol=0.005)

    assert fast_result == (True, "") and slow_result == (True, "")
    assert abs(fast_robot.x - slow_robot.x) <= 0.005
    # 느린 쪽이 같은 거리를 가려면 더 많은 tick(=더 많은 publish) 이 필요하다 —
    # 그게 곧 "속도가 바뀌어도 거리가 바뀌지 않는다" 는 것의 증거다.
    assert len(slow_sent) > len(fast_sent)


def test_cancel_returns_failure():
    """취소는 실패다 — `run()` 이 성공을 돌려주면 상위 BT 가 취소된 도킹을 성공으로 읽는다."""
    robot = FakeRobot()
    sent = []

    def publish(lin, ang):
        sent.append((lin, ang))
        robot.publish(lin, ang)

    ex = MoveExecutor(publish_twist=publish, pose_fn=robot.pose,
                      drive_speed=0.08, drive_tol=0.005)
    ok, why = ex.run([Move("drive", 10.0)], cancel=lambda: len(sent) >= 3)
    assert (ok, why) == (False, "canceled")


def test_cancel_publishes_a_stop():
    robot = FakeRobot()
    sent = []

    def publish(lin, ang):
        sent.append((lin, ang))
        robot.publish(lin, ang)

    ex = MoveExecutor(publish_twist=publish, pose_fn=robot.pose,
                      drive_speed=0.08, drive_tol=0.005)
    ex.run([Move("drive", 10.0)], cancel=lambda: len(sent) >= 3)
    assert sent[-1] == (0.0, 0.0)
    assert len(sent) < 10, "취소 뒤에도 계속 밀어붙이면 취소가 아니다"


def test_every_plan_ends_stopped():
    sent, _robot, result = _drive_steps(
        [Move("turn", 0.5), Move("drive", 0.2), Move("turn", -0.3)],
        turn_speed=0.4, drive_speed=0.08)
    assert result == (True, "")
    assert sent[-1] == (0.0, 0.0)


def test_zero_length_move_is_skipped():
    sent, _robot, result = _drive_steps([Move("turn", 0.0), Move("drive", 0.0)])
    assert sent == [(0.0, 0.0)]
    assert result == (True, "")


def test_pose_fn_exception_still_publishes_a_stop():
    """스펙에 없는 여분의 안전망 시험 — pose_fn 이 도중에 죽어도 바퀴가 안 남는다."""
    sent = []
    calls = {"n": 0}

    def pose_fn():
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("odom 통신 오류")
        return (0.0, 0.0, 0.0)

    ex = MoveExecutor(publish_twist=lambda lin, ang: sent.append((lin, ang)),
                      pose_fn=pose_fn, drive_speed=0.08, drive_tol=0.005)
    try:
        ex.run([Move("drive", 10.0)])
    except RuntimeError:
        pass
    else:
        raise AssertionError("예외가 삼켜졌다 — 호출자가 실패를 못 본다")
    assert sent[-1] == (0.0, 0.0)


# ── shelf_dock.unlock_payload — Fix round 1: 새 토픽 대신 기존 /libi/node_block ──
#
# `node` 를 받아 `NodeBlockRegistry.set` 이 알아듣는 "즉시 해제" payload(ttl_sec=0)를
# 만드는 순수 함수만 여기서 본다. 실제 발행(ROS)은 `shelf_dock._run` 이 한다.

def test_unlock_payload_is_a_release():
    payload = unlock_payload({"shelf": "문학서가", "node": 5})
    assert payload == {"node": 5, "ttl_sec": 0.0, "reason": "shelf_dock"}


def test_missing_node_arg_does_not_publish_an_unlock():
    """`node` 가 없으면 `None` — 호출자가 이걸 보고 발행을 건너뛰고 경고를 남긴다."""
    assert unlock_payload({"shelf": "문학서가"}) is None
    assert unlock_payload({}) is None


def test_plan_failure_still_releases_the_lock():
    """`plan_dock` 이 실패해도 잠금 해제 발행이 **실제로** 나가야 한다.

    2026-08-04 리뷰 P2: 이전 버전은 `inspect.getsource` 로 호출 순서만 봤다 —
    발행 자체를 지워도 소스에 문자열만 남아 있으면 통과했다. 이번엔 가짜
    `publish_fn` 을 주입해 실제로 어떤 payload 가 나가는지 확인한다.

    `_release_lock_before_moving` 은 `plan_dock` 의 성공/실패를 애초에 모른다 —
    `_run` 이 그 결과를 보기 **전에** 이걸 한 번만 부르므로, "실패 경로에서
    빠뜨린다"는 종류의 결함이 구조적으로 재발할 수 없다.
    """
    from app.core.shelf_dock import _release_lock_before_moving

    published, warnings = [], []
    node, at = _release_lock_before_moving(
        {"shelf": "문학서가", "node": 7}, published.append, warnings.append)

    assert published == [{"node": 7, "ttl_sec": 0.0, "reason": "shelf_dock"}]
    assert node == 7 and at is not None
    assert warnings == []


def test_missing_node_releases_nothing_but_warns():
    from app.core.shelf_dock import _release_lock_before_moving

    published, warnings = [], []
    node, at = _release_lock_before_moving(
        {"shelf": "문학서가"}, published.append, warnings.append)

    assert published == []
    assert (node, at) == (None, None)
    assert len(warnings) == 1 and "node" in warnings[0]


# ── shelf_dock 프레임 신선도 — Fix round 3: 시계를 monotonic 으로 통일 (P0) ──────
#
# frame_tap 의 stamp 는 `time.monotonic()` 이다(`aba_ai_service/follower_perception/
# scripts/frame_tap.py` `write()`). `marker_dock.py` 는 그것과 같은 시계로 비교한다
# (`now = time.monotonic(); ... now - stamp > FRAME_STALE_SEC`). 예전 `shelf_dock`
# 은 `time.time()`(epoch) 과 비교해서 두 시계 기준점이 억 단위로 벌어져 **항상
# stale** 로 판정됐다 — 표식을 영영 못 보고 매번 frame_stale 로 실패했다.

def test_wait_for_fresh_frame_accepts_a_frame_within_the_stale_window():
    from app.core.shelf_dock import _wait_for_fresh_frame

    got = ("frame", 1, 100.0)
    frame = _wait_for_fresh_frame(
        read_tap_fn=lambda: got, now_fn=lambda: 100.2, sleep_fn=lambda s: None,
        deadline_sec=1.0, stale_sec=0.4)
    assert frame == "frame"


def test_wait_for_fresh_frame_gives_up_when_nothing_arrives():
    from app.core.shelf_dock import _wait_for_fresh_frame

    clock = {"t": 0.0}

    def now_fn():
        return clock["t"]

    def sleep_fn(s):
        clock["t"] += s

    frame = _wait_for_fresh_frame(
        read_tap_fn=lambda: None, now_fn=now_fn, sleep_fn=sleep_fn,
        deadline_sec=0.2, stale_sec=0.4)
    assert frame is None


def test_wait_for_fresh_frame_rejects_a_stamp_from_a_different_clock():
    """frame_tap 의 stamp 는 monotonic 이다. `now_fn` 에 epoch(`time.time`) 을
    주면(=예전 결함을 그대로 재현하면) 차이가 억 단위라 **항상 stale** 로
    판정돼야 한다 — 실측(진짜 두 시계를 그대로 씀, 가짜 시계가 아니다)."""
    import time as _time

    from app.core.shelf_dock import _wait_for_fresh_frame

    monotonic_stamp = _time.monotonic()      # frame_tap.write() 와 같은 시계
    got = ("frame", 1, monotonic_stamp)

    frame = _wait_for_fresh_frame(
        read_tap_fn=lambda: got, now_fn=_time.time, sleep_fn=lambda s: None,
        deadline_sec=0.01, stale_sec=0.4)
    assert frame is None, "epoch 와 monotonic 을 섞으면 항상 stale 이어야 정상이다"


def test_shelf_dock_run_reads_frames_with_the_monotonic_clock():
    """`_run` 의 PID 루프가 프레임 stamp 와 같은 monotonic 시계를 쓰는지."""
    import inspect

    from app.core import shelf_dock

    src = inspect.getsource(shelf_dock._run)
    assert "now = time.monotonic()" in src
    assert "now - stamp" in src
    assert "time.time()" not in src


# ── shelf_dock 카메라 선택 재발행 — Fix round 3 (P1) ────────────────────────────
#
# `PersonBlockGuard` 는 `active_command == "navigate"` 일 때만 `/libi/camera_select`
# 를 갱신한다. 도킹 중엔 `active_command` 가 `shelf_dock` 이라 아무도 앞캠을 안
# 잡는다 — `camera_sender` 는 선택 안 된 캠을 1.9Hz 로만 보므로 `FRAME_STALE_SEC`
# (0.4초) 판정에 늘 걸린다. `shelf_dock` 이 도킹 내내 직접 갱신해야 한다.

def test_camera_renew_interval_is_at_most_half_the_sender_expiry():
    """`libi_perception.config.CAMERA_SELECT_EXPIRY_SEC` 는 3.0초다. 그 절반보다
    뜸하면 만료 워치독이 "none" 으로 떨어뜨린 뒤에야 다음 요청이 와서 카메라가
    깜빡인다(`libi_modes/common/person_block.py` 의 `_CAMERA_RENEW_SEC` 주석과
    같은 근거)."""
    from app.core.shelf_dock import CAMERA_RENEW_SEC
    assert CAMERA_RENEW_SEC <= 1.5


def test_should_renew_camera_fires_first_then_rate_limits():
    from app.core.shelf_dock import _should_renew_camera

    assert _should_renew_camera(None, 100.0) is True          # 첫 호출은 무조건
    assert _should_renew_camera(100.0, 100.5) is False        # 0.5s < 1.0s 는 아직
    assert _should_renew_camera(100.0, 101.0) is True         # 1.0s 지나면 다시


# ── backup_runner preflight 취소 유실 — Fix round 3 (P0) ────────────────────────
#
# 좌표형 backup 은 `_read_current_pose()`(느릴 수 있는 `/amcl_pose` 대기) 를 끝낸
# **뒤에야** `_running`/세대를 잡았었다. 그 사이에 들어온 취소는 잡을 세대가 없어
# 사라지고, 다음 실제 주행이(상위가 이미 포기했는데) 시작됐다.

def test_cancel_during_preflight_is_not_lost():
    """pose 읽기 단계에서 취소가 들어오면 주행이 **시작되지 않아야** 한다."""
    from app.core import backup_runner

    drive_calls = []

    def fake_drive(moves, gen):
        drive_calls.append((moves, gen))
        return True, 200, {"backed": True}, ""

    def fake_read_pose():
        # preflight(=/amcl_pose 대기) 도중 상위가 취소를 보낸 상황을 흉내낸다.
        assert backup_runner.request_cancel() is True, (
            "이 시점엔 이미 _running 이 잡혀 있어야 한다 — 안 그러면 취소가 사라진다")
        return (0.0, 0.0, 0.0)

    ok, status, data, msg = backup_runner.run_backup(
        {"x": 1.0, "y": 1.0}, _read_pose_fn=fake_read_pose, _drive_fn=fake_drive)

    assert drive_calls == [], "취소됐는데 주행이 시작됐다"
    assert ok is False and status == 499

def test_fms_backup_follows_saved_checkpoints_in_reverse_order():
    """FMS가 명시적으로 `backup`을 보냈을 때 최종축→옆축 순서로 되돌아간다."""
    from app.core import backup_runner

    record_return_targets([(1.0, 0.0), (0.0, 0.0)])
    driven = []

    def fake_drive(moves, _gen):
        driven.extend(moves)
        return True, 200, {"backed": True}, ""

    ok, status, _data, _msg = backup_runner.run_backup(
        {}, _read_pose_fn=lambda: (2.0, 0.0, 0.0), _drive_fn=fake_drive)

    assert ok is True and status == 200
    assert [round(m.value, 6) for m in driven if m.kind == "drive"] == [1.0, 1.0]

