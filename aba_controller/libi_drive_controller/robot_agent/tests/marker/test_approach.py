"""상태기계 테스트 — 하드웨어 없이 전 단계 전이와 중단 조건을 검증한다."""
import math

import pytest

from app.marker.approach import MarkerApproach
from app.marker.config import MarkerDriveConfig
from app.marker.types import MarkerObs


def obs(z, ex=0.0, yaw=0.0, lat=0.0, marker_id=1):
    return MarkerObs(marker_id=marker_id, ex=ex, z_m=z,
                     yaw_deg=yaw, lateral_m=lat, size_frac=0.1)


def step(m, o=None, *, yaw=0.0, fwd=0.0, front=None, t=0.0):
    """긴 키워드를 매번 쓰지 않기 위한 얇은 래퍼. fwd = 전진 누적값(m)."""
    return m.step(o, yaw_deg=yaw, forward_m=fwd, front_m=front, now_s=t)


# ---------------------------------------------------------------- 탐색

def test_search_turns_left_first():
    m = MarkerApproach(MarkerDriveConfig())
    c = step(m)
    assert c.phase == "SEARCH"
    assert c.angular > 0
    assert c.linear == 0.0


def test_search_stops_turning_at_step_target():
    m = MarkerApproach(MarkerDriveConfig(search_step_deg=20.0, turn_pause_s=0.4))
    step(m, t=0.0)
    c = step(m, yaw=20.0, t=0.5)
    assert c.angular == 0.0        # 목표 각 도달 → 멈춰서 본다(모션블러 회피)


def test_search_aborts_after_full_sweep():
    m = MarkerApproach(MarkerDriveConfig(search_step_deg=20.0, search_span_deg=60.0,
                                         turn_pause_s=0.0))
    yaw, t, c = 0.0, 0.0, None
    for _ in range(500):
        c = step(m, yaw=yaw, t=t)
        if c.done:
            break
        yaw += 5.0 if c.angular > 0 else (-5.0 if c.angular < 0 else 0.0)
        t += 0.1
    assert c.phase == "ABORT"
    assert c.reason == "not_found"


def test_search_completes_sweep_even_with_coarse_yaw_steps():
    """루프가 느려 한 틱에 여러 도씩 돌아도 스윕이 진행돼야 한다.

    ±tol 창만 보면 5Hz(4°/tick)에서 창을 건너뛰어 도달 판정이 영영 안 난다.
    목표 각 교차로도 판정하므로 통과해야 한다.
    """
    m = MarkerApproach(MarkerDriveConfig(search_step_deg=20.0, search_span_deg=60.0,
                                         search_tol_deg=2.0, turn_pause_s=0.0,
                                         search_step_timeout_s=5.0, timeout_s=600.0))
    yaw, t, c = 0.0, 0.0, None
    for _ in range(400):
        c = step(m, yaw=yaw, t=t)
        if c.done:
            break
        yaw += 12.0 if c.angular > 0 else (-12.0 if c.angular < 0 else 0.0)
        t += 0.2
    assert c.phase == "ABORT" and c.reason == "not_found"   # turn_stall 이 아니어야 한다


def test_search_aborts_when_rotation_makes_no_progress():
    """회전 명령을 내는데 yaw 가 안 변한다 = 바퀴 헛돎이거나 odom 정지.

    이 감시가 없으면 전역 타임아웃까지 헛돌고 원인이 'timeout' 으로 오분류된다.
    """
    m = MarkerApproach(MarkerDriveConfig(search_step_timeout_s=1.0, timeout_s=600.0))
    c = None
    for i in range(50):
        c = step(m, yaw=0.0, t=0.1 * i)       # yaw 가 영영 안 변한다
        if c.done:
            break
    assert c.phase == "ABORT"
    assert c.reason == "turn_stall"


# ---------------------------------------------------------------- 원거리 접근

def test_marker_found_switches_to_homing():
    m = MarkerApproach(MarkerDriveConfig())
    c = step(m, obs(1.5))
    assert c.phase == "HOMING"
    assert c.linear > 0


def test_other_marker_id_is_ignored():
    m = MarkerApproach(MarkerDriveConfig(marker_id=1))
    c = step(m, obs(1.5, marker_id=7))
    assert c.phase == "SEARCH"


