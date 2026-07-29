from .pid import clamp


def apply_avoidance(linear_x, angular_z, scan, cfg):
    """Post-process PID output with LiDAR: front slowdown + side shy-away."""
    if not scan:
        return linear_x, angular_z
    n = len(scan)
    step = n / 360.0

    def arc_min(deg_iter):
        idx = [int(i * step) % n for i in deg_iter]
        vals = [scan[i] for i in idx if scan[i] and scan[i] > 0.05]
        return min(vals) if vals else 10.0

    # front arc: proportional slowdown, then a hard stop
    front = arc_min(range(-cfg.FRONT_ARC_DEG, cfg.FRONT_ARC_DEG + 1))
    if front < cfg.MIN_DIST:
        linear_x *= max(0.0, front / cfg.MIN_DIST)
    # 비례 감속만으로는 안 선다 — 계수가 0 이 되는 건 거리 0 에서다.
    # 그 아래에 끊는 선을 둔다. **전진만** 막는다: 후진까지 막으면 너무 붙었을 때
    # 빠져나올 수단이 사라진다.
    stop_dist = getattr(cfg, "STOP_DIST", 0.0)
    if stop_dist > 0 and front < stop_dist and linear_x > 0:
        linear_x = 0.0

    # side arcs: shy away
    lo, hi = cfg.SIDE_ARC
    left = arc_min(range(lo, hi))
    right = arc_min(range(360 - hi + 1, 360 - lo + 1))
    steer = 0.0
    if left < cfg.AVOID_DIST:
        steer -= (cfg.AVOID_DIST - left) * cfg.AVOID_KP    # wall on left -> steer right
    if right < cfg.AVOID_DIST:
        steer += (cfg.AVOID_DIST - right) * cfg.AVOID_KP   # wall on right -> steer left
    angular_z = clamp(angular_z + steer, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)

    return linear_x, angular_z
