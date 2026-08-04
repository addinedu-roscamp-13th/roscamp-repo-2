"""서가 도킹 계산. 카메라·ROS 없이 합성 프레임과 합성 격자로 본다."""
import math

import numpy as np
import pytest

from app.shelf.raycast import Grid
from app.core.shelf_dock import (CLEARANCE_M, EXTRA_TURN_RAD, FINAL_YAW_RAD,
                                 MARKER_SERVO_MAX_ANG, SHELF_YAW, USE_CAMERA_CALIBRATION,
                                 visual_servo_angular_z, plan_dock, shelf_axes, axis_projection,
                                 bounded_pid_linear, MAP_AXIS_MAX_LINEAR_MPS, dock_status_payload,
                                 map_heading_error)

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


def test_shelf_yaw_table_has_exactly_the_two_demo_shelves():
    assert set(SHELF_YAW) == {"문학서가", "과학-인문학서가"}
    assert math.isclose(SHELF_YAW["문학서가"], 1.5708)
    assert math.isclose(SHELF_YAW["과학-인문학서가"], -1.5708)


def test_constants_match_the_spec():
    assert math.isclose(EXTRA_TURN_RAD, 0.3491)
    assert math.isclose(FINAL_YAW_RAD, 3.1416)
    assert math.isclose(CLEARANCE_M, 0.02)


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