def test_homing_steers_proportionally_to_ex():
    small = step(MarkerApproach(MarkerDriveConfig()), obs(1.5, ex=0.10))
    big = step(MarkerApproach(MarkerDriveConfig()), obs(1.5, ex=0.40))
    assert abs(big.angular) > abs(small.angular)


def test_homing_ignores_flipped_yaw_beyond_gate():
    """게이트 밖에서는 yaw 해가 뒤집혀도 명령이 달라지면 안 된다."""
    a = step(MarkerApproach(MarkerDriveConfig()), obs(1.5, ex=0.2, yaw=+170.0))
    b = step(MarkerApproach(MarkerDriveConfig()), obs(1.5, ex=0.2, yaw=-170.0))
    assert a.angular == pytest.approx(b.angular)


def test_homing_respects_deadband():
    m = MarkerApproach(MarkerDriveConfig(steer_deadband=0.05))
    c = step(m, obs(1.5, ex=0.01))
    assert c.angular == 0.0


def test_steer_sign_flips_direction():
    plus = step(MarkerApproach(MarkerDriveConfig(steer_sign=1.0)), obs(1.5, ex=0.3))
    minus = step(MarkerApproach(MarkerDriveConfig(steer_sign=-1.0)), obs(1.5, ex=0.3))
    assert plus.angular == pytest.approx(-minus.angular)


def test_gate_switches_to_axis_align():
    m = MarkerApproach(MarkerDriveConfig(axis_gate_m=0.6))
    step(m, obs(0.9), t=0.0)
    c = step(m, obs(0.5), t=0.1)
    assert c.phase == "AXIS_ALIGN"


def test_timeout_aborts():
    m = MarkerApproach(MarkerDriveConfig(timeout_s=1.0))
    step(m, obs(1.5), t=0.0)
    c = step(m, obs(1.5), t=2.0)
    assert c.phase == "ABORT" and c.reason == "timeout"


# ---------------------------------------------------------------- 축 정렬

def _to_axis_align(cfg=None):
    m = MarkerApproach(cfg or MarkerDriveConfig())
    step(m, obs(0.9), t=0.0)
    step(m, obs(0.5), t=0.1)
    return m


def test_axis_align_corrects_more_when_skewed():
    straight = step(_to_axis_align(), obs(0.5, yaw=0.0, lat=0.0), t=0.2)
    skewed = step(_to_axis_align(), obs(0.5, yaw=15.0, lat=0.12), t=0.2)
    assert abs(skewed.angular) > abs(straight.angular)


def test_axis_align_moves_in_pulses():
    m = _to_axis_align(MarkerDriveConfig(move_pulse_s=0.1, move_pause_s=0.9))
    moving = step(m, obs(0.4), t=0.15)     # 펄스 진행 중(0.1 ~ 0.2)
    resting = step(m, obs(0.4), t=0.5)     # 펄스 종료 후, 다음 펄스는 1.1 부터
    assert moving.linear > 0
    assert resting.linear == 0.0


def test_reaching_stop_distance_while_visible_is_done():
    m = _to_axis_align(MarkerDriveConfig(stop_m=0.10, front_offset_m=0.0))
    c = step(m, obs(0.09), t=0.2)
    assert c.done and c.phase == "DONE" and c.reason == "reached"


def test_front_offset_shifts_stop_point():
    """정지 판정은 마커~최전방 기준이므로 오프셋만큼 더 일찍 선다."""
    m = _to_axis_align(MarkerDriveConfig(stop_m=0.10, front_offset_m=0.05))
    c = step(m, obs(0.14), t=0.2)
    assert c.done and c.reason == "reached"


def test_yaw_misalignment_blocks_blind_push():
    """lateral 이 0이어도 yaw 가 틀어져 있으면 무시각 구간에 들어가면 안 된다.

    들어가면 남은 거리를 30° 비스듬히 밀고 들어간다.
    """
    m = _to_axis_align(MarkerDriveConfig(pose_yaw_tol_deg=8.0, lost_near_m=0.25))
    c = step(m, obs(0.24, yaw=30.0, lat=0.0), t=0.2)
    assert c.phase == "AXIS_ALIGN"
    assert abs(c.angular) > 0


