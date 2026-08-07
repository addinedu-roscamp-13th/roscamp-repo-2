"""이동 실행기. ROS 없이 발행된 twist 와 가짜 pose 적분 모형만 본다.

⚠️ 시간이 아니라 **odom(pose_fn)** 으로 도달을 판정한다(revision.md R1). 시계 대신
가짜 로봇을 둔다: 발행된 (lin, ang) 을 받아 `x += lin*dt*cos(yaw)`, `yaw += ang*dt` 로
적분하고, `pose_fn` 은 그 누적 상태를 돌려준다. `test_speed_change_does_not_change_the_distance`
가 시간 기반으로의 되돌림을 잡는 시험이다 — 속도를 바꿔도 도달 거리가 같아야 한다.
"""
import math

import pytest

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
    """FMS가 명시적으로 `backup`을 보냈을 때 최종축→옆축 순서로 되돌아간다.

    거리는 그대로 1m 씩이되 **부호가 음수**다 — 서가에서는 코가 아니라 등을 돌려
    후진으로 뺀다(`geometry.retreat_moves`).
    """
    from app.core import backup_runner

    record_return_targets([(1.0, 0.0), (0.0, 0.0)])
    driven = []

    def fake_drive(moves, _gen):
        driven.extend(moves)
        return True, 200, {"backed": True}, ""

    ok, status, _data, _msg = backup_runner.run_backup(
        {}, _read_pose_fn=lambda: (2.0, 0.0, 0.0), _drive_fn=fake_drive)

    assert ok is True and status == 200
    assert [round(m.value, 6) for m in driven if m.kind == "drive"] == [-1.0, -1.0]


# ── 서가에서 빠져나갈 때 꽁무늬가 서가를 안 쓸어야 한다 (2026-08-07 실기) ──────────
#
# 도킹이 끝나면 로봇은 서가와 **나란히** 선다(FINAL_YAW 180°, 서가는 SHELF_YAW ±90°
# 쪽 옆구리 3cm). 빠져나가려면 제자리 90° 회전을 피할 수 없으니, 남는 선택은
# **어느 쪽이 서가를 쓸고 지나가느냐**뿐이다.

@pytest.mark.parametrize("shelf_yaw", [1.5708, -1.5708])
def test_leaving_the_shelf_swings_the_tail_away_from_it(shelf_yaw):
    """빠져나가는 회전 내내 **꽁무늬가 서가 쪽을 향하지 않아야** 한다.

    ⚠️ 되돌림 감지용이다 — 코를 목표로 돌리는 `approach_moves` 로 되돌리면 꽁무늬가
       정확히 서가 방향에 가서 서므로 이 시험이 빨개진다.
    """
    from app.shelf.geometry import retreat_moves, wrap_pi

    final_yaw = 3.1416                      # shelf_dock.FINAL_YAW_RAD
    # 체크포인트는 서가 **반대편**(빠져나갈 방향)으로 0.2m.
    tx, ty = -0.2 * math.cos(shelf_yaw), -0.2 * math.sin(shelf_yaw)
    moves = retreat_moves(0.0, 0.0, final_yaw, tx, ty)

    turn = next(m for m in moves if m.kind == "turn")
    # 꽁무늬가 훑는 구간 = (시작 꽁무늬 방위) → (끝 꽁무늬 방위), 회전 부호를 따라.
    tail0 = wrap_pi(final_yaw + math.pi)
    for step in range(101):                 # 회전을 촘촘히 적분해 **경로 전체**를 본다
        tail = wrap_pi(tail0 + turn.value * step / 100.0)
        assert abs(wrap_pi(tail - shelf_yaw)) > math.radians(45), (
            f"꽁무늬가 서가 쪽({shelf_yaw:.3f})을 훑고 지나간다: {tail:.3f}")

    assert next(m for m in moves if m.kind == "drive").value < 0, "후진이어야 한다"


# ── map 절대 yaw 회전도 tick 마다 페이싱돼야 한다 (2026-08-07 실기: backup timeout) ──
#
# `pose_fn` 이 유일한 `time.sleep`·`spin_once` 지점이다(`_run` 의 pose_fn 주석).
# map 절대 분기가 그걸 안 부르면 자는 구간이 없어 4000 tick 을 1초 안에 태우고,
# 90° 회전(0.4rad/s = 실제 4초)은 시작하자마자 timeout 이 난다.

