"""서가 도킹 계산. 카메라·ROS 없이 합성 프레임과 합성 격자로 본다."""
import math

import numpy as np
import pytest

from app.shelf.raycast import Grid
from app.core.shelf_dock import (CLEARANCE_M, EXTRA_TURN_RAD, FINAL_YAW_RAD,
                                 MARKER_SERVO_MAX_ANG, SHELF_YAW, USE_CAMERA_CALIBRATION,
                                 visual_servo_angular_z, plan_dock, shelf_axes, axis_projection,
                                 bounded_pid_linear, MAP_AXIS_MAX_LINEAR_MPS, dock_status_payload,
                                 map_heading_error, is_pose_fresh, SENSOR_STATE_STALE_SEC,
                                 POST_CENTER_STALE_SEC, MARKER_CENTER_STABLE_FRAMES,
                                 MARKER_SERVO_HZ, ema, MARKER_SERVO_DERIV_LPF_ALPHA,
                                 MAP_AXIS_TOL_M, MARKER_LOST_GRACE_SEC, FRAME_STALE_SEC,
                                 MARKER_SERVO_TIMEOUT_SEC, MARKER_SEARCH_ANG,
                                 MARKER_SEARCH_HALF_SPAN_RAD, marker_search_angular,
                                 blind_travel_stale_sec, AMCL_UPDATE_MIN_D_M,
                                 FINAL_APPROACH_TOL_M, FINAL_APPROACH_KP,
                                 FINAL_APPROACH_MAX_LINEAR_MPS,
                                 FINAL_APPROACH_TIMEOUT_SEC,
                                 camera_center_and_bearing, MAP_YAW_TOL_RAD,
                                 AMCL_UPDATE_MIN_A_RAD, MAP_YAW_ANG,
                                 TURN_SETTLE_SEC, MAP_YAW_TIMEOUT_SEC,
                                 map_pose_from_odom, AMCL_DEAD_RECKON_MAX_M,
                                 resolve_map_pose, MAP_AXIS_KP, map_turn_angular,
                                 MAP_YAW_ANTIPODE_MARGIN_RAD, MAP_YAW_STABLE_TICKS)
from app.shelf.geometry import wrap_pi

#: 실기 nav2_params.yaml:304 의 `robot_radius`. 도킹 정지 거리가 이보다 작으면
#: 로봇 몸통이 벽 안에 있는 자리를 목표로 삼는 셈이다 — 아래 시험이 그걸 막는다.
NAV2_ROBOT_RADIUS_M = 0.06

K640 = (609.15651744, 607.39537016, 278.17496904, 250.36175645)


def _wall_grid():
    """1m x 1m, 2cm 셀. x >= 0.5 가 전부 점유."""
    w = h = 50
    data = []
    for row in range(h):
        for col in range(w):
            data.append(100 if col >= 25 else 0)
    return Grid(data=data, width=w, height=h, resolution=0.02,
                origin_x=0.0, origin_y=0.0)


def _frame_with_marker_at(u_center):
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    x0 = int(u_center) - 20
    img[100:140, x0:x0 + 40] = (120, 200, 40)
    return img


def test_shelf_yaw_table_has_exactly_the_three_demo_shelves():
    assert set(SHELF_YAW) == {"문학서가", "과학-인문학서가", "예술서가"}
    assert math.isclose(SHELF_YAW["문학서가"], 1.5708)
    assert math.isclose(SHELF_YAW["과학-인문학서가"], -1.5708)
    assert math.isclose(SHELF_YAW["예술서가"], 1.5708)


def test_constants_match_the_spec():
    assert math.isclose(EXTRA_TURN_RAD, 0.3491)
    assert math.isclose(FINAL_YAW_RAD, 3.1416)
    assert math.isclose(CLEARANCE_M, 0.088)


def test_clearance_leaves_the_robot_body_outside_the_shelf():
    """CLEARANCE_M 은 **로봇 원점**에서 잰 거리다(PGM 레이캐스트가 AMCL pose 에서
    쏜다) — 로봇 반지름보다 작으면 "몸통이 벽 안에 있는 자리"를 목표로 삼는 셈이다.
    2026-08-05까지 0.02(반지름 0.06보다 작았다)라 4cm 파고드는 값이었다.
    같은 날 0.07 → 0.09 로 또 올렸다 — 실여유 1cm 로는 PGM↔실제 라이다 오차
    (실측 -5.1cm / +2.9cm)를 못 버틴다(CLEARANCE_M 주석).
    2026-08-07 에 사용자 지시로 0.088(실여유 2.8cm)로 2mm 좁혔다.

    ⚠️ 예전 이 자리에는 "로봇이 원형으로 모델링돼 있어 회전해도 쓸고 가는 반경은
       반지름 그대로 — 이 부등식만 지키면 회전 중에도 안 닿는다" 고 적혀 있었다.
       **2026-08-07 실기가 그걸 반증했다: 빠져나가는 90° 제자리 회전에서 꽁무늬가
       서가에 닿았다.** 원형 모델은 nav2 의 가정이지 실물이 아니다. 그래서 이
       부등식은 "몸통이 벽 안에 있는 목표를 잡지 않는다" 까지만 보증한다 — 회전
       안전은 여기가 아니라 회전 **방향**으로 지킨다(`geometry.retreat_moves`).
    """
    assert CLEARANCE_M > NAV2_ROBOT_RADIUS_M, "몸통이 벽 안으로 들어가는 목표다"
    # 실측 PGM 오차가 cm 단위라(위 docstring) 1cm 짜리 여유는 뜻이 없다.
    # round: 0.088 - 0.06 은 2진 부동소수에서 0.027999999999999997 이다.
    assert round(CLEARANCE_M - NAV2_ROBOT_RADIUS_M, 6) >= 0.028, "실제 틈이 2.8cm 미만이다"


def test_unknown_shelf_is_rejected():
    moves, info = plan_dock("없는서가", (0.1, 0.5, 0.0),
                            _frame_with_marker_at(160), _wall_grid(),
                            K640, 320)
    assert moves is None
    assert "shelf" in info["error"]