def test_blind_push_needs_consecutive_aligned_frames():
    """한 프레임 튄 값에 눈을 감고 밀기 시작하면 안 된다 — 연속으로 맞아야 넘어간다."""
    cfg = MarkerDriveConfig(pose_yaw_tol_deg=8.0, lost_near_m=0.25,
                            aligned_frames_needed=3)
    m = _to_axis_align(cfg)
    step(m, obs(0.24, yaw=30.0, lat=0.15), t=0.15)         # 먼저 틀어진 프레임(카운트 0)
    good = obs(0.24, yaw=2.0, lat=0.01)
    assert step(m, good, t=0.2).phase == "AXIS_ALIGN"      # 1회
    assert step(m, good, t=0.3).phase == "AXIS_ALIGN"      # 2회
    assert step(m, good, t=0.4).phase == "BLIND_PUSH"      # 3회째에 진입


def test_one_noisy_aligned_frame_does_not_start_blind_push():
    cfg = MarkerDriveConfig(pose_yaw_tol_deg=8.0, lost_near_m=0.25,
                            aligned_frames_needed=3, align_stall_s=100.0)
    m = _to_axis_align(cfg)
    step(m, obs(0.24, yaw=30.0, lat=0.15), t=0.2)          # 틀어짐
    step(m, obs(0.24, yaw=2.0, lat=0.01), t=0.3)           # 한 번 튀어서 맞음
    c = step(m, obs(0.24, yaw=25.0, lat=0.12), t=0.4)      # 다시 틀어짐
    assert c.phase == "AXIS_ALIGN"


def test_align_stall_aborts_on_lateral():
    m = _to_axis_align(MarkerDriveConfig(align_stall_s=0.5, pose_axis_tol_m=0.08))
    c = None
    for i in range(20):
        c = step(m, obs(0.18, lat=0.15), t=0.2 + 0.1 * i)
        if c.done:
            break
    assert c.phase == "ABORT" and c.reason == "align_stall"


def test_align_stall_aborts_on_yaw_only():
    """yaw 만 계속 안 맞는 경우도 잡아야 한다(lateral 은 0)."""
    m = _to_axis_align(MarkerDriveConfig(align_stall_s=0.5, pose_yaw_tol_deg=8.0))
    c = None
    for i in range(20):
        c = step(m, obs(0.18, yaw=45.0, lat=0.0), t=0.2 + 0.1 * i)
        if c.done:
            break
    assert c.phase == "ABORT" and c.reason == "align_stall"


# ---------------------------------------------------------------- 상실 분기

def test_lost_far_returns_to_search():
    m = MarkerApproach(MarkerDriveConfig(lost_grace=3, lost_near_m=0.25))
    step(m, obs(0.9), t=0.0)
    c = None
    for i in range(3):
        c = step(m, t=0.1 * (i + 1))
    assert c.phase == "SEARCH"


def test_lost_near_enters_blind_push():
    m = MarkerApproach(MarkerDriveConfig(lost_grace=3, lost_near_m=0.25))
    step(m, obs(0.5), t=0.0)
    step(m, obs(0.18), t=0.1)
    c = None
    for i in range(3):
        c = step(m, t=0.2 + 0.1 * i)
    assert c.phase == "BLIND_PUSH"


# ---------------------------------------------------------------- 무시각 전진

def test_blind_push_travels_remaining_then_done():
    m = MarkerApproach(MarkerDriveConfig(lost_grace=1, lost_near_m=0.25,
                                         stop_m=0.10, front_offset_m=0.0))
    step(m, obs(0.5), t=0.0)
    step(m, obs(0.16), t=0.1)
    c = step(m, t=0.2, fwd=0.0)
    assert c.phase == "BLIND_PUSH" and c.linear > 0
    c = step(m, t=0.3, fwd=0.03)
    assert not c.done                       # 0.06 중 0.03 만 갔다
    c = step(m, t=0.4, fwd=0.06)
    assert c.done and c.phase == "DONE"


