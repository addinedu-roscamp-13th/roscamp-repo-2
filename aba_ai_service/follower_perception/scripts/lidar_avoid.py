"""LiDAR obstacle avoidance for the simple drive path (bang-bang, no PID).

Pure logic (no ROS) so it's testable and swappable for follower_control's
lidar_avoidance later. cmd_bridge feeds it the LaserScan and applies the result
right before publishing /cmd_vel (robot-local, so the safety loop stays fast).

Rules:
  BRAKE (block translation head-on in the travel direction):
    moving forward & front < STOP_DIST  -> linear_x = 0
    moving back    & back  < STOP_DIST  -> linear_x = 0
  AVOID (side): a wall within SIDE_AVOID overrides the rotation to point AWAY
    from it, so the robot never turns toward it; if both sides are within, it
    avoids the closer one. Runs while moving or rotating; skipped when parked
    (0,0) so it never spins in place when idle.

Note: the followed person is also "in front" — keep STOP_DIST well BELOW the
follow distance so the owner at follow distance doesn't trigger a stop.
"""
import math

STOP_DIST = 0.10          # m: front/back brake — block translation if closer than this
SIDE_AVOID = 0.10         # m: a side wall within this -> steer AWAY (and never toward)
SIDE_DRIFT = 0.12         # rad/s: angular used to steer away from a side wall


def _norm(deg):
    while deg > 180.0:
        deg -= 360.0
    while deg < -180.0:
        deg += 360.0
    return deg


def sector_min(ranges, angle_min, angle_inc, lo_deg, hi_deg):
    """Min valid range (m) over angles in [lo_deg, hi_deg]. inf if none."""
    best = math.inf
    for i, r in enumerate(ranges):
        if not (r and math.isfinite(r) and r > 0.0):
            continue
        a = _norm(math.degrees(angle_min + i * angle_inc))
        if lo_deg <= a <= hi_deg:
            best = min(best, r)
    return best


def sectors8(ranges, angle_min, angle_inc, flip_180=False):
    """The eight 45-deg sectors as a dict, keyed:
        front / front_left / left / back_left / back / back_right / right / front_right
    flip_180=True remaps each sector to its 180-deg opposite (LiDAR mounted flipped).
    """
    def m(lo, hi):
        return sector_min(ranges, angle_min, angle_inc, lo, hi)
    s = {
        "front":       m(-22.5, 22.5),
        "front_left":  m(22.5, 67.5),
        "left":        m(67.5, 112.5),
        "back_left":   m(112.5, 157.5),
        "back":        min(m(157.5, 180.0), m(-180.0, -157.5)),
        "back_right":  m(-157.5, -112.5),
        "right":       m(-112.5, -67.5),
        "front_right": m(-67.5, -22.5),
    }
    if flip_180:                                   # each sector <- its 180-deg opposite
        s = {
            "front":       s["back"],       "back":        s["front"],
            "left":        s["right"],      "right":       s["left"],
            "front_left":  s["back_right"], "back_right":  s["front_left"],
            "front_right": s["back_left"],  "back_left":   s["front_right"],
        }
    return s


def sectors4(ranges, angle_min, angle_inc, flip_180=False):
    """(front, back, left, right) for avoidance. left/right = MIN over their three
    sub-sectors so a diagonal obstacle can't slip a gap; front/back stay narrow
    (±22.5 deg) for head-on braking. See sectors8 for the full breakdown.
    """
    s = sectors8(ranges, angle_min, angle_inc, flip_180=flip_180)
    left = min(s["front_left"], s["left"], s["back_left"])
    right = min(s["front_right"], s["right"], s["back_right"])
    return s["front"], s["back"], left, right


def avoid_cmd(linear_x, angular_z, front, back, left, right):
    """Brake head-on, and steer AWAY from any side wall within SIDE_AVOID.

    Returns (linear_x, angular_z, reason): "clear" | "front" | "back" | "avoid".
    A wall within SIDE_AVOID on a side OVERRIDES rotation to point away from it
    (so it never turns toward it); if both sides are within, it avoids the closer
    one. Runs while moving or rotating; skipped only when parked (0,0).
    """
    reason = "clear"
    active = (linear_x != 0.0) or (angular_z != 0.0)     # incoming command, not parked
    if linear_x > 0.0 and front < STOP_DIST:
        linear_x = 0.0
        reason = "front"
    elif linear_x < 0.0 and back < STOP_DIST:
        linear_x = 0.0
        reason = "back"
    if active:
        l_near = left < SIDE_AVOID
        r_near = right < SIDE_AVOID
        if l_near and r_near:
            angular_z = -SIDE_DRIFT if left <= right else SIDE_DRIFT  # away from closer
            reason = "avoid"
        elif l_near:
            angular_z = -SIDE_DRIFT                       # left wall -> steer right
            reason = "avoid"
        elif r_near:
            angular_z = SIDE_DRIFT                        # right wall -> steer left
            reason = "avoid"
    return linear_x, angular_z, reason