def test_missing_marker_is_rejected():
    blank = np.zeros((240, 320, 3), dtype=np.uint8)
    moves, info = plan_dock("문학서가", (0.1, 0.5, 0.0), blank,
                            _wall_grid(), K640, 320)
    assert moves is None
    assert "marker" in info["error"]


def test_marker_dead_centre_rays_straight_ahead():
    """주점(cx@320 = 139.09)에 표식이 있으면 bearing 0 — 로봇 yaw 그대로 쏜다."""
    moves, info = plan_dock("문학서가", (0.1, 0.5, 0.0),
                            _frame_with_marker_at(160), _wall_grid(),
                            K640, 320)
    assert moves is not None
    assert abs(info["bearing_rad"]) < 0.01
    assert math.isclose(info["ray_yaw_rad"], 0.0, abs_tol=0.01)


def test_hit_distance_matches_the_wall():
    moves, info = plan_dock("문학서가", (0.1, 0.5, 0.0),
                            _frame_with_marker_at(160), _wall_grid(),
                            K640, 320)
    assert 0.38 <= info["hit_dist_m"] <= 0.42


def test_approach_target_stays_one_clearance_from_the_shelf_surface():
    moves, info = plan_dock("문학서가", (0.1, 0.5, 0.0),
                            _frame_with_marker_at(160), _wall_grid(),
                            K640, 320)
    hx, hy = info["hit_xy"]
    ax, ay = info["approach_xy"]
    assert math.isclose(hx - ax, CLEARANCE_M * math.cos(SHELF_YAW["문학서가"]))
    assert math.isclose(hy - ay, CLEARANCE_M * math.sin(SHELF_YAW["문학서가"]))
    assert math.isclose(sum(m.value for m in moves if m.kind == "drive"),
                        abs(ax - 0.1) + abs(ay - 0.5))


def test_camera_calibration_is_excluded_from_the_map_ray():
    _moves, info = plan_dock("문학서가", (0.1, 0.5, 0.0),
                             _frame_with_marker_at(200), _wall_grid(),
                             K640, 320)
    assert USE_CAMERA_CALIBRATION is False
    assert info["bearing_rad"] == 0.0
    assert math.isclose(info["ray_yaw_rad"], 0.0)


def test_plan_ends_facing_the_final_yaw():
    moves, _info = plan_dock("문학서가", (0.1, 0.5, 0.3),
                             _frame_with_marker_at(160), _wall_grid(),
                             K640, 320)
    turned = 0.3 + sum(m.value for m in moves if m.kind == "turn")
    assert math.isclose(((turned - FINAL_YAW_RAD + math.pi) % (2 * math.pi)) - math.pi,
                        0.0, abs_tol=1e-9)


def test_pid_visual_servo_uses_the_green_tape_centroid_feedback():
    # 화면 오른쪽 표식 오차는 오른쪽(음의 yaw), 왼쪽 오차는 왼쪽으로 보정한다.
    assert visual_servo_angular_z(0.2, 0.0, 0.0) < 0.0
    assert visual_servo_angular_z(-0.2, 0.0, 0.0) > 0.0
    assert abs(visual_servo_angular_z(10.0, 10.0, 10.0)) <= MARKER_SERVO_MAX_ANG


def test_lateral_axis_is_perpendicular_to_the_shelf_normal():
    normal, lateral = shelf_axes("문학서가")
    assert math.isclose(normal[0] * lateral[0] + normal[1] * lateral[1], 0.0, abs_tol=1e-9)
    assert math.isclose(axis_projection(1.0, 2.0, lateral), 1.0 * lateral[0] + 2.0 * lateral[1])


def test_map_pid_command_keeps_direction_and_speed_limit():
    assert bounded_pid_linear(1.0, 99.0, MAP_AXIS_MAX_LINEAR_MPS) == MAP_AXIS_MAX_LINEAR_MPS
    assert bounded_pid_linear(-1.0, 99.0, MAP_AXIS_MAX_LINEAR_MPS) == -MAP_AXIS_MAX_LINEAR_MPS


def test_lateral_heading_uses_amcl_map_yaw_not_odom_yaw():
    # odom의 원점/방위 보정값은 map과 다를 수 있다. 옆축 자세 제어에는 절대 map
    # 방위(AMCL)를 써야, 두 프레임 사이의 고정 offset이 회전 명령에 섞이지 않는다.
    assert math.isclose(map_heading_error(1.2, 0.7), 0.5)
    assert math.isclose(map_heading_error(-3.0, 3.0), math.tau - 6.0)


def test_dock_status_payload_carries_phase_and_numeric_progress():
    import json

    payload = json.loads(dock_status_payload("문학서가", "final_progress",
                                            pgm_distance_m=0.12,
                                            remaining_to_clearance_m=0.10))
    assert payload == {"event": "shelf_dock", "shelf": "문학서가",
                       "phase": "final_progress", "pgm_distance_m": 0.12,
                       "remaining_to_clearance_m": 0.10}


def test_no_wall_within_range_is_rejected():
    empty = Grid(data=[0] * 2500, width=50, height=50, resolution=0.02,
                 origin_x=0.0, origin_y=0.0)
    moves, info = plan_dock("문학서가", (0.1, 0.5, 0.0),
                            _frame_with_marker_at(160), empty, K640, 320)
    assert moves is None
    assert "raycast" in info["error"]


def test_is_pose_fresh_matches_the_old_strict_boundary():
    # 옛 조건은 `now - at > SENSOR_STATE_STALE_SEC` 이면 stale. 그대로 재현되는지.
    now = 1000.0
    assert is_pose_fresh(now - SENSOR_STATE_STALE_SEC, now, SENSOR_STATE_STALE_SEC)
    assert not is_pose_fresh(now - SENSOR_STATE_STALE_SEC - 0.01, now, SENSOR_STATE_STALE_SEC)
    assert not is_pose_fresh(None, now, SENSOR_STATE_STALE_SEC)


