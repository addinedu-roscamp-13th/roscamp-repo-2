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


# ── 하드 스톱 ────────────────────────────────────────────────────────────────


def _front_at(dist):
    """전방 아크만 `dist`, 나머지는 멀리."""
    scan = [10.0] * 360
    for d in range(-15, 16):
        scan[d % 360] = dist
    return scan


def test_hard_stop_kills_forward_inside_stop_dist():
    """비례 감속만으로는 안 선다 — 계수가 0 이 되는 건 거리 0 에서다."""
    lin, _ = apply_avoidance(0.12, 0.0, _front_at(0.10), _cfg(STOP_DIST=0.25))
    assert lin == 0.0


def test_hard_stop_leaves_reverse_alone():
    """후진까지 **0 으로 끊으면** 너무 붙었을 때 빠져나올 수단이 사라진다.

    ⚠️ 기존 비례 감속은 후진에도 걸린다(전방 장애물 기준으로 부호와 무관하게 곱한다).
    그건 이 변경 이전부터의 동작이라 그대로 둔다 — 여기서 보장하는 건
    "하드 스톱이 후진을 죽이지 않는다" 하나다.
    """
    lin, _ = apply_avoidance(-0.05, 0.0, _front_at(0.10), _cfg(STOP_DIST=0.25))
    assert lin < 0.0, "후진이 살아 있어야 한다"


def test_hard_stop_off_when_zero():
    """STOP_DIST=0 이면 기존 동작(비례 감속만) 그대로다."""
    lin, _ = apply_avoidance(0.12, 0.0, _front_at(0.10), _cfg(STOP_DIST=0.0))
    assert lin > 0.0


def test_outside_stop_dist_only_scales():
    """정지선 밖에서는 끊지 않고 줄이기만 한다."""
    lin, _ = apply_avoidance(0.12, 0.0, _front_at(0.30), _cfg(STOP_DIST=0.25))
    assert lin == 0.12          # MIN_DIST(0.20) 밖이라 감속도 없다