def test_blind_push_uses_displacement_not_path_length():
    """제자리 진동으로 누적거리가 늘어도 완료로 읽히면 안 된다."""
    m = MarkerApproach(MarkerDriveConfig(lost_grace=1, lost_near_m=0.25,
                                         stop_m=0.10, front_offset_m=0.0,
                                         no_progress_s=100.0))
    step(m, obs(0.5), t=0.0)
    step(m, obs(0.16), t=0.1)
    step(m, t=0.2, fwd=0.0)
    c = None
    for i in range(10):                     # 앞뒤로 0.02m 왕복 = 누적 0.4m, 순전진 0
        c = step(m, t=0.3 + 0.1 * i, fwd=0.02 if i % 2 == 0 else 0.0)
    assert not c.done
    assert c.phase == "BLIND_PUSH"


def test_blind_push_subtracts_coasting_during_lost_grace():
    """상실 유예 동안 관성으로 더 간 거리는 목표에서 빠져야 한다.

    진입 시점을 기준 삼으면 그만큼 더 밀어 목표를 지나친다.
    """
    m = MarkerApproach(MarkerDriveConfig(lost_grace=3, lost_near_m=0.25,
                                         stop_m=0.10, front_offset_m=0.0))
    step(m, obs(0.5), t=0.0)
    step(m, obs(0.16), t=0.1, fwd=0.00)     # 마지막 관측: 남은 목표 0.06m
    step(m, t=0.2, fwd=0.02)                # 유예 중 관성 2cm
    step(m, t=0.3, fwd=0.04)                # 유예 중 관성 총 4cm
    c = step(m, t=0.4, fwd=0.06)            # 관측 시점 대비 6cm → 도달
    assert c.done and c.phase == "DONE" and c.reason == "reached"


def test_blind_push_blocked_when_no_progress():
    m = MarkerApproach(MarkerDriveConfig(lost_grace=1, lost_near_m=0.25, no_progress_s=0.5))
    step(m, obs(0.5), t=0.0)
    step(m, obs(0.18), t=0.1)
    c = None
    for i in range(10):
        c = step(m, t=0.2 + 0.1 * i, fwd=0.0)
        if c.done:
            break
    assert c.phase == "ABORT" and c.reason == "blocked"


# ---------------------------------------------------------------- 안전

def test_scan_guard_stops_immediately():
    c = step(_to_axis_align(), obs(0.3), front=0.03, t=0.5)
    assert c.done and c.reason == "scan_guard"
    assert c.linear == 0.0 and c.angular == 0.0


def test_nan_scan_does_not_bypass_guard_silently():
    """NaN 은 모든 비교가 False 라 '안전'처럼 통과한다. '값 없음'으로 취급해야 한다."""
    m = _to_axis_align()
    c = step(m, obs(0.3), front=float("nan"), t=0.5)
    assert c.phase == "AXIS_ALIGN"          # 계속 진행하되 근접 판정에는 안 쓴다
    assert not c.done


def test_nan_observation_is_treated_as_no_detection():
    m = MarkerApproach(MarkerDriveConfig())
    c = step(m, obs(float("nan")))
    assert c.phase == "SEARCH"


def test_bad_odom_aborts():
    m = MarkerApproach(MarkerDriveConfig())
    c = step(m, obs(1.0), yaw=float("nan"))
    assert c.phase == "ABORT" and c.reason == "bad_odom"


def test_finished_machine_keeps_returning_zero():
    m = _to_axis_align()
    step(m, obs(0.05), t=0.2)               # DONE
    c = step(m, obs(0.5), t=0.3)
    assert c.done and c.linear == 0.0 and c.angular == 0.0


# ------------------------------------------------- codex 적대적 리뷰 회귀 테스트

def test_loss_while_misaligned_stops_instead_of_pushing_blind():
    """45° 틀어진 채로 마커를 잃으면 그대로 밀지 않는다.

    벽 코앞에서 탐색을 돌리는 것도 무의미하므로 멈추고 이유를 남긴다.
    """
    m = _to_axis_align(MarkerDriveConfig(lost_grace=1, lost_near_m=0.25,
                                         align_stall_s=100.0))
    step(m, obs(0.18, yaw=45.0, lat=0.15), t=0.2)
    c = step(m, t=0.3)
    assert c.phase == "ABORT" and c.reason == "lost_misaligned"


