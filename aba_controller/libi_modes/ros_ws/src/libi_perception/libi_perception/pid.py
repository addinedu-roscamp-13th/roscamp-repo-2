import math


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class FollowPID:
    """Distance PID (sqrt-area) -> linear_x (with reverse); bearing PID (cx) -> angular_z."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.reset()

    def reset(self):
        self._i_size = 0.0
        self._prev_size = 0.0
        self._i_cx = 0.0
        self._prev_cx = 0.0
        self._ang = 0.0

    def compute(self, cx, area, dt):
        cfg = self.cfg
        dt = dt if dt > 0 else 1e-3

        # distance -> linear_x
        size = math.sqrt(max(0.0, area))
        e = cfg.TARGET_SIZE - size
        self._i_size = clamp(self._i_size + e * dt,
                             -cfg.INTEGRAL_DIST_CLAMP, cfg.INTEGRAL_DIST_CLAMP)
        d = (e - self._prev_size) / dt
        self._prev_size = e
        lin = cfg.KP_DIST * e + cfg.KI_DIST * self._i_size + cfg.KD_DIST * d
        lin = clamp(lin, -cfg.LINEAR_X_REVERSE_MAX, cfg.LINEAR_X_MAX)

        # bearing -> angular_z
        e_cx = (cfg.IMAGE_WIDTH / 2.0) - cx
        if abs(e_cx) < cfg.ANGLE_DEADZONE:
            e_cx = 0.0
        self._i_cx = clamp(self._i_cx + e_cx * dt,
                           -cfg.INTEGRAL_ANGLE_CLAMP, cfg.INTEGRAL_ANGLE_CLAMP)
        d_cx = (e_cx - self._prev_cx) / dt
        self._prev_cx = e_cx
        target_ang = (cfg.KP_ANGLE * e_cx + cfg.KI_ANGLE * self._i_cx
                      + cfg.KD_ANGLE * d_cx)
        s = cfg.ANGULAR_SMOOTHING
        self._ang = s * target_ang + (1.0 - s) * self._ang
        ang = clamp(self._ang, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)

        return lin, ang
