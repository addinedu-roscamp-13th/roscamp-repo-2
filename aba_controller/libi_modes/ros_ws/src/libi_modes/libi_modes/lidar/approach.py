"""라이다 도킹 국면 상태기계 — 센서도 통신도 모른다.

    SETTLE ──▶ ACQUIRE ──▶ ALIGN ──▶ APPROACH ──▶ FINAL ──▶ DONE
                  │           └──────────┴──────────┘
                  └────────────────── ABORT ◀────── 이탈·타임아웃·과주행

## 탐색(SEARCH) 국면이 없다

기존 ArUco 도킹은 마커가 화각(±30°)을 벗어나면 좌우 7걸음을 훑었다. 라이다는 360도를
보므로 **못 찾는 이유가 시야가 아니다.** 확정에 실패하면 회전하지 않고 실패로 뺀다.

## 제자리 회전을 하지 않는다

각속도 데드밴드가 두 개다 — 정지 중 제자리 회전은 0.16 rad/s 아래로 안 돌고, 주행 중
조향은 0.08 로도 먹는다(정지마찰이 이미 깨져 있어서다). 그래서 항상 후진하면서
조향하고, 펄스가 쉬는 구간에는 조향도 0 을 낸다 — 안 도는 명령을 내는 것은 잡음이다.

## 감속을 속도가 아니라 듀티로 한다

모터 데드밴드가 0.05 m/s 다. 그 아래로 적으면 명령은 나가는데 바퀴가 안 돈다. 그래서
가까워지면 **짧게 밀고 멈춰서 다시 보기**를 반복한다. 한 번 미는 시간을 남은 거리에
비례시키면 저절로 수렴하고, 마지막 한 걸음이 정확히 최소 이동량(5mm)이 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from libi_modes.lidar.config import LidarDockConfig
from libi_modes.lidar.detect import NotchObs


@dataclass(frozen=True)
class Cmd:
    linear: float       # m/s. **항상 ≤ 0** — 후진 도킹에서 전진은 언제나 버그다
    angular: float      # rad/s
    phase: str
    done: bool
    reason: str


class LidarApproach:
    """관측을 먹여 명령을 받는다. 상태는 전부 이 안에 있다."""

    def __init__(self, cfg: LidarDockConfig):
        self.cfg = cfg
        self.phase = "SETTLE"
        self._t0 = None
        self._phase_t0 = None
        self._confirm = 0
        self._lost = 0
        self._ema: NotchObs | None = None
        self._ema_at: float | None = None
        self._pulse_until = 0.0
        self._pulse_next = 0.0

    # ── 도우미 ────────────────────────────────────────────────────────────
    def _stop(self, phase: str, reason: str) -> Cmd:
        self.phase = phase
        return Cmd(0.0, 0.0, phase, phase in ("DONE", "ABORT"), reason)

    def _enter(self, phase: str, now_s: float) -> None:
        if phase != self.phase:
            self.phase = phase
            self._phase_t0 = now_s

    def _filter(self, obs: NotchObs, now_s: float) -> NotchObs | None:
        """EMA + 튐 게이트. 튄 프레임은 **버린다**(상실로 센다).

        한 프레임의 오검출이 그대로 조향에 실리면 로봇이 홱 튼다.

        ## ⚠️ 게이트는 **경과 시간에 비례**한다 (codex P1 #5)

        고정 5cm 게이트는 두 방향으로 틀린다: 스캔이 몇 프레임 빠진 뒤의 **정상**
        관측이 5cm 를 넘어 버려지고, 반대로 정지 중의 4.9cm **오검출**은 통과한다.
        물리적 상한(`jump_speed_max_mps`)에 경과 시간을 곱해 판정한다.

        ## FAR ↔ NEAR 전환도 이 게이트를 통과해야 한다

        인수인계 시점의 `d` 가 이어지지 않으면 NEAR 가 엉뚱한 것을 물었다는 뜻이다.
        """
        c, prev = self.cfg, self._ema
        if prev is None:
            self._ema = obs
            self._ema_at = now_s
            return obs
        dt = max(now_s - (self._ema_at if self._ema_at is not None else now_s), 1e-3)
        d_gate = c.jump_speed_max_mps * dt
        if abs(obs.d - prev.d) > d_gate or abs(obs.y - prev.y) > c.jump_gate_y_m:
            return None
        # yaw 는 NEAR 로 넘어가면 0 으로 떨어지므로 전환 프레임에서 게이트를 안 건다.
        if not obs.near and not prev.near and abs(obs.yaw - prev.yaw) > c.jump_gate_yaw_rad:
            return None
        k = c.ema_coef
        self._ema = NotchObs(
            d=k * obs.d + (1 - k) * prev.d,
            y=k * obs.y + (1 - k) * prev.y,
            yaw=0.0 if obs.near else k * obs.yaw + (1 - k) * prev.yaw,
            depth=obs.depth,
            wall_m=obs.wall_m if obs.near else k * obs.wall_m + (1 - k) * prev.wall_m,
            near=obs.near,
        )
        self._ema_at = now_s
        return self._ema

    def _linear(self, d: float, now_s: float) -> float:
        """후진 속도의 **크기**. 펄스 구간에서는 쉬는 동안 0 을 돌려준다."""
        c = self.cfg
        if d > c.v_far_dist_m:
            return c.v_far_mps
        if d > c.pulse_dist_m:
            return c.v_near_mps
        if now_s >= self._pulse_next:
            remain = max(d - c.stop_m, 0.0)
            t = min(max(remain / c.v_near_mps, c.pulse_min_s), c.pulse_max_s)
            self._pulse_until = now_s + t
            self._pulse_next = now_s + t + c.pulse_pause_s
        return c.v_near_mps if now_s < self._pulse_until else 0.0

    def _angular(self, obs: NotchObs, lin_mag: float) -> float:
        c = self.cfg
        if lin_mag == 0.0:
            # 정지 중에는 0.08 rad/s 로 바퀴가 안 돈다. 안 도는 명령은 안 낸다.
            return 0.0
        k_yaw = {"ALIGN": c.k_yaw_align,
                 "APPROACH": c.k_yaw_approach}.get(self.phase, c.k_yaw_final)
        raw = c.steer_sign * (c.k_y * obs.y + k_yaw * obs.yaw)
        return max(-c.ang_max_rad_s, min(c.ang_max_rad_s, raw))

    # ── 본체 ──────────────────────────────────────────────────────────────
    def step(self, obs: NotchObs | None, now_s: float) -> Cmd:
        c = self.cfg
        if self._t0 is None:
            self._t0, self._phase_t0 = now_s, now_s

        if self.phase in ("DONE", "ABORT"):
            return self._stop(self.phase, "finished")

        if now_s - self._t0 >= c.timeout_s:
            return self._stop("ABORT", "timeout")

        # ── SETTLE — nav2 잔여 속도가 죽기를 기다린다 ──────────────────────
        if self.phase == "SETTLE":
            if now_s - self._t0 < c.settle_sec:
                return Cmd(0.0, 0.0, "SETTLE", False, "settling")
            self._enter("ACQUIRE", now_s)

        # ⚠️ **FINAL 이 아니면 NEAR 관측을 받지 않는다.** NEAR 에는 직선 피팅이라는
        #    오검출 방어가 없어 노치 없는 벽을 노치로 읽는다(실측 100%). 도킹이
        #    오검출로 **시작**되지 않게 하는 구조적 방어(SETTLE·ACQUIRE)일 뿐 아니라,
        #    ALIGN·APPROACH 에서도 NEAR 의 `y` 는 못 믿는다 — 노치 가장자리가 창
        #    밖으로 잘려 좌우 오차가 커진다(실측: 2cm→12mm, 3cm→21mm). 그 값이 FINAL
        #    진입 문턱(`y_max_enter_final_m`, ≤15mm)을 오염시키면 안 된다. NEAR 는
        #    FAR 가 확정하고 추적해 온 뒤 FINAL 에서의 인수인계일 때만 유효하다.
        if obs is not None and obs.near and self.phase != "FINAL":
            obs = None
        filtered = self._filter(obs, now_s) if obs is not None else None

        # ── ACQUIRE — 정지 상태에서 확정. 회전하지 않는다 ──────────────────
        if self.phase == "ACQUIRE":
            if filtered is None:
                self._confirm = 0
                if now_s - self._phase_t0 >= c.acquire_timeout_s:
                    return self._stop("ABORT", "notch_not_found")
                return Cmd(0.0, 0.0, "ACQUIRE", False, "searching")
            self._confirm += 1
            if self._confirm < c.confirm_frames:
                return Cmd(0.0, 0.0, "ACQUIRE", False, "confirming")
            self._enter("ALIGN", now_s)

        # ── 주행 국면 ─────────────────────────────────────────────────────
        if filtered is None:
            self._lost += 1
            if self._lost > c.lost_frames_max:
                return self._stop("ABORT", "notch_lost")
            return Cmd(0.0, 0.0, self.phase, False, "lost")
        self._lost = 0

        if filtered.d < c.stop_m - c.overshoot_margin_m:
            return self._stop("ABORT", "overshoot")

        # ⚠️ **정지 조건이 `d <= stop_m` 이 아니다** (codex P0 #4, 시뮬로 확인).
        #
        # 한 걸음보다 적게 남았으면 그 걸음을 밟는 순간 지나친다 — 불감시간(스캔 주기 +
        # 명령 지연 + 관성)을 이길 수 없기 때문이다. 초안대로 `d <= stop_m` 에서
        # 멈추면 실제로는 목표를 10~14mm 지나쳤고 과주행 중단이 40/60 이었다.
        # 이 한 줄이 그것을 0/60 으로 바꾼다. 대신 **모자란 쪽으로 치우친다** —
        # 도킹에서는 그쪽이 안전하고, 남은 편향은 `stop_m` 실측이 흡수한다.
        min_step = c.pulse_min_s * c.v_near_mps
        if filtered.d - c.stop_m < min_step:
            return self._stop("DONE", "arrived")

        # ── 국면 선택 ─────────────────────────────────────────────────────
        # 벽이 min range 아래로 가기 전에 yaw 를 끝낸다. `near` 관측은 정의상
        # 벽이 없는 상태이므로 항상 FINAL 이다.
        wall_gone = filtered.near or not (filtered.wall_m >= c.wall_alive_min_m)
        if wall_gone:
            if self.phase not in ("FINAL",):
                # ⚠️ FINAL 진입 문턱 — FAR 가 살아 있는 동안 정렬을 끝내야 한다.
                #    NEAR 는 근거리에서 노치 가장자리가 창 밖으로 잘려 좌우 오차가
                #    커진다(실측: 좌우 2cm → 12mm, 3cm → 21mm). 어긋난 채 넘기지 않는다.
                if abs(filtered.y) > c.y_max_enter_final_m:
                    return self._stop("ABORT", "not_aligned_for_final")
            self._enter("FINAL", now_s)
        elif filtered.d <= c.v_far_dist_m:
            self._enter("APPROACH", now_s)
        else:
            self._enter("ALIGN", now_s)

        if self.phase == "FINAL" and abs(filtered.y) > c.y_max_final_m:
            return self._stop("ABORT", "lateral_too_large")

        lin_mag = self._linear(filtered.d, now_s)
        ang = self._angular(filtered, lin_mag)
        return Cmd(-lin_mag, ang, self.phase, False, "driving")