def test_post_center_stale_sec_covers_the_marker_centering_stationary_hold():
    """실측(2026-08-05): center_marker_pid()가 정렬 확인을 위해
    MARKER_CENTER_STABLE_FRAMES 프레임 연속 정지한다 — 그동안 AMCL은 새 pose를
    안 낸다. POST_CENTER_STALE_SEC 이 그 정지 시간보다 짧으면 이 완화가
    무의미해서(정지 시간 안 이미 다시 stale) 실패가 재발한다."""
    stationary_hold_sec = MARKER_CENTER_STABLE_FRAMES / MARKER_SERVO_HZ
    assert POST_CENTER_STALE_SEC > stationary_hold_sec

    # 회귀 재현: 정지 시작 직후(=hold 시작 시점) 찍힌 마지막 AMCL pose가, hold가
    # 끝나는 시점엔 옛 기준(0.75초)으론 죽고, 새 기준(POST_CENTER_STALE_SEC)으론
    # 산다 — 이게 원래 버그(CENTERED1 직후 실패)와 이번 수정의 핵심 차이다.
    now = 2000.0
    at = now - stationary_hold_sec
    assert not is_pose_fresh(at, now, SENSOR_STATE_STALE_SEC)   # 옛 기준: 여전히 실패
    assert is_pose_fresh(at, now, POST_CENTER_STALE_SEC)        # 새 기준: 통과


def test_ema_first_sample_passes_through_unchanged():
    assert ema(None, 42.0, 0.35) == 42.0


def test_ema_smooths_toward_raw_by_alpha():
    # ema(prev=0, raw=10, alpha=0.35) == 0.35*10 + 0.65*0 == 3.5
    assert math.isclose(ema(0.0, 10.0, 0.35), 3.5)
    # alpha=1.0 이면 필터 없는 것과 같다(raw 그대로).
    assert ema(0.0, 10.0, 1.0) == 10.0
    # alpha=0.0 이면 절대 안 바뀐다(raw 를 완전히 무시).
    assert ema(5.0, 999.0, 0.0) == 5.0


def test_ema_damps_a_noise_spike_more_than_raw_derivative_would():
    """실측(2026-08-05): marker_not_found 없이 marker_timeout 이 났다 — 매 프레임
    마커는 찾는데 ±5px 안에서 30프레임을 못 버텼다. 미분항에 노이즈 스파이크가 그대로
    들어가면 각속도 명령이 튀어 정렬이 흔들린다 — 필터가 그 스파이크를 죽이는지 확인."""
    prev = None
    filtered = None
    noisy_derivative_samples = [0.0, 0.0, 8.0, 0.0, 0.0]   # 가운데 하나만 큰 노이즈 스파이크
    for raw in noisy_derivative_samples:
        filtered = ema(filtered, raw, MARKER_SERVO_DERIV_LPF_ALPHA)
    # alpha=0.35 로 3번 감쇠된 스파이크 기여분만 남는다 — raw 스파이크(8.0)보다 훨씬 작다.
    assert filtered < 8.0 * 0.35   # 스파이크 나온 그 tick 의 최대 기여치보다도 작음


def test_map_axis_tol_clears_amcl_update_min_d_with_margin():
    """실측(2026-08-05): 옆축 목표 오차가 AMCL update_min_d(2cm) 보다 작으면(옛 값
    0.01m) AMCL 이 원리상 다시는 pose 를 안 내 LAT MOVE 가 매번 pose_stale 로
    죽는다(0.1cm/2.1cm 이동 요구 두 번 다 재현). 이 부등식이 깨지면 같은 결함이
    되돌아온다."""
    assert MAP_AXIS_TOL_M > AMCL_UPDATE_MIN_D_M


def test_marker_lost_grace_is_much_longer_than_frame_staleness():
    """둘은 서로 다른 것을 재므로 값이 같을 이유가 없다 — 한때 같은 값(0.4초)이었고
    그게 실패의 원인이었다(2026-08-05 실측):

      · FRAME_STALE_SEC   — 카메라 **영상이 끊겼나**. 짧아야 한다(죽은 화면으로 제어 금지).
      · MARKER_LOST_GRACE_SEC — 영상은 멀쩡한데 **마커가 화각에 없나**. 회전 잔상이
        가시고 좌우로 훑어 되찾을 시간이 필요하니 훨씬 길어야 한다.

    한 번 훑고도 남을 만큼(적어도 탐색 한 주기) 길어야 의미가 있다."""
    assert MARKER_LOST_GRACE_SEC > FRAME_STALE_SEC * 10
    sweep_period = 4.0 * MARKER_SEARCH_HALF_SPAN_RAD / MARKER_SEARCH_ANG
    assert MARKER_LOST_GRACE_SEC >= sweep_period, "한 주기도 못 훑고 포기한다"


def test_marker_servo_timeout_leaves_room_to_align_after_a_full_search():
    """탐색에 다 써 버리면 되찾은 순간 전체 시간이 끝나 정렬을 못 한다."""
    align_sec = MARKER_CENTER_STABLE_FRAMES / MARKER_SERVO_HZ
    assert MARKER_SERVO_TIMEOUT_SEC >= MARKER_LOST_GRACE_SEC + align_sec


def test_marker_search_sweeps_both_ways_and_returns_to_where_it_started():
    """실측(2026-08-05): 못 찾으면 가만히 서서 유예만 세다 죽었다("찾으러 가지도 않고
    못 찾았다고 한다"). 이제 훑는다 — 단, 한 방향으로 계속 돌면 못 찾았을 때 로봇이
    엉뚱한 데를 보고 끝나므로 **왕복**이어야 한다(한 주기 적분 = 0)."""
    quarter = MARKER_SEARCH_HALF_SPAN_RAD / MARKER_SEARCH_ANG
    period = 4.0 * quarter

    assert marker_search_angular(0.0) > 0                  # 한쪽으로 나갔다가
    assert marker_search_angular(1.5 * quarter) < 0        # 반대로 넘어오고
    assert marker_search_angular(3.5 * quarter) > 0        # 다시 제자리로

    # 한 주기를 적분하면 0 으로 돌아온다 — 훑고 나서 원래 보던 방향이다.
    dt = period / 2000.0
    swept = sum(marker_search_angular(i * dt) * dt for i in range(2000))
    assert abs(swept) < 1e-6

    # 편도 최대 진폭이 설정한 폭을 넘지 않는다(끝까지 갔을 때가 최대).
    peak = sum(marker_search_angular(i * dt) * dt
               for i in range(int(quarter / dt)))
    assert math.isclose(peak, MARKER_SEARCH_HALF_SPAN_RAD, rel_tol=1e-3)