def test_loss_inside_stop_distance_counts_as_arrival():
    """목표 거리 안에서 마커가 시야를 벗어난 것은 도착이지 실패가 아니다."""
    # 정렬돼 있으면 보이는 그 프레임에서 이미 완료되므로, 아직 안 끝난 상태를
    # 만들려면 틀어진 채로 목표 거리에 있어야 한다(제자리 정렬 중).
    m = _to_axis_align(MarkerDriveConfig(lost_grace=1, stop_m=0.10, front_offset_m=0.0,
                                         align_stall_s=100.0))
    assert step(m, obs(0.095, yaw=30.0), t=0.2).reason == "final_align"
    c = step(m, t=0.3)
    assert c.done and c.phase == "DONE" and c.reason == "reached"


def test_stop_distance_while_misaligned_rotates_instead_of_finishing():
    """거리가 맞아도 비뚤면 완료가 아니다 — 전진을 멈추고 제자리에서 맞춘다."""
    m = _to_axis_align(MarkerDriveConfig(stop_m=0.10, front_offset_m=0.0,
                                         pose_yaw_tol_deg=8.0, align_stall_s=100.0))
    c = step(m, obs(0.09, yaw=45.0, lat=0.20), t=0.2)
    assert not c.done
    assert c.phase == "AXIS_ALIGN" and c.reason == "final_align"
    assert c.linear == 0.0 and abs(c.angular) > 0


def test_align_stall_allows_slow_but_real_convergence():
    """오차가 계속 줄고 있으면 시간이 오래 걸려도 중단하면 안 된다.

    45°를 steer_ang_max(0.08rad/s)로 8° 안까지 줄이려면 8.1초가 필요하다.
    경과 시간으로 자르는 판정은 이 정상 수렴을 죽인다.
    """
    m = _to_axis_align(MarkerDriveConfig(align_stall_s=1.0, pose_yaw_tol_deg=8.0,
                                         align_progress_eps=0.05))
    c = None
    yaw = 45.0
    for i in range(30):                    # 매 프레임 조금씩 좋아진다
        yaw -= 1.0
        # 로봇은 실제로 조금씩 전진한다 — 안 그러면 무진전 감시(blocked)에 걸린다
        c = step(m, obs(0.40, yaw=yaw), t=0.2 + 0.5 * i, fwd=0.01 * i)
        if c.done:
            break
    assert c.phase == "AXIS_ALIGN", f"{c.phase}/{c.reason}"


def test_align_stall_timer_does_not_leak_across_search():
    """상실→탐색→재획득한 정렬 시도가 즉시 중단되면 안 된다."""
    m = MarkerApproach(MarkerDriveConfig(lost_grace=1, align_stall_s=0.5,
                                         pose_axis_tol_m=0.08))
    step(m, obs(0.50, lat=0.10), t=0.0)      # AXIS_ALIGN 진입(미정렬)
    step(m, t=0.1)                            # 상실 → SEARCH
    c = step(m, obs(0.50, lat=0.10), t=1.0)  # 재획득
    assert c.phase == "AXIS_ALIGN" and not c.done


def test_steering_state_is_reset_after_reacquisition():
    """상실 구간을 넘어 LPF 가 살아남으면 재획득 첫 틱이 옛 방향으로 나간다."""
    m = MarkerApproach(MarkerDriveConfig(lost_grace=1, axis_gate_m=0.6))
    right = step(m, obs(1.5, ex=+0.5), t=0.0)
    step(m, t=0.1)                            # 상실 → SEARCH (조향 상태 초기화)
    left = step(m, obs(1.5, ex=-0.5), t=1.0)
    assert right.angular * left.angular < 0   # 즉시 반대로 꺾여야 한다


def test_dt_uses_real_elapsed_time():
    """적분은 실제 경과 시간을 받아야 한다. 1/loop_hz 고정이면 시간을 속인다."""
    cfg = MarkerDriveConfig(steer_ki=0.4, steer_kd=0.0, loop_hz=12.0,
                            steer_ang_max=0.3, steer_ang_min=0.0)
    fast = MarkerApproach(cfg)
    slow = MarkerApproach(cfg)
    for i in range(3):
        f = step(fast, obs(1.5, ex=0.3), t=0.08 * i)
        s = step(slow, obs(1.5, ex=0.3), t=1.00 * i)
    assert abs(s.angular) > abs(f.angular)    # 느린 루프가 적분을 더 쌓는다


