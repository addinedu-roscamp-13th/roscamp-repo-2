"""라이다 도킹 국면 상태기계 — 센서도 통신도 모른다.

    SETTLE ──▶ SEARCH ──▶ ACQUIRE ──▶ ALIGN ──▶ APPROACH ──▶ FINAL ──▶ DONE
                  │           │           └──────────┴──────────┘
                  │                       ALIGN ◀── RETREAT ◀── 정렬 게이트(재시도 남으면)
                  └───────────┴────────────────── ABORT ◀────── 이탈·타임아웃·과주행(재시도 소진)

## SEARCH 국면 — 사전 회전을 안 믿는다

## [2026-08-03] 이전에는 "탐색 국면이 없다"였다 — 실측으로 뒤집혔다

기존 설계 근거: "라이다는 360도를 보므로 못 찾는 이유가 시야가 아니다. 확정 실패하면
회전하지 않고 실패로 뺀다." 이 가정은 **접근 전 회전(FaceApproachYaw)이 정확하다**는
것을 전제한다. 그런데 실측(pinky-3)에서 그 회전이 목표각에 못 미친 채 다음 단계로
넘어온 사례가 나왔다(벽 yaw -40.7도, ACQUIRE 의 허용치 35도 초과 — `notch_not_found`
로 반복 실패). nav2 자체 회전 정밀도에 기대는 대신, 라이다가 스스로 훨씬 넓은 창
(`search_half_deg`)으로 **벽만** 찾고(노치는 아직 안 찾는다) 그 방향으로 제자리
회전해 정렬한 뒤에야 ACQUIRE 로 넘어간다. `search_align_tol_rad` 안에 들어오면 그
즉시 ACQUIRE 로 넘기므로, 이후 흐름(확정→정렬→접근→최종)은 이전과 완전히 같다.

⚠️ [2026-08-04] "벽+노치를 찾는다"가 아니라 **"벽만" 찾는다** — 처음엔 노치까지
확정된 `obs.yaw` 로 돌렸는데, 각도가 심하게 틀어지면 벽은 잡혀도 **노치 자체가 그
각도에서 인식이 안 됐다**(라이다가 노치 바닥이 아니라 옆벽을 스치거나 깊이·폭이
찌그러져 찍힌다). 그러면 `obs` 가 계속 `None` 이라 SEARCH 가 방향 신호를 영영
못 받고 무한정 스핀만 했다. 벽은 노치보다 훨씬 넓은 각도에서 잡히므로(아래
`search_wall_yaw_max_rad`), 방향 결정과 노치 확정을 분리했다 — 자세한 이유는
`step()` 의 SEARCH 블록 주석.

## 제자리 회전을 하지 않는다 (SEARCH 제외)

각속도 데드밴드가 두 개다 — 정지 중 제자리 회전은 0.16 rad/s 아래로 안 돌고, 주행 중
조향은 0.08 로도 먹는다(정지마찰이 이미 깨져 있어서다). ACQUIRE 이후로는 항상
후진하면서 조향하고, 펄스가 쉬는 구간에는 조향도 0 을 낸다 — 안 도는 명령을 내는
것은 잡음이다. **SEARCH 만 예외다** — 아직 후진을 시작 못 했으니(방향을 모른다)
정지 상태에서 도는 수밖에 없고, 그래서 `search_rot_rad_s` 는 회전 데드밴드
(`ROTATE_DEADBAND_RAD_S`) 위로 강제된다(`config.py:clamped`).

## 감속을 속도가 아니라 듀티로 한다

모터 데드밴드가 0.05 m/s 다. 그 아래로 적으면 명령은 나가는데 바퀴가 안 돈다. 그래서
가까워지면 **짧게 밀고 멈춰서 다시 보기**를 반복한다. 한 번 미는 시간을 남은 거리에
비례시키면 저절로 수렴하고, 마지막 한 걸음이 정확히 최소 이동량(5mm)이 된다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from libi_modes.lidar.config import LidarDockConfig
from libi_modes.lidar.detect import NotchObs


@dataclass(frozen=True)
class Cmd:
    linear: float       # m/s. **항상 ≤ 0** — 후진 도킹에서 전진은 언제나 버그다.
                        # 유일한 예외: `phase=="RETREAT"`(정렬 게이트 재시도로
                        # 짧게 물러날 때만, `_retry_or_abort()` 참고).
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
        #: `not_aligned_for_final` 게이트를 **딱 한 번, 벽이 진짜 사라진 순간에만**
        #: 물으려고 — 선제 FINAL 진입(아래 `step()` 참고) 시점에 물으면 아직 교정할
        #: 거리가 남았는데 성급하게 ABORT 된다(실측: y=-12mm, y_max=15mm 인데도
        #: ABORT — 그 시점의 filtered.y 는 아직 더 컸다).
        self._final_gate_checked = False
        #: [2026-08-04, codex 검토] 근접 구간에서 "정렬됐다"를 연속 N프레임으로
        #: 확인하려는 카운터·시각. 한 프레임(노이즈 있는 EMA 값)만 보고 판단
        #: 하지 않는다.
        self._waypoint_confirm = 0
        self._waypoint_hold_t0: float | None = None
        self._waypoint_confirmed_once = False
        #: [2026-08-04, codex 검토 2차] 웨이포인트 존(`stop_m+waypoint_margin_m`)에
        #: **처음 들어오는 그 프레임**에 딱 한 번 정렬을 확인한다 — 실기(yaw=15.6°,
        #: d=112mm)에서 안 맞은 채로 존에 들어와 놓고도 아무도 안 막아서 뒤늦게
        #: FINAL 의 `lateral_too_large` 로만 잡혔다. `_final_gate_checked` 는
        #: `wall_really_gone` 에 걸려 이보다 훨씬 늦게(또는 다른 타이밍에) 돈다 —
        #: 별도 플래그다.
        self._waypoint_gate_checked = False
        #: `_angular()` 의 각속도 변화율 제한이 쓰는 이전 출력·시각.
        self._prev_ang = 0.0
        self._ang_at: float | None = None
        #: [2026-08-04, 사용자 지적] `(d,y)` 를 재는 기준선(그 프레임의 RANSAC
        #: `wall.yaw`) 자체가 프레임마다 노이즈로 흔들린다 — 로봇도 벽도
        #: 안 움직여도 `y` 가 흔들린다는 뜻이다(§10 yaw-y 상관관계의 실제
        #: 원인일 가능성). 느리게만 따라가는 기준 yaw 를 따로 들고 있다가
        #: `_stabilize()` 가 그걸로 다시 투영한다.
        self._wall_yaw_ref: float | None = None
        #: [2026-08-04, 사용자 요청] 정렬 게이트가 막았을 때 바로 ABORT 하는
        #: 대신 몇 번은 물러났다 재시도한다 — 아래 `_retry_or_abort()`/RETREAT.
        self._retry_count = 0
        self._retreat_until: float | None = None

    # ── 도우미 ────────────────────────────────────────────────────────────
    def _stop(self, phase: str, reason: str) -> Cmd:
        self.phase = phase
        return Cmd(0.0, 0.0, phase, phase in ("DONE", "ABORT"), reason)

    def _enter(self, phase: str, now_s: float) -> None:
        if phase != self.phase:
            self.phase = phase
            self._phase_t0 = now_s

    def _retry_or_abort(self, reason: str, now_s: float) -> Cmd:
        """정렬 게이트가 막았을 때 바로 ABORT 하는 대신, 남은 재시도가 있으면
        RETREAT(짧은 전진)로 물러났다가 ALIGN 으로 다시 시도한다.

        [2026-08-04, 사용자 요청] `Cmd.linear` 는 시스템 전체에서 "항상 ≤0"
        불변식이다(전진은 도크 충돌 버그) — 그 불변식을 전역으로 푸는 게
        아니라, **이 트리거가 걸렸을 때만** 예외적으로 RETREAT 국면 안에서
        짧게 전진한다. 물러나면 거리가 늘어나 `_angular()` 의 `k_yaw_far_scale`
        우선순위 램프가 다시 "먼 구간"으로 보고 재정렬 기회를 준다. 이전
        시도의 게이트·정렬확인 상태(래치·카운터)는 재시도가 그 잔여 상태에
        오염되지 않게 전부 초기화한다.
        """
        c = self.cfg
        if self._retry_count >= c.waypoint_retry_max:
            return self._stop("ABORT", reason)
        self._retry_count += 1
        self.phase = "RETREAT"
        self._retreat_until = now_s + c.waypoint_retreat_s
        self._waypoint_gate_checked = False
        self._final_gate_checked = False
        self._waypoint_confirm = 0
        self._waypoint_hold_t0 = None
        self._waypoint_confirmed_once = False
        return Cmd(c.v_near_mps, 0.0, "RETREAT", False, reason)

    def _stabilize(self, obs: NotchObs) -> NotchObs:
        """`(d,y)` 를 잰 기준선을 프레임마다 새로 재지 않는다.

        [2026-08-04, 사용자 지적] `detect()` 는 매 프레임 새로 RANSAC 한
        `wall.yaw` 를 기준으로 `(d,y)` 를 계산한다 — 그 벽 각도 추정 자체가
        RANSAC 표본 노이즈로 프레임마다 조금씩 흔들리면, 로봇도 벽도 안
        움직였는데 **기준선이 흔들려서** `y` 가 흔들린다. §10 에서 확인한
        "yaw 클수록 y 오차 큼"(1도당 2.4mm) 상관관계도 이 메커니즘으로
        설명된다 — 기준 각도 오차가 그대로 `y` 오차로 새는 것이지, 로봇
        위치 자체의 오차가 아닐 수 있다.

        느리게만 따라가는 기준 yaw(`self._wall_yaw_ref`) 를 따로 들고,
        이번 프레임의 `(d,y)` 를 원시 좌표로 복원한 뒤 그 안정된 기준으로
        다시 투영한다(작은 각도 근사가 아니라 정확한 회전 — 접근 초반엔
        기준과의 차이가 작지 않을 수 있다).

        ⚠️ `obs.yaw` 자체는 안 건드린다 — 그건 조향(`k_yaw*yaw`)이 쓰는
        실측 헤딩 오차라 반응성이 필요하다. 이 함수가 안정시키는 건 "d·y
        를 무엇을 기준으로 쟀는가" 뿐이다.

        NEAR 관측은 애초에 벽을 안 맞춘다(라이다 원점 기준 그대로) — 재투영
        할 기준 자체가 없으므로 그대로 돌려준다. **FINAL 도 마찬가지로
        건너뛴다** — 그 국면은 벽면이 min range 아래로 사라지는 중이라
        `obs.yaw` 자체가 이미 못 믿을 값이다(`k_yaw_final=0` 인 이유와 같은
        근거). 못 믿는 yaw 로 재투영하면 오히려 y 에 그 잡음을 그대로
        옮겨 심는다(실기: yaw=0.5 한 프레임에 y 가 0→38mm 로 튀어
        `lateral_too_large` 오탐 유발 확인) — 안정화가 반대 효과를 낸다.
        """
        c = self.cfg
        if obs.near or self.phase == "FINAL":
            return obs
        if self._wall_yaw_ref is None:
            self._wall_yaw_ref = obs.yaw
            return obs
        k = c.wall_yaw_ref_coef
        self._wall_yaw_ref = k * obs.yaw + (1 - k) * self._wall_yaw_ref
        delta = obs.yaw - self._wall_yaw_ref
        cd, sd = math.cos(delta), math.sin(delta)
        d_ref = obs.d * cd - obs.y * sd
        y_ref = obs.d * sd + obs.y * cd
        return replace(obs, d=d_ref, y=y_ref)

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

    def _angular(self, obs: NotchObs, lin_mag: float, now_s: float) -> float:
        c = self.cfg
        if lin_mag == 0.0:
            # 정지 중에는 0.08 rad/s 로 바퀴가 안 돈다. 안 도는 명령은 안 낸다.
            return 0.0
        k_yaw = {"ALIGN": c.k_yaw_align,
                 "APPROACH": c.k_yaw_approach}.get(self.phase, c.k_yaw_final)
        # ⚠️ [2026-08-04, 사용자 지적] `k_y*y + k_yaw*yaw` 를 그냥 더하면 두
        #    항이 서로 싸운다 — y 를 줄이려면 로봇이 **잠깐 비스듬히** 틀어야
        #    하는데(움직이면서 도는 것만이 y 를 바꾼다, 위 참고), yaw 항이
        #    동시에 그 비스듬함을 계속 0 으로 당긴다. 실기로 확인: d=343mm
        #    에서 yaw 는 이미 +0.4°(거의 완벽)인데 y 는 +29mm 로 그대로였다
        #    — yaw 게인을 올릴수록(5차 조정) y 가 틀 기회를 못 잡는
        #    역효과였다. 주차처럼 멀 때는 각을 허용해 y 부터 줄이고,
        #    존 경계에 가까워질수록 yaw 를 조여 똑바로 세운다 — 거리
        #    비례로 `k_yaw` 자체를 낮은 값(`k_yaw_far_scale` 비율)에서
        #    본래 값으로 램프업한다. ALIGN·APPROACH 에만 적용 — FINAL 은
        #    애초에 `k_yaw_final=0` 이라 무의미하다.
        if self.phase in ("ALIGN", "APPROACH"):
            near_d = c.stop_m + c.waypoint_margin_m
            far_d = c.v_far_dist_m
            span = max(far_d - near_d, 1e-6)
            priority = max(0.0, min(1.0, (far_d - obs.d) / span))  # 0=멀다, 1=존 경계
            k_yaw *= c.k_yaw_far_scale + (1.0 - c.k_yaw_far_scale) * priority
        raw = c.steer_sign * (c.k_y * obs.y + k_yaw * obs.yaw)
        # ⚠️ [2026-08-04, codex 검토 2차] `stop_m+waypoint_margin_m` 존에서
        #    조향을 0 으로 뚝 끊었더니(1차) 그 전에 못 맞춘 y·yaw 를 회수할
        #    방법이 없어져 실기에서 FINAL `lateral_too_large` 로 터졌다(존
        #    진입 자체가 정렬 안 된 채로도 가능했다 — 아래 `step()` 의 존
        #    진입 게이트가 그건 따로 막는다). y/yaw 피드백은 그대로 두고,
        #    대신 존 안에서는 상한만 **거리에 비례해 부드럽게** 줄인다 —
        #    `d=stop_m` 에서 0, `d=stop_m+margin` 에서 원래 상한. 급격한
        #    마지막 회전(사용자 스케치가 막으려던 그 경로)은 여전히 못 나오되,
        #    잔여 오차를 완전히 포기하지도 않는다.
        ang_max = c.ang_max_rad_s
        if obs.d <= c.stop_m + c.waypoint_margin_m:
            span = max(c.waypoint_margin_m, 1e-6)
            scale = max(0.0, min(1.0, (obs.d - c.stop_m) / span))
            ang_max = c.ang_max_rad_s * scale
        raw = max(-ang_max, min(ang_max, raw))
        # ⚠️ [2026-08-04, codex 검토] "각속도 max 를 풀자"엔 반대했다 — P 제어라
        #    D 가 없어서 그대로 풀면 관측 노이즈가 그대로 좌우 요동으로 나간다.
        #    대신 **출력의 변화율**을 제한한다 — 관측을 미분하지 않으니 노이즈를
        #    증폭하지 않으면서 프레임 간 급변만 눌러준다.
        #    ⚠️ 첫 호출은 그대로 통과시킨다 — `_ang_at is None` 일 때 `dt` 를
        #    0 으로 두면(예전 시도) `step_max` 가 사실상 0 이 돼 **첫 조향
        #    명령이 항상 거의 0 으로 깎였다**(EMA 필터의 "첫 샘플은 그대로
        #    쓴다"와 같은 이유 — 기준이 없는데 변화율을 제한할 수 없다).
        if self._ang_at is None:
            self._prev_ang = raw
            self._ang_at = now_s
            return raw
        dt = max(now_s - self._ang_at, 1e-3)
        step_max = c.ang_slew_max_rad_s2 * dt
        out = max(self._prev_ang - step_max, min(self._prev_ang + step_max, raw))
        self._prev_ang = out
        self._ang_at = now_s
        return out

    # ── 본체 ──────────────────────────────────────────────────────────────
    def step(self, obs: NotchObs | None, now_s: float,
             search_wall_yaw: float | None = None,
             min_range_m: float | None = None) -> Cmd:
        c = self.cfg
        if self._t0 is None:
            self._t0, self._phase_t0 = now_s, now_s

        if self.phase in ("DONE", "ABORT"):
            return self._stop(self.phase, "finished")

        if now_s - self._t0 >= c.timeout_s:
            return self._stop("ABORT", "timeout")

        # ⚠️ [2026-08-04, 사용자 요청] 최우선 안전 정지 — 노치 검출·정렬 상태와
        #    **완전히 무관**하게, 라이다가 실제로 잰 최소 거리(필터·추정 없음,
        #    `detect.min_range_m`)가 `safety_clearance_m` 아래면 그 자리에서
        #    바로 선다. 오늘 밤 여러 번 확인했듯 `d`(노치 추정)는 틀릴 수
        #    있지만, 이 값은 라이다가 잰 그대로다 — "정확히 맞춰 서는가"보다
        #    "절대 안 부딪히는가"가 우선이면 이게 그 마지막 보루다. `SEARCH`
        #    는 회전만 하므로 제외(그 국면엔 애초에 전진 성분이 없다).
        #    `RETREAT` 도 제외 — 그 국면은 **멀어지는** 중이라 가까움이
        #    도착 신호가 아니고, 여기 걸리면 도착(DONE)으로 잘못 끝난다.
        if (self.phase not in ("SEARCH", "RETREAT") and min_range_m is not None
                and min_range_m <= c.safety_clearance_m):
            return self._stop("DONE", "min_range_safety_stop")

        # ⚠️ **FINAL 이 아니면 NEAR 관측을 받지 않는다.** NEAR 에는 직선 피팅이라는
        #    오검출 방어가 없어 노치 없는 벽을 노치로 읽는다(실측 100%). 도킹이
        #    오검출로 **시작**되지 않게 하는 구조적 방어(SETTLE·SEARCH·ACQUIRE)일
        #    뿐 아니라, ALIGN·APPROACH 에서도 NEAR 의 `y` 는 못 믿는다 — 노치
        #    가장자리가 창 밖으로 잘려 좌우 오차가 커진다(실측: 2cm→12mm, 3cm→21mm).
        #    그 값이 FINAL 진입 문턱(`y_max_enter_final_m`, ≤15mm)을 오염시키면 안
        #    된다. NEAR 는 FAR 가 확정하고 추적해 온 뒤 FINAL 에서의 인수인계일
        #    때만 유효하다. **SEARCH 가 이 줄보다 먼저 있으면 안 된다** — NEAR 의
        #    `yaw=0.0` 을 "정렬 끝났다"로 오인해 오검출로 ACQUIRE 에 넘어간다.
        if obs is not None and obs.near and self.phase != "FINAL":
            obs = None

        # ── RETREAT — 정렬 게이트 재시도용, `v_far_dist_m` 까지 물러난다 ─────
        #   [2026-08-04, 사용자 요청] `Cmd.linear` 의 "항상 ≤0" 불변식에 대한
        #   유일한 예외 — `_retry_or_abort()` 가 정렬 게이트 트리거일 때만
        #   여기로 들어온다. 시간이 아니라 **거리**로 끊는다 — `v_far_dist_m`
        #   는 `_angular()` 의 우선순위 램프가 이미 "먼 구간"(yaw 게인
        #   `k_yaw_far_scale` 비율)으로 보는 기준점이라, 딱 거기까지 물러나면
        #   다음 시도가 곧바로 그 여유를 온전히 받는다 — y 가 자연스럽게
        #   맞춰질 room 을 주는 것. `waypoint_retreat_s` 는 이제 **최대 시간
        #   상한**(안전판)이다 — 물러나는 동안 검출이 끊겨도 무한정 밀리지
        #   않는다.
        if self.phase == "RETREAT":
            reached = obs is not None and obs.d >= c.v_far_dist_m
            if reached or now_s >= self._retreat_until:
                self._enter("ALIGN", now_s)
                return Cmd(0.0, 0.0, "ALIGN", False, "retry")
            return Cmd(c.v_near_mps, 0.0, "RETREAT", False, "retreating")

        # ── SETTLE — nav2 잔여 속도가 죽기를 기다린다 ──────────────────────
        if self.phase == "SETTLE":
            if now_s - self._t0 < c.settle_sec:
                return Cmd(0.0, 0.0, "SETTLE", False, "settling")
            self._enter("SEARCH", now_s)

        # ── SEARCH — 넓은 창으로 "벽만" 찾아 그 방향으로 제자리 정렬한다 ────
        #   ⚠️ [2026-08-04 실기 정정] 원래는 `obs`(노치까지 확정된 전체 검출)의
        #   yaw 로 돌았다. 그런데 각도가 심하게 틀어지면 **벽은 잡혀도 노치
        #   자체가 그 각도에서 인식이 안 된다**(라이다가 노치 바닥이 아니라
        #   옆벽을 스치거나 깊이·폭이 찌그러져 찍힌다) — `obs` 가 계속 `None`
        #   이라 SEARCH 가 어느 방향으로 돌아야 할지 신호 자체를 못 받고
        #   무한정 스핀만 했다. 그래서 회전 방향은 **벽 fit 의 yaw 만**으로
        #   정한다(`search_wall_yaw` — 드라이버가 노치 확인 없이
        #   `fit_wall()` 만 불러 넘긴다). 벽은 노치보다 훨씬 넓은 각도에서도
        #   잡힌다(`search_wall_yaw_max_rad` 가 사실상 무제한). 노치 확정은
        #   정렬이 끝난 뒤 ACQUIRE 가 좁은 창으로 새로 한다 — 그래서 정렬이
        #   끝나는 즉시 **여기서 리턴한다**(같은 tick 에 이어서 확정까지
        #   하지 않는다). 필터도 안 거친다 — 회전 중엔 y·yaw 가 빠르게
        #   바뀌는 게 정상이라 EMA·튐게이트를 쓰면 정상 프레임이 걸러진다.
        if self.phase == "SEARCH":
            if now_s - self._phase_t0 >= c.search_timeout_s:
                return self._stop("ABORT", "search_timeout")
            if search_wall_yaw is None:
                return Cmd(0.0, c.search_rot_rad_s, "SEARCH", False, "searching")
            if abs(search_wall_yaw) > c.search_align_tol_rad:
                ang = math.copysign(c.search_rot_rad_s, c.steer_sign * search_wall_yaw)
                return Cmd(0.0, ang, "SEARCH", False, "aligning")
            self._enter("ACQUIRE", now_s)
            return Cmd(0.0, 0.0, "ACQUIRE", False, "confirming")

        filtered = self._filter(self._stabilize(obs), now_s) if obs is not None else None

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

        # ── 웨이포인트 존 진입 게이트 — 처음 들어오는 순간 딱 한 번 정렬 확인 ──
        #   [2026-08-04, codex 검토 2차] `_angular()` 의 존(위)이 정렬 여부를
        #   안 보고 무조건 걸렸다 — 실기(yaw=15.6°, d=112mm)에서 안 맞은 채
        #   이 존에 들어와 놓고도 아무도 안 막아서 한참 뒤 FINAL 의
        #   `lateral_too_large` 로만 잡혔다(재시도 여지를 그만큼 깎아 먹는다
        #   — codex 권고: 실패는 최대한 앞당겨 잡아야 한다). 여기서 존
        #   경계를 처음 넘는 그 프레임에 딱 한 번 물어서, 못 맞췄으면 바로
        #   ABORT 한다 — `_final_gate_checked`(아래, `wall_really_gone` 전용)
        #   와는 별개 플래그다. 안 맞았는데도 그냥 태우면 존 안에서는
        #   `_angular()` 상한이 이미 줄어드는 중이라 회수도 더 어렵다.
        if (filtered.d <= c.stop_m + c.waypoint_margin_m
                and not self._waypoint_gate_checked):
            self._waypoint_gate_checked = True
            if (abs(filtered.y) > c.y_max_enter_final_m
                    or abs(filtered.yaw) > c.yaw_max_enter_final_rad):
                return self._retry_or_abort("not_aligned_for_waypoint", now_s)

        # ── 웨이포인트 정렬 확인 — 연속 N프레임, 못 채우면 일찍 ABORT ────────
        #   [2026-08-04, codex 검토] "정지해서 y를 맞춘다"는 반박당했다 — 제자리
        #   회전은 yaw만 바꾸고 y(횡위치)는 거의 못 바꾼다(lateral offset은
        #   이동+조향으로만 줄어든다, 실제 도킹 원리와 일치). 그래서 로봇은 계속
        #   움직이며 조향하게 둔다(`_linear`/`_angular` 그대로) — 여기서는 그
        #   결과가 "믿을 만큼 안정적으로 정렬됐는가"만 딱 잘라 확인한다.
        #   `pulse_dist_m` 안(근접 구간)에 들어온 뒤로 `waypoint_align_timeout_s`
        #   안에 연속 `waypoint_align_confirm_frames` 프레임을 못 채우면 90초
        #   전체 타임아웃까지 안 끌고 여기서 먼저 ABORT한다 — 실패 판정을
        #   앞당겨야 재시도할 여지가 남는다(codex 권고, "마지막에야 ABORT하면
        #   재시도율만 높아진다").
        if filtered.d <= c.pulse_dist_m:
            if self._waypoint_hold_t0 is None:
                self._waypoint_hold_t0 = now_s
            aligned_now = (abs(filtered.y) <= c.y_max_enter_final_m
                          and abs(filtered.yaw) <= c.yaw_max_enter_final_rad)
            self._waypoint_confirm = self._waypoint_confirm + 1 if aligned_now else 0
            if self._waypoint_confirm >= c.waypoint_align_confirm_frames:
                # ⚠️ 래치 — codex 권고("히스테리시스, 노이즈로 안팎을 왕복하면
                #    안 된다"). 한 번 확인됐으면 이후 프레임이 잠깐 흔들려도
                #    같은 정지 시각 기준으로 다시 타임아웃을 걸지 않는다.
                self._waypoint_confirmed_once = True
            if (not self._waypoint_confirmed_once
                    and now_s - self._waypoint_hold_t0 >= c.waypoint_align_timeout_s):
                return self._retry_or_abort("waypoint_align_timeout", now_s)
        else:
            self._waypoint_confirm = 0
            self._waypoint_hold_t0 = None
            self._waypoint_confirmed_once = False

        # ── 국면 선택 ─────────────────────────────────────────────────────
        # 벽이 min range 아래로 가기 전에 yaw 를 끝낸다. `near` 관측은 정의상
        # 벽이 없는 상태이므로 항상 FINAL 이다.
        wall_really_gone = filtered.near or not (filtered.wall_m >= c.wall_alive_min_m)
        # ⚠️ [2026-08-04 실측, 1차] `wall_really_gone` 만으로 FINAL 전환을 걸면
        #    안 된다 — "벽이 사라졌다"를 알려면 **그 사실을 담은 성공 프레임**이
        #    와야 하는데, 신호가 서서히가 아니라 뚝 끊기면(실측: d≈111mm 근방)
        #    그 확인 프레임 자체가 안 온다 — `detect()` 가 그냥 `None` 을 낸다.
        #    그러면 NEAR 인수인계 트리거(`phase=="FINAL"`, 드라이버·
        #    dock_scan_visualize 둘 다 이 조건으로 `detect_near` 를 부른다)를
        #    영영 못 받고 `lost` 만 쌓이다 `notch_lost` 로 ABORT 된다. `d` 가
        #    이미 위험구간(`pulse_dist_m`) 안이면 이번 프레임에 `wall_m` 을
        #    못 재도 미리 FINAL 로 넘긴다 — 신호가 끊기기 **전에** NEAR 받을
        #    준비를 해 둔다.
        prep_for_near = wall_really_gone or filtered.d <= c.pulse_dist_m
        if prep_for_near:
            # ⚠️ [2026-08-04 실측, 2차] 위 1차 수정이 새 결함을 만들었다 — 선제
            #    진입 시점에 바로 정렬 게이트를 물으면 아직 교정할 거리가 남았는데
            #    성급하게 ABORT 된다(실측: y=-12mm, 문턱 15mm 인데도 ABORT — 그
            #    순간의 filtered.y 는 아직 더 컸다. `pulse_dist_m` 은 벽이 실제로
            #    사라지는 지점보다 훨씬 여유 있게 잡혀 있어서다). **국면 이름표는
            #    선제로 바꾸되(그래야 NEAR 폴백 준비가 된다), "정렬 안 됐으면
            #    포기" 판정은 벽이 진짜 사라진 그 순간에 딱 한 번만 묻는다** —
            #    `_final_gate_checked` 로 잠가서 두 번 안 묻는다(이미 FINAL 로
            #    넘어간 뒤 매 프레임 다시 걸리면 안 된다).
            if wall_really_gone and not self._final_gate_checked:
                self._final_gate_checked = True
                # ⚠️ FINAL 진입 문턱 — FAR 가 살아 있는 동안 정렬을 끝내야 한다.
                #    NEAR 는 근거리에서 노치 가장자리가 창 밖으로 잘려 좌우 오차가
                #    커진다(실측: 좌우 2cm → 12mm, 3cm → 21mm). 어긋난 채 넘기지 않는다.
                # ⚠️ y 만 보면 안 된다 — y 오차 자체가 yaw 에 비례하므로(§10 실측)
                #    yaw 가 크면 이 y 값을 못 믿는다. 실측: yaw=12.8도에서 y=24mm
                #    (문턱 안)로 통과했는데 실제로는 물리 접촉이 났다.
                if (abs(filtered.y) > c.y_max_enter_final_m
                        or abs(filtered.yaw) > c.yaw_max_enter_final_rad):
                    return self._retry_or_abort("not_aligned_for_final", now_s)
            self._enter("FINAL", now_s)
        elif filtered.d <= c.v_far_dist_m:
            self._enter("APPROACH", now_s)
        else:
            self._enter("ALIGN", now_s)

        if self.phase == "FINAL" and abs(filtered.y) > c.y_max_final_m:
            return self._stop("ABORT", "lateral_too_large")

        lin_mag = self._linear(filtered.d, now_s)
        ang = self._angular(filtered, lin_mag, now_s)
        return Cmd(-lin_mag, ang, self.phase, False, "driving")