def test_blind_travel_allows_more_time_the_slower_we_go():
    """AMCL 은 update_min_d(2cm)를 움직여야 pose 를 낸다 — 느리게 갈수록 그 간격이
    시간으로는 길어진다. 고정 시간으로 재면 느릴 때 억울하게 걸린다."""
    fast = blind_travel_stale_sec(0.05, cap_sec=20.0)     # 5cm/s
    slow = blind_travel_stale_sec(0.004, cap_sec=20.0)    # 4mm/s (접근 끝자락)
    assert slow > fast
    assert math.isclose(fast, AMCL_UPDATE_MIN_D_M / 0.05)


def test_blind_travel_never_exceeds_the_cap():
    assert blind_travel_stale_sec(0.0001, cap_sec=2.0) == 2.0


def test_stationary_robot_keeps_trusting_its_last_pose():
    """실측(2026-08-05, FINAL MOVE amcl_stale): 기준에 걸리면 멈춰서 기다리는데,
    안 움직이면 AMCL 이 영영 안 와서 교착이었다. 멈춘 로봇의 옛 pose 는 여전히 맞다
    (로봇이 그 자리에 있다) — 속도 0 이면 한도가 cap 까지 열려 교착이 안 생긴다."""
    assert blind_travel_stale_sec(0.0, cap_sec=20.0) == 20.0


def test_final_approach_end_speed_would_have_tripped_the_old_fixed_window():
    """왜 9/11 까지 다 통과하고 마지막에서만 났는지 — 끝자락 속도로는 2cm 가는 데
    옛 고정 기준(0.75초)의 몇 배가 걸린다. 이 시험이 그 산수를 붙들어 둔다."""
    end_speed = bounded_pid_linear(FINAL_APPROACH_TOL_M, FINAL_APPROACH_KP,
                                   FINAL_APPROACH_MAX_LINEAR_MPS)
    needed = blind_travel_stale_sec(end_speed, cap_sec=FINAL_APPROACH_TIMEOUT_SEC)
    assert needed > SENSOR_STATE_STALE_SEC


def test_marker_position_does_not_affect_the_map_ray_at_all():
    """최종 접근에서 마커를 **선택**으로 돌린 근거(2026-08-05 실기 지적):
    `USE_CAMERA_CALIBRATION=False` 면 bearing 이 u 와 무관하게 항상 0이라, 거리
    판정(PGM 광선 = AMCL yaw)에 마커가 아무 기여도 안 한다. 마커의 역할은 좌우
    보정뿐이다 — 서가에 붙어 화각을 벗어나도 접근을 계속할 수 있는 이유다.

    ⚠️ USE_CAMERA_CALIBRATION 을 True 로 돌리면 이 전제가 깨진다(그때는 bearing 이
    u 에 따라 달라져 마커가 거리 판정에 필요해진다) — 이 시험이 그걸 잡는다."""
    assert USE_CAMERA_CALIBRATION is False
    left = camera_center_and_bearing(10.0, K640, 640, 640)
    right = camera_center_and_bearing(630.0, K640, 640, 640)
    assert left == right                      # u 가 뭐든 같은 결과
    assert left[1] == 0.0                     # bearing 은 0
    assert left[0] == 320.0                   # cx 는 화면 정중앙


def test_map_yaw_tolerance_clears_amcl_update_min_a():
    """map 절대 yaw 회전은 AMCL yaw 로 닫는다 — 허용오차가 AMCL 의 각도 해상도
    (update_min_a)보다 작으면 **원리상 만족 못 하는 조건**이 된다(MAP_AXIS_TOL_M 이
    update_min_d 보다 작아 pose_stale 이 반복됐던 사고와 같은 모양)."""
    assert MAP_YAW_TOL_RAD > AMCL_UPDATE_MIN_A_RAD


def test_blind_travel_works_for_rotation_too():
    """같은 산수를 각속도에도 쓴다 — AMCL 은 update_min_a 만큼 돌아야 pose 를 낸다."""
    slow_spin = blind_travel_stale_sec(0.05, cap_sec=20.0, max_blind_m=AMCL_UPDATE_MIN_A_RAD)
    fast_spin = blind_travel_stale_sec(0.35, cap_sec=20.0, max_blind_m=AMCL_UPDATE_MIN_A_RAD)
    assert slow_spin > fast_spin
    assert math.isclose(fast_spin, AMCL_UPDATE_MIN_A_RAD / 0.35)


def test_turn_speed_matches_the_one_that_actually_moves_the_robot():
    """실측(2026-08-05): P 제어로 목표 근처에서 속도를 낮췄더니 정지마찰 때문에
    **로봇이 아예 안 돌았다**. 이 레포에서 실제로 돌던 값(MoveExecutor 의
    turn_speed=0.4, backup_runner.py:102)을 그대로 쓴다."""
    from app.core.backup_runner import MoveExecutor
    proven = MoveExecutor(publish_twist=lambda *_: None, pose_fn=lambda: (0.0, 0.0, 0.0))
    assert MAP_YAW_ANG == proven.turn_speed


def test_one_turn_tick_lands_inside_the_tolerance():
    """고정 속도라 오버슛이 문제다 — 한 tick 회전량이 허용오차보다 작아야 그 안에서
    멈출 수 있다(아니면 좌우로 영영 왔다갔다 한다)."""
    per_tick = MAP_YAW_ANG / MARKER_SERVO_HZ
    assert per_tick < MAP_YAW_TOL_RAD


def test_turn_settle_covers_more_than_one_camera_frame():
    """회전 뒤 프레임 게이트 — 최소한 프레임 한 장은 새로 들어올 시간이어야 한다."""
    assert TURN_SETTLE_SEC > 1.0 / MARKER_SERVO_HZ


