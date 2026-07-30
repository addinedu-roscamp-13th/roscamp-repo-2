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

    def compute(self, cx, area, dt, image_width=None):
        """image_width 를 주면 그 해상도로 들어온 값을 **기준 폭(cfg.IMAGE_WIDTH)으로 환산**한다.

        검출이 자기 해상도를 실어 보내면 그게 항상 옳다 — cfg 는 추측일 뿐이다.

        ## [2026-07-30] 상수를 고치는 대신 입력을 환산한다

        이 PID 의 튜닝값은 **전부 640 폭 기준 픽셀**이다:
        `TARGET_SIZE=280`, `KP_DIST=0.0030`, `ANGLE_DEADZONE=45`, `INTEGRAL_*_CLAMP`.
        그런데 카메라 해상도를 320x240 으로 내리자 같은 사람의 bbox 가 **선형으로 절반**이
        됐다. 그러면 두 가지가 동시에 깨진다:

          · 거리: `sqrt(w*h)` 가 절반 → 화면을 꽉 채워도 123 이라 **목표 280 에 영영 못 닿는다.**
            오차가 계속 양수라 전진이 안 멈춘다 — 2026-07-28 에 겪은 "그냥 들이박네"와 같은 조건.
          · 조향: 중심을 640/2=320 으로 잡는데 실제 중심은 160 → 사람이 늘 왼쪽에 있다고 본다.

        상수를 해상도마다 다시 튜닝하면 그 표가 또 어긋난다. 대신 **들어온 픽셀을 기준
        폭으로 환산**하면 아래 수식과 튜닝값이 하나도 안 바뀐다. 640 에서는 k=1 이라
        예전과 **완전히 같은 계산**이다.

        ⚠️ 이게 동작하려면 검출 쪽이 `image_width` 를 실어 보내야 한다. 안 보내면 k=1 로
           떨어져 예전(=해상도가 640 이라는 가정) 그대로다.
        """
        cfg = self.cfg
        dt = dt if dt > 0 else 1e-3
        width = image_width if image_width else cfg.IMAGE_WIDTH
        # 기준 폭 환산 계수. 320 폭이면 k=2.0 → 픽셀값을 640 기준으로 되돌린다.
        # 길이에 선형이므로 cx 와 sqrt(area) 에 같은 k 를 곱한다(면적이 아니라 변 길이다).
        k = cfg.IMAGE_WIDTH / float(width) if width else 1.0
        cx = cx * k

        # distance -> linear_x
        size = math.sqrt(max(0.0, area)) * k
        e = cfg.TARGET_SIZE - size
        self._i_size = clamp(self._i_size + e * dt,
                             -cfg.INTEGRAL_DIST_CLAMP, cfg.INTEGRAL_DIST_CLAMP)
        d = (e - self._prev_size) / dt
        self._prev_size = e
        lin = cfg.KP_DIST * e + cfg.KI_DIST * self._i_size + cfg.KD_DIST * d
        lin = clamp(lin, -cfg.LINEAR_X_REVERSE_MAX, cfg.LINEAR_X_MAX)

        # bearing -> angular_z
        # cx 는 위에서 기준 폭으로 환산했으므로 중심도 기준 폭 기준이다.
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