def test_map_absolute_turn_is_paced_by_pose_fn():
    """map 절대 회전 분기가 **tick 마다** `pose_fn` 을 불러야 한다.

    ⚠️ 되돌림 감지용이다 — 그 한 줄을 빼면 호출수가 0 이 되어 빨개진다.
    """
    calls = {"pose": 0, "publish": 0}
    yaw = {"v": 3.1416}

    def publish(_lin, ang):
        calls["publish"] += 1
        yaw["v"] = wrap_pi(yaw["v"] + ang * 0.02)

    def pose_fn():
        calls["pose"] += 1
        return (0.0, 0.0, yaw["v"])

    ex = MoveExecutor(publish_twist=publish, pose_fn=pose_fn,
                      map_yaw_fn=lambda: yaw["v"])
    ok, why = ex.run([Move("turn", -1.5708, abs_yaw=1.5708)])

    assert ok is True and why == ""
    assert calls["publish"] > 0
    assert calls["pose"] >= calls["publish"] - 1, (
        f"발행 {calls['publish']}회에 pose_fn {calls['pose']}회 — 페이싱이 빠졌다")


# ── 첫 회전 목표는 **map 상수**다 — 몇 cm 떨어진 두 AMCL 점의 atan2 가 아니다 ──────

def test_retreat_faces_the_given_map_yaw_instead_of_guessing_from_two_points():
    """`face_yaw` 를 주면 체크포인트가 어디 있든 회전 목표가 **그 값**이다.

    ⚠️ 여기 목표점은 일부러 엉뚱한 방향(+x)에 둔다 — 추정으로 되돌리면 코가 180°
       를 보게 되어 이 시험이 빨개진다.
    """
    from app.shelf.geometry import retreat_moves

    moves = retreat_moves(0.0, 0.0, 3.1416, 1.0, 0.0, face_yaw=1.5708)
    turn = next(m for m in moves if m.kind == "turn")
    assert math.isclose(turn.abs_yaw, 1.5708, abs_tol=1e-9)
    assert math.isclose(wrap_pi(3.1416 + turn.value), 1.5708, abs_tol=1e-9)
    # 거리는 그대로 두 점 사이 거리다 — 못박는 건 방위뿐이다.
    assert math.isclose(next(m for m in moves if m.kind == "drive").value, -1.0)


def test_short_checkpoint_hop_still_retreats_the_right_way(monkeypatch):
    """체크포인트가 2cm 밖에 안 떨어졌고 AMCL 이 **엉뚱한 쪽으로 튀어도**, 못박은
    방위 덕에 첫 회전은 서가(+90°)를 향한다 — 꽁무늬는 반대편으로 빠진다.

    이게 `face_yaw` 를 넣은 이유다: `hit_dist − CLEARANCE_M` 이 수 cm 라 두 점의
    atan2 는 AMCL 잡음(±2~3cm)에 통째로 휘둘린다.
    """
    from app.core import backup_runner

    # 서가 법선 +90°. 물러날 곳은 −90° 쪽 2cm 인데, 잡음이 x 로 2cm 밀어 놨다.
    backup_runner.record_return_targets([(0.02, -0.02), (0.02, -0.30)],
                                        retreat_yaw=1.5708)
    driven = []

    def fake_drive(moves, _gen):
        driven.extend(moves)
        return True, 200, {"backed": True}, ""

    ok, _status, _data, _msg = backup_runner.run_backup(
        {}, _read_pose_fn=lambda: (0.0, 0.0, 3.1416), _drive_fn=fake_drive)

    assert ok is True
    first_turn = next(m for m in driven if m.kind == "turn")
    assert math.isclose(first_turn.abs_yaw, 1.5708, abs_tol=1e-9), (
        "첫 회전이 잡음 섞인 atan2 로 돌아갔다")
    assert driven[1].kind == "drive" and driven[1].value < 0