def test_every_nested_helper_is_defined_before_it_is_called():
    """실측(2026-08-05): `turn_to_map_yaw` 를 정의보다 앞에서 불러
    `UnboundLocalError: cannot access local variable 'turn_to_map_yaw'` 로 도킹이
    통째로 죽었다. `_run()` 은 중첩 def 와 실행 본문이 **섞여 있어** 눈으로는 순서가
    잘 안 보인다(센서 대기·회전이 def 들 사이에 끼어 있다) — AST 로 못 박는다.

    파이썬은 이걸 import 시점에 못 잡는다. 실기에서 그 줄이 실행될 때만 터진다."""
    import ast
    import inspect
    from app.core import shelf_dock as sd

    tree = ast.parse(inspect.getsource(sd))
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_run")
    defined_at = {n.name: n.lineno for n in run.body if isinstance(n, ast.FunctionDef)}

    too_early = []
    for node in ast.walk(run):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in defined_at
                and node.lineno < defined_at[node.func.id]):
            too_early.append(f"{node.func.id}() at line {node.lineno} "
                             f"but defined at line {defined_at[node.func.id]}")
    assert not too_early, "정의보다 먼저 부른다(UnboundLocalError): " + "; ".join(too_early)


def test_fast_turn_gets_no_slack_from_the_distance_rule():
    """실측(2026-08-05, LAT MOVE 5/11 `amcl_stale`): 거리 기준(4차 수정)이 이 경로엔
    여유를 **하나도** 안 줬다. 빠르게 돌수록 거리 기준이 더 짧게 나와서
    `max(SENSOR_STATE_STALE_SEC, ...)` 의 옛 바닥값이 그대로 지배한다 — 그래서
    10/11 에서 고쳤다고 본 것이 5/11 로 자리만 옮겼다.

    다음에 또 임계값을 만지려 할 때 "거기선 안 통한다" 를 먼저 보이게 못 박는다.
    """
    slack = blind_travel_stale_sec(MAP_YAW_ANG, MAP_YAW_TIMEOUT_SEC, AMCL_UPDATE_MIN_A_RAD)
    assert slack < SENSOR_STATE_STALE_SEC
    assert max(SENSOR_STATE_STALE_SEC, slack) == SENSOR_STATE_STALE_SEC


def test_map_pose_from_odom_rotates_the_odom_delta_into_map():
    """odom 증분은 **map 프레임으로 돌려서** 얹어야 한다.

    로봇이 자기 정면으로 10cm 갔다. odom 에서는 방위가 0.5rad 이라 증분이 비스듬히
    찍히지만, map 에서는 방위가 0.0 이므로 결과는 정확히 x 로 +10cm 여야 한다.
    (돌리는 걸 빼먹으면 여기서 어긋난다.)
    """
    amcl = (1.0, 2.0, 0.0)
    odom_fix = (5.0, 5.0, 0.5)
    odom_now = (5.0 + 0.1 * math.cos(0.5), 5.0 + 0.1 * math.sin(0.5), 0.5)

    x, y, yaw = map_pose_from_odom(amcl, odom_fix, odom_now)

    assert x == pytest.approx(1.1, abs=1e-9)
    assert y == pytest.approx(2.0, abs=1e-9)
    assert yaw == pytest.approx(0.0, abs=1e-9)


def test_pose_is_known_through_an_amcl_gap_that_used_to_kill_lat_move():
    """`amcl_stale` 회귀 시험 — 이게 5·10·11 단계 실패의 공통 모양이다.

    LAT MOVE 의 회전은 0.4rad/s 로 ~90° 를 도는데, 그 사이 AMCL 이 0.75초(위
    시험이 못 박은 실제 기준)를 넘겨 조용하면 옛 코드는 자세를 **모른다고** 보고
    죽었다. 조용한 건 고장이 아니라 이벤트 토픽의 설계다 — 그동안 얼마나 돌았는지는
    odom 이 정확히 안다.
    """
    gap_sec = 0.9                      # 옛 기준 0.75초를 넘긴다
    turned = MAP_YAW_ANG * gap_sec     # 0.36 rad
    assert gap_sec > SENSOR_STATE_STALE_SEC

    pose = map_pose_from_odom((1.0, 2.0, 0.0), (5.0, 5.0, 0.5), (5.0, 5.0, 0.5 + turned))

    assert pose is not None, "AMCL 이 조용한 동안에도 자세는 알아야 한다"
    assert pose[0] == pytest.approx(1.0) and pose[1] == pytest.approx(2.0)
    assert pose[2] == pytest.approx(turned)