def test_final_align_failure_is_not_reported_as_success():
    """거리는 맞췄는데 정면각을 못 맞췄으면 성공이 아니다.

    도킹 제어기가 이걸 DONE 으로 보고하면 거짓 성공이다 — 멈추되 실패로 남긴다.
    """
    m = _to_axis_align(MarkerDriveConfig(stop_m=0.10, front_offset_m=0.0,
                                         pose_yaw_tol_deg=8.0, align_stall_s=0.2))
    c = None
    for i, t in enumerate((0.2, 0.3, 0.4, 0.51)):
        yaw = 46.0 if i % 2 == 0 else 45.0     # 오차가 미세하게 진동한다
        c = step(m, obs(0.09, yaw=yaw, lat=0.20), t=t)
        if c.done:
            break
    assert c.phase == "ABORT" and c.reason == "final_align_failed"


# ------------------------------------------------- 느린 회전 (실기에서 나온 문제)

def _sweep_until_done(cfg, *, yaw_follows_command: bool, max_ticks=4000):
    """탐색만 돌린다(마커 없음). yaw 가 명령을 따라가는지 여부만 바꾼다."""
    m = MarkerApproach(cfg)
    yaw, t, dt = 0.0, 0.0, 1.0 / cfg.loop_hz
    for _ in range(max_ticks):
        cmd = m.step(None, yaw_deg=yaw, forward_m=0.0, front_m=None, now_s=t)
        if cmd.done:
            return cmd, t
        if yaw_follows_command:
            yaw += math.degrees(cmd.angular) * dt      # 명령대로 실제로 돈다
        t += dt
    raise AssertionError("끝나지 않았다")


def test_slow_but_real_rotation_is_not_called_a_stall():
    """불감대가 높아 ang_search 를 낮춘 로봇은 한 스텝에 3초 넘게 걸린다.

    실기 증상: ang_search 0.16rad/s 로 60° 구간을 도는 데 6.5초가 걸려
    search_step_timeout_s(3초)를 넘고, 정상 회전인데 turn_stall 로 중단됐다.
    시간이 아니라 '각도가 변하고 있는가'로 판정해야 한다.
    """
    cfg = MarkerDriveConfig(ang_search=0.16, search_step_timeout_s=3.0)
    cmd, elapsed = _sweep_until_done(cfg, yaw_follows_command=True)
    assert cmd.reason == "not_found", f"{cmd.reason} (경과 {elapsed:.1f}초)"
    assert elapsed > 3.0, "이 시나리오가 3초를 안 넘으면 회귀를 못 잡는다"


def test_frozen_odom_is_still_a_stall():
    """회전 명령을 내는데 yaw 가 전혀 안 변하면 여전히 turn_stall 이어야 한다."""
    cfg = MarkerDriveConfig(ang_search=0.16, search_step_timeout_s=3.0)
    cmd, _ = _sweep_until_done(cfg, yaw_follows_command=False)
    assert cmd.reason == "turn_stall"


def test_zero_pause_makes_axis_align_continuous():
    """pi/drive-smooth.sh 가 코드 수정 없이 연속 주행이 되는 근거.

    기본값(정지 0.9초)은 전체 틱의 17% 만 전진 명령이 나간다. 멈춘 순간의
    흐리지 않은 프레임으로만 마커 자세를 풀기 위한 것이다. 정지를 0 으로 두면
    끊김 없이 전진해야 한다 — 그러지 않으면 스크립트가 아무것도 안 바꾸는 셈이다.
    """
    def duty(pause):
        cfg = MarkerDriveConfig(move_pause_s=pause, axis_gate_m=0.6, lin_pulse=0.05)
        m = MarkerApproach(cfg)
        m.phase = "AXIS_ALIGN"
        moving = 0
        for i in range(60):
            t = i / cfg.loop_hz
            cmd = m.step(obs(z=0.45, lat=0.03), yaw_deg=0.0, forward_m=0.05 * t,
                         front_m=None, now_s=t)
            moving += cmd.linear > 0
        return moving / 60

    assert duty(0.0) == 1.0, "정지 0 인데도 끊긴다"
    assert duty(0.9) < 0.25, "기본값이 원래 띄엄띄엄이 아니면 이 테스트가 무의미하다"