def test_second_leg_is_not_pinned_to_the_shelf_normal():
    """못박기는 **첫 다리만**이다. 옆축 다리까지 서가 법선으로 돌리면 엉뚱한 데로 간다."""
    from app.core import backup_runner

    backup_runner.record_return_targets([(0.0, -0.1), (0.3, -0.1)], retreat_yaw=1.5708)
    driven = []

    ok, _status, _data, _msg = backup_runner.run_backup(
        {}, _read_pose_fn=lambda: (0.0, 0.0, 3.1416),
        _drive_fn=lambda moves, _gen: (driven.extend(moves), (True, 200, {}, ""))[1])

    assert ok is True
    turns = [m for m in driven if m.kind == "turn"]
    assert math.isclose(turns[0].abs_yaw, 1.5708, abs_tol=1e-9)
    # 둘째 다리 목표는 +x 쪽이므로 등을 그리로 돌린다 = 코는 −x(π).
    assert math.isclose(abs(turns[1].abs_yaw), math.pi, abs_tol=1e-6)



# ─── 도킹 뒤 복귀(backup)가 매번 "pose 를 못 얻었다" 로 죽던 건 ─────────────────
# 실측(2026-08-05 관제 UI): 배달 5다리 중 3번(backup)에서 정지.
#   ● 1. 주행 → 예술서가 / ● 2. 책 집기 / ○ 3. 작업 ← 여기서 죽음
#   사유 — 현재 pose(/amcl_pose) 를 못 얻었다
# 그 뒤 목적지 주행·놓기·순회(task_done → PATROL)까지 통째로 막혔다.

def test_backup_pose_comes_from_the_long_lived_subscription(monkeypatch):
    """도킹 직후 로봇은 **정지**해 있고, 그때 AMCL 은 원리상 아무것도 안 낸다.

    그러니 그 순간 `/amcl_pose` 를 새로 구독해 기다리는 건 답이 없다 —
    상시 구독이 들고 있는 마지막 자세를 써야 한다(`park_dock.py:129` 와 같은 방식).
    """
    from app.core import backup_runner, ros_bridge

    monkeypatch.setattr(ros_bridge, "get_current_pose", lambda: (1.5, 2.5, 0.3))

    def _must_not_probe():
        raise AssertionError("상시 구독에 자세가 있는데 새 구독으로 기다렸다")

    monkeypatch.setattr(backup_runner, "_probe_amcl_pose", _must_not_probe)
    assert backup_runner._read_current_pose() == (1.5, 2.5, 0.3)


def test_backup_falls_back_to_probe_only_when_nothing_is_cached(monkeypatch):
    """상시 구독이 아직 아무것도 못 받았을 때만(부팅 직후 등) 예비 경로로 간다."""
    from app.core import backup_runner, ros_bridge

    monkeypatch.setattr(ros_bridge, "get_current_pose", lambda: None)
    monkeypatch.setattr(backup_runner, "_probe_amcl_pose", lambda: (9.0, 9.0, 0.0))
    assert backup_runner._read_current_pose() == (9.0, 9.0, 0.0)


def test_backup_probe_subscribes_latched_or_a_stopped_robot_never_hears_amcl():
    """예비 경로도 **TRANSIENT_LOCAL** 이어야 한다.

    기본 VOLATILE 이면 구독 뒤 새로 발행되는 것만 받는데, 정지한 로봇에서는
    그게 영영 안 온다 — 예비 경로가 예비 구실을 못 한다. AST 로 못 박는다
    (ROS 없이는 QoS 를 실행으로 확인할 수 없다).
    """
    import ast
    import inspect

    from app.core import backup_runner

    tree = ast.parse(inspect.getsource(backup_runner._probe_amcl_pose))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "TRANSIENT_LOCAL" in names, \
        "정지한 로봇은 VOLATILE 구독으로 /amcl_pose 를 영영 못 받는다"
    # 그리고 그 프로파일이 실제로 구독에 쓰여야 한다 — 만들어만 두면 의미가 없다.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_subscription"):
            assert not (isinstance(node.args[-1], ast.Constant)
                        and isinstance(node.args[-1].value, int)), \
                "create_subscription 이 아직 depth 정수(기본 VOLATILE)를 쓴다"
            break
    else:
        raise AssertionError("create_subscription 호출을 못 찾았다")


# ─── 복귀 회전을 map 절대 yaw 로 닫는다 ───────────────────────────────────────
# 목표는 언제나 map 절대 자세인데 실행이 odom 적분 상대 회전이면 오차가 누적된다.
# 도킹은 2026-08-05 에 `turn_to_map_yaw` 로 갈아탔는데 복귀만 옛 방식이 남아 있었다 —
# 복귀는 체크포인트 2개 × (TURN·DRIVE·TURN) = **회전 6번**이라 누적이 더 크다.

