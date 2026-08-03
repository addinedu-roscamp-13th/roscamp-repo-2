import math


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def angle_deadzone(cfg) -> float:
    """방위 정지 구간의 반폭(px).

    비율(`ANGLE_DEADZONE_FRAC`)이 있으면 그쪽이 이기고, 없으면 고정 픽셀로 떨어진다.
    `control_loop` 도 "사라질 때 가운데였나"를 같은 기준으로 판정해야 하므로 —
    두 곳에 같은 식을 적어 두면 한쪽만 고쳐져 조용히 어긋난다 — 여기 한 번만 쓴다.
    """
    dz_frac = getattr(cfg, "ANGLE_DEADZONE_FRAC", None)
    return (cfg.IMAGE_WIDTH * dz_frac) if dz_frac is not None else cfg.ANGLE_DEADZONE


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

    def compute(self, cx, area, dt):
        """검출이 보낸 **카메라 원본 픽셀을 그대로** 쓴다. 해상도 환산 없다.

        ## [2026-08-02] 환산을 걷어냈다

        예전에는 `image_width` 를 받아 `k = cfg.IMAGE_WIDTH / image_width` 를 곱해
        모든 좌표를 640 기준으로 끌어올렸다(2026-07-30, 카메라를 640→320 으로 내리면서
        옛 튜닝값을 살리려고 넣은 것). 계산 자체는 정확했지만 화면에서 눈으로 잰
        픽셀과 `config.py` 의 숫자가 늘 2배 달라 튜닝할 때마다 암산이 필요했다.
        **사용자 지시로 제거했다** — 이제 화면에서 잰 값이 곧 설정값이다.

        ⚠️ 대신 `config.py` 의 픽셀 상수가 **카메라 해상도에 묶였다.** 해상도를
           바꾸는 날 `IMAGE_WIDTH`·`TARGET_SIZE`·`DIST_DEADZONE`·`ANGLE_DEADZONE`·
           `INTEGRAL_*_CLAMP` 와 게인을 손으로 같이 옮겨야 한다. 안 옮기면
           2026-07-30 증상(화면을 꽉 채워도 목표에 못 닿아 전진이 안 멈춤,
           중심이 어긋나 한쪽으로 계속 돔)이 **에러 없이** 되돌아온다.
        """
        cfg = self.cfg
        dt = dt if dt > 0 else 1e-3

        # distance -> linear_x
        size = math.sqrt(max(0.0, area))
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
        # ⚠️ **방위 데드존은 화면 비율로 잡는다 — 고정 픽셀이 아니다.**
        #
        #   화면의 3등분 가이드선은 `w//3`·`2w//3` 에 그려지므로 가운데 칸은 언제나
        #   **중심 ±w/6** 이다. 비율로 두면 눈에 보이는 그 칸이 그대로 정지 구간이다.
        #   `ANGLE_DEADZONE_FRAC = 1/6` 이 기본. 0 이면 구간을 끈다.
        deadzone = angle_deadzone(cfg)
        e_cx = (cfg.IMAGE_WIDTH / 2.0) - cx

        # ── 정지 구간 ─────────────────────────────────────────────────────
        #
        # 규칙은 화면 그대로다: **bbox 중심이 3등분 가운데 칸 안이면 안 돈다.
        # 칸을 벗어나면 돈다.** 그 이상도 이하도 아니어야 한다.
        #
        # ⚠️ [2026-08-02] **히스테리시스(`ANGLE_RESUME_RATIO`)를 걷어냈다.**
        #
        #   1.3 이면 "칸을 30% 더 벗어나야 비로소 돈다"가 된다. 320 폭에서 칸 경계는
        #   53.3px 인데 실제로 도는 문턱은 69.3px 이라, 그 사이 16px 은 **사람이
        #   가이드선 밖으로 명백히 나갔는데도 로봇이 안 도는** 구간이었다.
        #   실측 2026-08-02: 사람이 왼쪽 칸에 확실히 있는 화면(e_cx=75.5)을 두고
        #   "이게 가운데냐"는 질문이 나왔다 — 화면과 제어가 어긋나면 화면이 거짓말을
        #   한다. 눈에 보이는 선이 곧 문턱이어야 한다.
        #
        # 히스테리시스가 있었던 이유는 진짜였다. 문턱 하나로는 둘 중 하나가 깨진다:
        #   · 오차를 빼내면 → 경계 바로 밖에서 명령이 0 에 가까워 **안 돈다**
        #     (실측: -0.009 rad/s 에 odom 이 ±0.013 으로 떨기만 했다 — 정지 마찰)
        #   · 빼지 않으면 → 경계에서 0 → `KP × 53.3` 으로 튀고, 그 반동으로 칸
        #     안팎을 오가며 **좌우로 깨작인다**("도리도리")
        #
        # 그래서 문턱을 옮기는 대신 **원인인 정지 마찰을 직접 보상한다**(아래
        # `ANGULAR_Z_MIN`). 오차는 거리축과 똑같이 **빼내서** 경계에서 0 부터
        # 이어지게 하고, 0 이 아닌 명령은 바퀴가 실제로 도는 크기까지 올린다.
        # 튐도 없고, 힘없이 못 도는 것도 없고, 화면 선이 진짜 문턱이 된다.
        if abs(e_cx) <= deadzone:
            e_cx = 0.0
            self._i_cx = 0.0
            self._prev_cx = 0.0
        else:
            e_cx -= math.copysign(deadzone, e_cx)
        self._i_cx = clamp(self._i_cx + e_cx * dt,
                           -cfg.INTEGRAL_ANGLE_CLAMP, cfg.INTEGRAL_ANGLE_CLAMP)
        d_cx = (e_cx - self._prev_cx) / dt
        self._prev_cx = e_cx
        target_ang = (cfg.KP_ANGLE * e_cx + cfg.KI_ANGLE * self._i_cx
                      + cfg.KD_ANGLE * d_cx)
        s = cfg.ANGULAR_SMOOTHING
        self._ang = s * target_ang + (1.0 - s) * self._ang
        ang = clamp(self._ang, -cfg.ANGULAR_Z_MAX, cfg.ANGULAR_Z_MAX)

        # ── 정지 마찰 보상 ────────────────────────────────────────────────
        #
        # 바퀴는 어느 크기 아래로는 **아예 안 돈다.** 그 아래 명령을 내는 것은
        # 도는 것도 서는 것도 아닌 상태 — odom 만 떨고 로봇은 제자리다.
        # 돌기로 정했으면(구간 밖) 실제로 돌아야 한다.
        #
        # ⚠️ 데드존 **안**에서는 절대 걸리면 안 된다. `e_cx = 0` 이면 `target_ang`
        #    이 0 이고 저역통과가 남은 값을 0 으로 끌어내리는 중인데, 여기서 최소값을
        #    씌우면 **가운데 칸에서 영원히 도는** 로봇이 된다. 그래서 원오차가 아니라
        #    **빼낸 뒤의 `e_cx`** 로 판정한다.
        ang_min = getattr(cfg, "ANGULAR_Z_MIN", 0.0)
        if ang_min > 0 and e_cx != 0.0 and 0 < abs(ang) < ang_min:
            ang = math.copysign(ang_min, ang)

        return lin, ang
