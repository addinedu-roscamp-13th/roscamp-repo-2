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
        #: 지금 방위를 잡는 중인가. 히스테리시스가 이걸로 문턱을 고른다(compute 주석).
        self._turning = False

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
        # 목표 근처에서는 아예 멈춘다.
        #
        # ⚠️ **구간 밖에서는 0 으로 만드는 게 아니라 빼낸다.** 방위각처럼 그냥 0 으로
        #    죽이면 경계 바로 밖에서 `KP × DIST_DEADZONE` 이 통째로 튀어나온다 —
        #    28px 이면 0.084 m/s 로, 최대(0.12)의 70% 다. 서 있던 로봇이 사람이 한
        #    걸음 물러난 순간 그 속도로 튄다. 빼내면 경계에서 0 부터 이어진다.
        #    (원본 `cmd_preview.SIZE_DEADBAND` 는 bang-bang 이라 밖이 늘 고정속도였고,
        #     그래서 이 문제가 없었다. PID 로 옮기면서 생긴 차이다.)
        #
        # 구간 안에서는 **적분과 미분 기억을 둘 다 턴다.**
        #   · 적분: 오차만 0 으로 만들면 `KI × I` 가 남아 정지 구간에서도 명령이
        #     완전히 0 이 되지 않고, 구간을 넘나들 때 예전에 쌓인 적분이 되살아나
        #     사람 쪽으로 밀린다.
        #   · 미분: `_prev_size` 를 안 지우면 구간에 **들어오는 첫 프레임**에
        #     `d = (0 - 직전오차)/dt` 가 한 번 튄다. 지금은 `KD_DIST = 0` 이라
        #     출력이 0 이지만, D 를 켜는 순간 "멈춰야 할 때 한 번 튀는" 버그가
        #     조용히 살아난다. 지금 막아 둔다.
        if abs(e) <= cfg.DIST_DEADZONE:
            e = 0.0
            self._i_size = 0.0
            self._prev_size = 0.0
        else:
            e -= math.copysign(cfg.DIST_DEADZONE, e)
        self._i_size = clamp(self._i_size + e * dt,
                             -cfg.INTEGRAL_DIST_CLAMP, cfg.INTEGRAL_DIST_CLAMP)
        d = (e - self._prev_size) / dt
        self._prev_size = e
        lin = cfg.KP_DIST * e + cfg.KI_DIST * self._i_size + cfg.KD_DIST * d
        lin = clamp(lin, -cfg.LINEAR_X_REVERSE_MAX, cfg.LINEAR_X_MAX)

        # bearing -> angular_z
        # cx 는 위에서 기준 폭으로 환산했으므로 중심도 기준 폭 기준이다.
        # ⚠️ [2026-08-01] **방위 데드존은 화면 비율로 잡는다 — 고정 픽셀이 아니다.**
        #
        #   화면의 3등분 가이드선은 `w//3`·`2w//3` 에 그려지므로 가운데 칸은 언제나
        #   **중심 ±w/6** 이다. 이걸 640 기준 고정 픽셀(106.67)로 두면 해상도가 바뀔
        #   때마다 값을 다시 계산해야 하고, 실제로 화면에 보이는 선과 어긋난다.
        #   비율로 두면 320이든 640이든 **눈에 보이는 그 칸**이 그대로 정지 구간이다.
        #
        #   `ANGLE_DEADZONE_FRAC = 1/6` 이 기본. 0 이면 구간을 끈다.
        dz_frac = getattr(cfg, "ANGLE_DEADZONE_FRAC", None)
        angle_deadzone = (cfg.IMAGE_WIDTH * dz_frac) if dz_frac is not None \
            else cfg.ANGLE_DEADZONE
        e_cx = (cfg.IMAGE_WIDTH / 2.0) - cx
        # [2026-08-01] 거리축과 **같은 방식**으로 바꿨다 — 0 으로 죽이지 않고 빼낸다.
        #
        # 예전에는 구간 밖에서 오차를 통째로 썼다. `ANGLE_DEADZONE` 이 45 일 때는
        # 경계 바로 밖에서 `KP_ANGLE × 45 = 0.045 rad/s` 라 티가 덜 났는데, 가운데
        # 칸(106.67)으로 넓히면서 그게 **0.107 rad/s** 가 됐다 — 사람이 칸 경계를
        # 한 픽셀 넘는 순간 그 속도로 튄다. 빼내면 경계에서 0 부터 이어진다.
        # (거리축 주석에 같은 논리가 있다. 그쪽은 2026-07-31 에 이미 고쳤고
        #  "방위각처럼 그냥 0 으로 죽이면" 이라며 여기를 남은 문제로 적어 뒀었다.)
        #
        # ── 정지 구간 + 히스테리시스 ──────────────────────────────────────
        #
        # 규칙은 화면 그대로다: **bbox 중심이 3등분 가운데 칸 안이면 안 돈다.**
        #
        # 경계를 하나만 두면 둘 중 하나가 반드시 깨진다:
        #   · 오차를 빼내면 → 칸 경계 바로 밖에서 명령이 0 에 가까워 **안 돈다**
        #     (실측: -0.009 rad/s 에 odom 이 ±0.013 으로 떨기만 했다)
        #   · 빼지 않으면 → 경계에서 0 → 0.107 로 튀고, 그 반동으로 칸 안팎을
        #     오가며 **좌우로 계속 깨작인다**(사용자 표현: "도리도리")
        #
        # 그래서 **멈추는 선과 다시 도는 선을 다르게** 둔다.
        #   · 도는 중  → 가운데 칸(`angle_deadzone`) 안에 들어오면 멈춘다
        #   · 멈춘 상태 → 칸을 `ANGLE_RESUME_RATIO` 배만큼 확실히 벗어나야 다시 돈다
        # 한 번 벗어나면 **오차를 통째로 쓴다** — 돌기로 했으면 실제로 돌아야 한다.
        # 되돌아오는 문턱이 더 멀리 있으므로 경계에서 켜졌다 꺼졌다 하지 않는다.
        #
        # `ANGLE_RESUME_RATIO = 1.0` 이면 히스테리시스가 없다(문턱 하나).
        resume = angle_deadzone * getattr(cfg, "ANGLE_RESUME_RATIO", 1.0)
        threshold = angle_deadzone if self._turning else resume
        if abs(e_cx) <= threshold:
            self._turning = False
            e_cx = 0.0
            self._i_cx = 0.0
            self._prev_cx = 0.0
        else:
            self._turning = True
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
