from types import SimpleNamespace
from libi_perception.lidar_avoidance import apply_avoidance


def _cfg(**over):
    base = dict(MIN_DIST=0.20, AVOID_DIST=0.40, AVOID_KP=0.50,
                FRONT_ARC_DEG=15, SIDE_ARC=(20, 71), ANGULAR_Z_MAX=0.60)
    base.update(over)
    return SimpleNamespace(**base)


def _clear_scan():
    return [10.0] * 360


def test_no_scan_unchanged():
    assert apply_avoidance(0.1, 0.2, [], _cfg()) == (0.1, 0.2)


def test_clear_path_unchanged():
    lin, ang = apply_avoidance(0.1, 0.2, _clear_scan(), _cfg())
    assert abs(lin - 0.1) < 1e-9
    assert abs(ang - 0.2) < 1e-9


def test_front_obstacle_slows_down():
    scan = _clear_scan()
    scan[0] = 0.10               # 0.10 < MIN_DIST 0.20 -> factor 0.5
    lin, _ = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert abs(lin - 0.05) < 1e-6


def test_left_obstacle_steers_right():
    scan = _clear_scan()
    scan[45] = 0.20              # inside SIDE_ARC (20..71), < AVOID_DIST
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang < 0              # steer right (negative) away from left wall


def test_right_obstacle_steers_left():
    scan = _clear_scan()
    scan[315] = 0.20            # mirror side (360-45), < AVOID_DIST
    _, ang = apply_avoidance(0.10, 0.0, scan, _cfg())
    assert ang > 0