def _turn_only(abs_yaw, rel):
    from app.shelf.geometry import Move, TURN
    return Move(TURN, rel, abs_yaw=abs_yaw)


def _drive_map_turn(start_map_yaw, abs_yaw, rel, *, odom_gain=1.0, hz=20.0):
    """회전 한 번을 굴린다. `(끝난 map yaw, tick 수, why)`.

    `odom_gain` 은 odom 이 실제 회전량을 잘못 재는 정도다 — 1.0 이 아니면 상대 회전
    방식은 그만큼 어긋나고, map 절대 방식은 영향을 안 받아야 한다.
    """
    from app.core.backup_runner import MoveExecutor

    state = {"map_yaw": start_map_yaw, "odom_yaw": 0.0}

    def publish(lin, ang):
        state["map_yaw"] = viewer_wrap(state["map_yaw"] + ang / hz)
        state["odom_yaw"] += ang / hz * odom_gain

    ex = MoveExecutor(publish_twist=publish,
                      pose_fn=lambda: (0.0, 0.0, state["odom_yaw"]),
                      turn_speed=0.4, turn_tol=0.02,
                      map_yaw_fn=lambda: state["map_yaw"])
    ok, why = ex.run([_turn_only(abs_yaw, rel)])
    return state["map_yaw"], ok, why


def viewer_wrap(a):
    import math
    return (a + math.pi) % (2 * math.pi) - math.pi


def test_backup_turn_lands_on_the_absolute_map_yaw():
    """상대각(`value`)이 틀려도 **map 절대 목표**에 선다."""
    import math
    # 상대각을 일부러 엉뚱하게(0.1rad) 줘도 절대 목표 -1.5708 로 가야 한다.
    end, ok, why = _drive_map_turn(start_map_yaw=1.0, abs_yaw=-1.5708, rel=0.1)
    assert ok, why
    assert abs(viewer_wrap(end - (-1.5708))) <= 0.02, end


def test_backup_turn_is_immune_to_odom_scale_error():
    """odom 이 회전량을 20% 적게 재도 map 기준 결과는 그대로다.

    옛 상대 회전 방식이면 그만큼 덜/더 돌고 그 오차가 다음 회전으로 누적됐다.
    """
    import math
    end, ok, _ = _drive_map_turn(start_map_yaw=0.0, abs_yaw=math.pi / 2,
                                 rel=math.pi / 2, odom_gain=0.8)
    assert ok
    assert abs(viewer_wrap(end - math.pi / 2)) <= 0.02, end


def test_backup_turn_falls_back_to_relative_when_map_pose_is_missing():
    """map pose 를 못 얻으면 **조용히 죽지 않고** 예전 상대 회전으로 돈다."""
    import math
    from app.core.backup_runner import MoveExecutor

    state = {"odom_yaw": 0.0}
    ex = MoveExecutor(publish_twist=lambda lin, ang: state.__setitem__("odom_yaw", state["odom_yaw"] + ang / 20.0),
                      pose_fn=lambda: (0.0, 0.0, state["odom_yaw"]),
                      turn_speed=0.4, turn_tol=0.02,
                      map_yaw_fn=lambda: None)          # 상시 구독이 비었다
    ok, why = ex.run([_turn_only(math.pi / 2, math.pi / 2)])
    assert ok, why
    assert abs(state["odom_yaw"] - math.pi / 2) <= 0.05


def test_approach_moves_carry_the_absolute_yaw_for_both_turns():
    """계획 단계에서 map 절대 목표를 실어 보내야 실행부가 쓸 수 있다."""
    import math
    from app.shelf.geometry import approach_moves, TURN

    moves = approach_moves(0.0, 0.0, 0.0, 1.0, 1.0, clearance=0.0, final_yaw=-1.5708)
    turns = [m for m in moves if m.kind == TURN]
    assert len(turns) == 2
    assert turns[0].abs_yaw == pytest.approx(math.atan2(1.0, 1.0))   # 진행 방향
    assert turns[1].abs_yaw == pytest.approx(-1.5708)                # 도착 자세