def test_dead_reckoning_still_gives_up_when_amcl_is_really_dead():
    """한도가 없으면 AMCL 이 죽어도 영영 못 알아챈다 — 거리로만 잡는다."""
    far = AMCL_DEAD_RECKON_MAX_M + 0.05
    assert map_pose_from_odom((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (far, 0.0, 0.0)) is None
    # 실측 옆축 이동(10~15cm)은 넉넉히 통과해야 한다.
    assert map_pose_from_odom((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.15, 0.0)) is not None


def test_no_angle_limit_because_docking_legitimately_turns_180_degrees():
    """각도 한도를 두면 **정상 회전 도중에 걸려 같은 버그를 되심는다.**

    처음 1.0rad 로 뒀다가 이 시험에 잡혔다: 도킹은 옆축 회전 90°(1.57rad)에 더해
    마지막 자세 회전을 `FINAL_YAW_RAD`(π)까지 돈다. 제자리 회전은 이동이 0 이라
    거리 한도에도 안 걸리므로, 아무리 많이 돌아도 자세는 알아야 한다.
    """
    for spun in (math.pi / 2, FINAL_YAW_RAD, -FINAL_YAW_RAD):
        pose = map_pose_from_odom((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, spun))
        assert pose is not None, f"{spun}rad 제자리 회전에서 자세를 잃었다"


def test_current_amcl_falls_back_to_dead_reckoning_instead_of_returning_none():
    """실제로 `amcl_stale` 을 없애는 지점은 `current_amcl()` 이다 — 그런데 그건
    `_run()` 안의 클로저라(ROS 핸들이 필요) 단위 시험이 못 닿는다. 배선만 AST 로
    못 박는다: 옛 코드처럼 "오래됐으면 `None`" 으로 되돌아가면 여기서 걸린다.

    같이 확인하는 것: `_on_amcl` 이 그 fix 와 짝이 되는 odom 을 남겨야 한다
    (안 남기면 이어 붙일 기준점이 없어 `map_pose_from_odom` 이 늘 `None` 이다).
    """
    import ast
    import inspect
    from app.core import shelf_dock as sd

    tree = ast.parse(inspect.getsource(sd))
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    called = {n.func.id for n in ast.walk(fns["current_amcl"])
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "resolve_map_pose" in called, \
        "current_amcl() 이 시험되는 판정(resolve_map_pose)을 안 쓴다 — 사본이 갈렸다"
    resolved = {n.func.id for n in ast.walk(fns["resolve_map_pose"])
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "map_pose_from_odom" in resolved, \
        "오래된 pose 를 odom 으로 이어 붙이지 않는다 — amcl_stale 재발"

    stored = {n.slice.value for n in ast.walk(fns["_on_amcl"])
              if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)}
    assert "odom" in stored, "_on_amcl 이 fix 시점 odom 을 안 남긴다"

    # 회전도 같은 함정이 있다 — 루프가 판정을 직접 재구현하면 시험이 아무것도 못 본다.
    turned = {n.func.id for n in ast.walk(fns["turn_to_map_yaw"])
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "map_turn_angular" in turned, \
        "turn_to_map_yaw 가 시험되는 판정(map_turn_angular)을 안 쓴다 — 사본이 갈렸다"
    assert "copysign" not in {n.func.attr for n in ast.walk(fns["turn_to_map_yaw"])
                              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}, \
        "회전 부호를 루프에서 다시 정하고 있다 — 반대편 고정이 우회된다"


# ─────────────────────────────────────────────────────────────────────────────
# 도킹 한 판 전체 재현 — AMCL 을 **이벤트 토픽 그대로** 모델링해서 굴린다.
#
# `amcl_stale` 네 번의 수정이 전부 실기에서만 드러난 이유는, 단위 시험이 "그 순간
# 하나" 만 봤기 때문이다. 실제로 죽인 건 **시간에 따라 벌어지는 간격**이었다
# (정지 2초 · 4mm/s 접근 · 회전 중 CPU 지연). 그래서 여기서는 한 판을 통째로 굴린다.
# ─────────────────────────────────────────────────────────────────────────────

AMCL_SCAN_HZ = 10.0          # 라이다 스캔 주기 — AMCL 은 이보다 빨리 못 낸다
ODOM_HZ = 30.0
_ODOM_OFFSET = (3.0, -1.0, 0.7)   # odom 원점이 map 에서 틀어져 있다(회전항을 실제로 태운다)


def _to_odom(map_pose):
    x, y, yaw = map_pose
    ox, oy, oyaw = _ODOM_OFFSET
    c, s = math.cos(oyaw), math.sin(oyaw)
    return (ox + x * c - y * s, oy + x * s + y * c, wrap_pi(yaw + oyaw))


def _dock_motion_profile():
    """실측 로그(2026-08-05 영상: 옆축 14.6cm, 최종 6cm)의 도킹 한 판을 재현한다."""
    dt = 1.0 / MARKER_SERVO_HZ
    steps = []

    def hold(sec):                      # 중앙정렬 확인 등 **정지** 구간
        steps.extend([(dt, 0.0, 0.0)] * int(round(sec / dt)))

    def turn(rad):                      # bang-bang 고정 각속도(MAP_YAW_ANG)
        w = math.copysign(MAP_YAW_ANG, rad)
        steps.extend([(dt, 0.0, w)] * int(round(abs(rad) / (MAP_YAW_ANG * dt))))

    def drive(dist):                    # PID — 목표에 가까울수록 느려진다
        remaining = dist
        while remaining > 0.0005:
            v = min(MAP_AXIS_MAX_LINEAR_MPS, MAP_AXIS_KP * remaining)
            steps.append((dt, v, 0.0))
            remaining -= v * dt

    hold(2.0)                # CENTER1 — 정지한 채 30프레임 정렬 확인
    turn(math.pi / 2)        # 옆축 방향으로
    drive(0.146)             # LAT MOVE
    hold(1.0)                # LAT DONE
    turn(-math.pi / 2)       # 서가 방향 복귀
    hold(2.0)                # CENTER2
    drive(0.060)             # FINAL MOVE — 끝에서 4mm/s 까지 느려진다
    return steps


def _replay(resolve, stall_from=None, stall_sec=0.0):
    """도킹 한 판을 굴리며 매 tick `resolve()` 를 부른다.

    `(잃은 횟수, 최대 오차 m, 최대 각오차 rad, AMCL 최대 침묵 s)` 를 돌려준다.
    `stall_from` 은 그 시각부터 `stall_sec` 동안 AMCL 을 통째로 멈춘다(Pi CPU 지연).
    """
    now = 0.0
    truth = (0.0, 0.0, 0.0)
    amcl_state, odom_state = {}, {}
    since_d = since_a = 0.0
    next_scan = next_odom = 0.0
    last_pub = 0.0
    lost = 0
    max_dxy = max_dyaw = worst_gap = 0.0

    for dt, v, w in _dock_motion_profile():
        x, y, yaw = truth
        yaw = wrap_pi(yaw + w * dt)
        truth = (x + v * dt * math.cos(yaw), y + v * dt * math.sin(yaw), yaw)
        since_d += abs(v) * dt
        since_a += abs(w) * dt
        now += dt

        if now >= next_odom:            # odom 은 움직임과 무관하게 계속 온다
            odom_state["pose"], odom_state["at"] = _to_odom(truth), now
            next_odom += 1.0 / ODOM_HZ

        stalled = stall_from is not None and stall_from <= now < stall_from + stall_sec
        if now >= next_scan:
            next_scan += 1.0 / AMCL_SCAN_HZ
            # AMCL 은 update_min_d/a 를 넘게 움직여야만 낸다 — 이게 이벤트 토픽의 정의다.
            if not stalled and (since_d >= AMCL_UPDATE_MIN_D_M or since_a >= AMCL_UPDATE_MIN_A_RAD):
                amcl_state["pose"], amcl_state["at"] = truth, now
                amcl_state["odom"] = odom_state.get("pose")
                since_d = since_a = 0.0
                last_pub = now
        worst_gap = max(worst_gap, now - last_pub)

        if not amcl_state:
            continue                     # 아직 첫 fix 전 — 도킹은 여기서 시작도 안 한다
        got = resolve(amcl_state, odom_state, now, SENSOR_STATE_STALE_SEC)
        if got is None:
            lost += 1
            continue
        max_dxy = max(max_dxy, math.hypot(got[0] - truth[0], got[1] - truth[1]))
        max_dyaw = max(max_dyaw, abs(wrap_pi(got[2] - truth[2])))
    return lost, max_dxy, max_dyaw, worst_gap


def _old_resolve(amcl_state, odom_state, now, stale_sec):
    """고치기 **전** 판정 그대로 — 시험에 이빨이 있는지 확인하는 대조군."""
    pose = amcl_state.get("pose")
    if pose is None or not is_pose_fresh(amcl_state.get("at"), now, stale_sec):
        return None
    return pose


def test_the_replay_actually_reproduces_the_old_failure():
    """대조군 확인 — 이 시나리오가 옛 코드를 실제로 죽여야 시험에 의미가 있다.

    안 죽으면 아래 시험은 아무것도 검증하지 않는 초록불이다(CLAUDE.md: `FakeDriver`
    가 늘 "running" 이라 옛 코드에서도 초록이던 사고와 같은 함정).
    """
    lost, _dxy, _dyaw, worst_gap = _replay(_old_resolve)
    assert worst_gap > SENSOR_STATE_STALE_SEC, "AMCL 침묵이 옛 기준을 못 넘겼다 — 재현 실패"
    assert lost > 0, "옛 판정이 안 죽었다 — 시나리오가 버그를 재현하지 못한다"


def test_dock_never_loses_the_pose_and_stays_accurate():
    """도킹 한 판 내내 자세를 한 번도 안 잃고, AMCL 이 조용한 동안도 정확하다."""
    lost, max_dxy, max_dyaw, worst_gap = _replay(resolve_map_pose)
    assert lost == 0, f"{lost} tick 에서 자세를 잃었다(최대 침묵 {worst_gap:.2f}s)"
    # odom 이 정확하면 이어 붙인 자세도 정확해야 한다 — 회전 오프셋까지 포함해서.
    assert max_dxy < 1e-6, f"위치 오차 {max_dxy:.6f}m"
    assert max_dyaw < 1e-6, f"각도 오차 {max_dyaw:.6f}rad"


def test_pose_survives_an_amcl_stall_during_the_lateral_turn():
    """영상(2026-08-05 18:01)의 LAT MOVE 실패 — 회전 중 AMCL 이 잠깐 멎은 경우.

    회전 구간의 기준은 `max(0.75, blind_travel_stale_sec(0.4, ..., 0.02))` = 0.75초라
    Pi 가 잠깐 밀리기만 해도 죽었다. 이제는 그 동안 얼마나 돌았는지를 odom 이 안다.
    """
    stall_start = 2.5      # 정지 2초 뒤 옆축 회전 한복판
    old_lost, *_ = _replay(_old_resolve, stall_from=stall_start, stall_sec=1.2)
    new_lost, dxy, dyaw, _gap = _replay(resolve_map_pose, stall_from=stall_start, stall_sec=1.2)
    assert old_lost > 0, "대조군이 안 죽었다 — 이 시험은 아무것도 안 본다"
    assert new_lost == 0, f"AMCL 이 1.2초 멎자 {new_lost} tick 에서 자세를 잃었다"
    assert dxy < 1e-6 and dyaw < 1e-6


def test_odom_loss_is_still_a_real_failure():
    """AMCL 이 조용한 건 봐주지만 **odom 이 끊긴 건 진짜 고장**이다 — 계속 실패해야 한다."""
    amcl = {"pose": (1.0, 2.0, 0.0), "at": 0.0, "odom": (0.0, 0.0, 0.0)}
    odom_dead = {"pose": (0.5, 0.0, 0.0), "at": 0.0}
    now = SENSOR_STATE_STALE_SEC + 1.0          # AMCL·odom 둘 다 오래됐다
    assert resolve_map_pose(amcl, odom_dead, now) is None
    # odom 만 살아 있으면 이어 붙인다(한도 안의 증분으로).
    odom_live = {"pose": (0.05, 0.0, 0.0), "at": now}
    assert resolve_map_pose(amcl, odom_live, now) == pytest.approx((1.05, 2.0, 0.0))


def test_amcl_stays_the_absolute_reference():
    """AMCL 을 안 쓰는 게 아니다 — 위치의 절대 기준은 언제나 마지막 AMCL fix 다.

    odom 은 그 fix **이후의 증분**만 얹는다. 그래서 (1) 안 움직였으면 결과는 AMCL
    원본 그대로이고, (2) 새 fix 가 오면 그동안 쌓인 odom 증분과 무관하게 즉시
    그쪽으로 갈린다 — odom 드리프트가 누적되지 않는다.
    """
    amcl = {"pose": (1.0, 2.0, 0.3), "at": 10.0, "odom": (5.0, 5.0, 0.9)}

    # (1) fix 이후 odom 이 그대로면 AMCL 원본과 완전히 같다.
    still = {"pose": (5.0, 5.0, 0.9), "at": 10.0}
    assert resolve_map_pose(amcl, still, 10.1) == pytest.approx((1.0, 2.0, 0.3))
    # 오래돼도(옛 코드가 죽던 조건) 마찬가지다 — 안 움직였으니 그 자리다.
    still_later = {"pose": (5.0, 5.0, 0.9), "at": 99.0}
    assert resolve_map_pose(amcl, still_later, 99.0) == pytest.approx((1.0, 2.0, 0.3))

    # (2) odom 이 20cm 흐른 뒤에도, 새 AMCL fix 가 오면 그 자리가 곧 정답이다.
    drifted = {"pose": (5.2, 5.0, 0.9), "at": 20.0}
    assert resolve_map_pose(amcl, drifted, 20.0) != pytest.approx((1.0, 2.0, 0.3))
    refixed = {"pose": (7.7, 7.7, 0.1), "at": 20.0, "odom": (5.2, 5.0, 0.9)}
    assert resolve_map_pose(refixed, drifted, 20.0) == pytest.approx((7.7, 7.7, 0.1))


# ─── map 절대 yaw 회전이 "잘 도는가" ─────────────────────────────────────────
# 회전은 언제나 map 좌표 기준이다 — 매 tick 목표와 현재 AMCL yaw 의 최단 오차만
# 본다(그동안 얼마나 돌았는지는 안 센다). 그 판정이 `_run()` 안 클로저에만 있어서
# 시험이 못 닿던 걸 `map_turn_angular()` 로 빼냈다.

def _spin_to(target, start, hz=MARKER_SERVO_HZ, max_sec=20.0):
    """제어법을 그대로 돌려 `(도달했나, 걸린 초, 방향이 바뀐 횟수, 총 회전량)`."""
    dt, yaw, t = 1.0 / hz, start, 0.0
    stable = flips = 0
    prev_sign = 0
    turned = 0.0
    while t < max_sec:
        ang = map_turn_angular(target, yaw)
        if ang == 0.0:
            stable += 1
            if stable >= MAP_YAW_STABLE_TICKS:
                return True, t, flips, turned
        else:
            stable = 0
            sign = 1 if ang > 0 else -1
            if prev_sign and sign != prev_sign:
                flips += 1
            prev_sign = sign
            yaw = wrap_pi(yaw + ang * dt)
            turned += abs(ang) * dt
        t += dt
    return False, t, flips, turned


def test_map_turn_converges_from_every_start_angle():
    """어느 자세에서 시작해도 목표 map yaw 로 수렴하고, 좌우로 왔다갔다 하지 않는다."""
    target = FINAL_YAW_RAD
    for i in range(72):                       # 5° 간격 한 바퀴
        start = wrap_pi(-math.pi + i * math.tau / 72)
        ok, secs, flips, turned = _spin_to(target, start)
        assert ok, f"start={start:.3f}rad 에서 수렴 못 함"
        assert flips == 0, f"start={start:.3f}rad 에서 좌우가 {flips}번 뒤집혔다"
        # 최단경로여야 한다 — 한 바퀴 돌아가면 안 된다.
        #
        # 예산에 `2 × 반대편 고정폭`이 들어간다. 반대편 근처에서 오차가 음수로
        # 접혔는데 방향을 양수로 고정하면 `2π-|오차|` 를 도는데, 그 차이가 최대
        # 이만큼이다(≤0.2rad≈11°). 재현성을 사는 대가이고, 그 구간 밖에서는 0 이다.
        budget = (abs(map_heading_error(target, start))
                  + 2 * MAP_YAW_ANTIPODE_MARGIN_RAD    # 반대편 고정의 대가
                  + MAP_YAW_TOL_RAD                     # 허용오차 안에서 멈춘다
                  + MAP_YAW_ANG / MARKER_SERVO_HZ)      # 한 tick 오버슛
        assert turned <= budget, f"start={start:.3f}rad: {turned:.3f} > {budget:.3f}"


def test_map_turn_direction_is_fixed_at_the_antipode():
    """정확히 반대편에서는 `wrap_pi` 경계라 AMCL 잡음 몇 mrad 로 부호가 뒤집힌다.

    회전량은 어느 쪽이든 같으니 손해가 없다 — 대신 **항상 같은 쪽**으로 고정해
    재현 가능하게 만든다. 안 그러면 같은 명령이 어떤 날은 좌, 어떤 날은 우로 돈다.
    """
    target = 0.0
    for noise in (-1e-4, -1e-6, 0.0, 1e-6, 1e-4):
        # 목표에서 정확히 π 떨어진 자세(+잡음)
        ang = map_turn_angular(target, wrap_pi(math.pi + noise))
        assert ang > 0.0, f"잡음 {noise} 에서 방향이 갈렸다: {ang}"


def test_map_turn_antipode_margin_clears_one_tick():
    """고정 구간이 한 tick 회전량보다 넓어야 경계를 한 번에 벗어난다.
    좁으면 벗어나기 전에 다시 경계 판정에 걸려 의미가 없다."""
    assert MAP_YAW_ANTIPODE_MARGIN_RAD > MAP_YAW_ANG / MARKER_SERVO_HZ


def test_map_turn_stops_inside_tolerance():
    assert map_turn_angular(1.0, 1.0) == 0.0
    assert map_turn_angular(1.0, 1.0 - MAP_YAW_TOL_RAD * 0.9) == 0.0
    assert map_turn_angular(1.0, 1.0 - MAP_YAW_TOL_RAD * 1.5) != 0.0


def test_final_turn_directions_for_the_real_shelves():
    """실기 배치에서 마지막 회전이 실제로 어느 쪽인지 못 박는다.

    도킹 끝 자세는 `SHELF_YAW + EXTRA_TURN_RAD` 이고 목표는 `FINAL_YAW_RAD`(π).
    서가마다 시작 자세가 다르니 좌/우가 갈리는 게 정상이다 — 종료 **자세**가 같아야
    팔이 같은 조건에서 일한다(FINAL_YAW_RAD 주석). 어느 쪽이든 최단경로다.
    """
    got = {}
    for shelf, yaw in SHELF_YAW.items():
        start = wrap_pi(yaw + EXTRA_TURN_RAD)
        ang = map_turn_angular(FINAL_YAW_RAD, start)
        got[shelf] = "왼쪽" if ang > 0 else "오른쪽"
        ok, _secs, flips, _turned = _spin_to(FINAL_YAW_RAD, start)
        assert ok and flips == 0, f"{shelf}: 수렴 실패 또는 방향 흔들림"
    # +Y 를 보는 두 서가는 왼쪽, -Y 를 보는 서가는 오른쪽 — 둘 다 π 까지 최단이다.
    assert got["문학서가"] == "왼쪽" and got["예술서가"] == "왼쪽"
    assert got["과학-인문학서가"] == "오른쪽"
