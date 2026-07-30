"""마커 주행 판단 로직 — 순수 상태기계.

카메라·모터·시계·ROS 를 모른다. 입력은 관측값과 센서 수치, 출력은 명령뿐이다.
그래서 하드웨어 없이 전체 판단을 테스트할 수 있고, 나중에 다른 스택 위로 승격할 때
이 파일은 손대지 않는다.

## 단계

    SEARCH ─검출→ HOMING ─z≤gate→ AXIS_ALIGN ─정렬+근접→ BLIND_PUSH ─거리도달→ DONE
       └─1스윕 실패→ ABORT           └─상실(먼 거리)→ SEARCH

## 좌표 약속

- `angular` 양수 = 좌회전(ROS 관례, z 축 CCW).
- `MarkerObs.lateral_m` 는 **마커 로컬 X 축(벽면 수평축) 상의 부호 있는 이탈**이다.
  벽에 붙은 마커 + 지상 주행이므로 수직 성분은 제어에 쓰지 않는다.
- 실제 좌우 극성은 로봇 배선·카메라 장착에 따라 뒤집힐 수 있다. 그건 `steer_sign`
  하나로만 뒤집는다(현장에서 정하는 값).
"""
import math

from .config import MarkerDriveConfig
from .types import Cmd, MarkerObs


def _finite(*values) -> bool:
    """NaN/inf 를 걸러낸다.

    NaN 은 비교가 전부 False 라 조용히 안전장치를 통과한다.
    예: NaN < scan_guard_m 는 False 라서 '안전하다'로 읽힌다.
    """
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def _valid_obs(obs: MarkerObs | None) -> bool:
    return obs is not None and _finite(obs.ex, obs.z_m, obs.yaw_deg, obs.lateral_m)


def _sweep_targets(step_deg: float, span_deg: float) -> list[float]:
    """탐색 목표 각도 순서: 좌로 훑고 중앙 복귀 후 우로 훑는다."""
    n = max(1, int(round(span_deg / step_deg)))
    left = [step_deg * (i + 1) for i in range(n)]
    right = [-step_deg * (i + 1) for i in range(n)]
    return left + [0.0] + right


class MarkerApproach:
    def __init__(self, cfg: MarkerDriveConfig):
        self.cfg = cfg.clamped()
        self.phase = "SEARCH"
        # --- 탐색 ---
        self._targets = _sweep_targets(self.cfg.search_step_deg, self.cfg.search_span_deg)
        self._target_i = 0
        self._yaw0 = None            # 탐색 기준이 되는 시작 방위각
        self._settle_until = 0.0     # 회전 후 재검출 대기 종료 시각
        self._step_started = None    # 현재 스텝 회전 시작 시각(회전 실패 감시)
        self._step_err0 = None       # 스텝 시작 시점의 각도 오차 부호(교차 판정용)
        self._turn_ref = None        # 회전 진행 감시 기준 방위각
        self._turn_since = 0.0       # 그 기준이 갱신된 시각
        # --- 조향 ---
        self._ex_f = None            # ex 저역통과 상태
        self._i_acc = 0.0            # 조향 적분 누적
        self._prev_ex = 0.0
        # --- 진행 상태 ---
        self._t0 = None              # 전체 시작 시각
        self._t_prev = None          # 직전 틱 시각(실제 dt 계산용)
        self._lost = 0               # 연속 상실 프레임 수
        self._last_z = None          # 마지막으로 본 마커 거리
        self._pulse_until = 0.0      # 펄스 전진 종료 시각
        self._pulse_next = 0.0       # 다음 펄스 시작 가능 시각
        self._blind_target = None    # 무시각 전진 목표 거리(m)
        self._blind_fwd0 = None      # 무시각 전진 기준점(전진 누적값)
        self._fwd_at_obs = None      # 마지막 유효 관측 시점의 전진 누적값
        self._progress_ref = None    # (진행거리, 시각) — 무진전 판정 기준
        self._align_since = None     # **연속 미정렬**이 시작된 시각
        self._last_aligned = False   # 마지막 관측이 정렬 상태였나(상실 경로 판단용)
        self._aligned_run = 0        # 연속으로 정렬 판정된 프레임 수
        self._align_best = None      # 현재 국면의 최소 정렬 오차(진전 판정 기준)
        self._final_align_since = None  # 목표 거리에서 제자리 정렬을 시작한 시각

    # ---- 내부 헬퍼 -------------------------------------------------------
    def _stop(self, reason: str, phase: str) -> Cmd:
        self.phase = phase
        return Cmd(0.0, 0.0, phase, phase in ("DONE", "ABORT"), reason)

    def _filter_ex(self, ex: float) -> float:
        a = self.cfg.ex_lpf_alpha
        self._ex_f = ex if self._ex_f is None else (a * ex + (1 - a) * self._ex_f)
        return self._ex_f

    def _steer(self, ex: float, dt: float) -> float:
        """ex 기반 조향 각속도.  angular = -sign * (kp*e + ki*∫e + kd*de/dt)"""
        c = self.cfg
        e = self._filter_ex(ex)
        if abs(e) < c.steer_deadband:
            self._i_acc = 0.0
            self._prev_ex = e
            return 0.0
        self._i_acc = max(-c.steer_i_max, min(c.steer_i_max, self._i_acc + e * dt))
        d = (e - self._prev_ex) / dt if dt > 0 else 0.0
        self._prev_ex = e
        u = c.steer_kp * e + c.steer_ki * self._i_acc + c.steer_kd * d
        mag = min(c.steer_ang_max, max(c.steer_ang_min, abs(u)))
        return -c.steer_sign * mag * (1.0 if u >= 0 else -1.0)

    def _yaw(self, o: MarkerObs) -> float:
        """카메라 장착 오차를 뺀 정면각.

        카메라가 로봇 정면에서 몇 도 틀어져 달려 있으면, 로봇이 벽과 정확히
        직각이어도 yaw 가 0 이 아니다. steer_sign 은 좌우를 뒤집을 뿐 이 편향은
        못 없앤다. 그래서 상수 보정을 따로 둔다(현장에서 재는 값).
        """
        return o.yaw_deg - self.cfg.yaw_offset_deg

    def _reset_steering(self) -> None:
        """조향 필터·적분 상태를 비운다.

        마커를 잃었다 다시 잡으면 오차 부호가 반대로 뒤집혀 있을 수 있다.
        LPF(alpha 0.45)가 옛 값을 물고 있으면 재획득 첫 틱이 이탈을 키우는
        방향으로 나간다: 0.45*(-0.5) + 0.55*(+0.5) = +0.05.
        """
        self._ex_f = None
        self._i_acc = 0.0
        self._prev_ex = 0.0

    def _align_error(self, o: MarkerObs) -> float:
        """정렬 오차를 허용치로 정규화한 하나의 수. 1.0 이하면 정렬된 것이다.

        lateral 과 yaw 를 **둘 다** 본다. lateral 만 보면 yaw 가 크게 틀어진 채로
        무시각 구간에 진입해서 남은 거리를 비스듬히 밀고 들어간다.
        """
        c = self.cfg
        return max(abs(o.lateral_m) / c.pose_axis_tol_m,
                   abs(self._yaw(o)) / c.pose_yaw_tol_deg)

    def _forward_progress_ok(self, forward_m: float, now_s: float) -> bool:
        """전진 명령을 내는 동안 odom 이 실제로 늘고 있나.

        무시각 구간에만 이 감시를 두면, 바퀴가 헛도는 로봇이 접근·정렬 단계에서는
        전역 타임아웃까지 계속 밀기만 한다.
        """
        if self._progress_ref is None:
            self._progress_ref = (forward_m, now_s)
            return True
        ref_fwd, ref_t = self._progress_ref
        if forward_m - ref_fwd > 0.005:
            self._progress_ref = (forward_m, now_s)
            return True
        return now_s - ref_t <= self.cfg.no_progress_s

    # ---- 단계 ------------------------------------------------------------
    def _do_search(self, yaw_deg: float, now_s: float) -> Cmd:
        c = self.cfg
        if self._yaw0 is None:
            self._yaw0 = yaw_deg
        if self._target_i >= len(self._targets):
            return self._stop("not_found", "ABORT")
        if now_s < self._settle_until:
            return Cmd(0.0, 0.0, "SEARCH", False, "settle")
        err = self._targets[self._target_i] - (yaw_deg - self._yaw0)
        if self._step_started is None:
            self._step_started = now_s
            self._step_err0 = err
            self._turn_ref = yaw_deg
            self._turn_since = now_s
        # 허용오차 창(±tol)만 보면 루프가 느릴 때 창을 건너뛴다.
        # 12Hz·0.35rad/s 면 1.7°/tick 이라 안전하지만, 검출이 무거워 5Hz 로 떨어지면
        # 4°/tick 이라 ±2° 창을 그냥 지나쳐 영영 도달 판정이 안 난다.
        # 그래서 **목표 각을 지났는지(오차 부호 반전)** 도 도달로 본다.
        crossed = self._step_err0 is not None and err * self._step_err0 < 0
        if abs(err) <= c.search_tol_deg or crossed:
            self._target_i += 1
            self._settle_until = now_s + c.turn_pause_s
            self._step_started = None
            self._step_err0 = None
            self._turn_ref = None
            return Cmd(0.0, 0.0, "SEARCH", False, "look")
        # 회전 명령을 냈는데 각도가 **안 변하는지** 를 본다. 경과 시간만 재면 안 된다 —
        # 한 스텝에 걸리는 시간은 ang_search 에 반비례하므로, 느린 로봇(불감대가 높아
        # ang_search 를 낮춘 경우)은 정상 회전 중에도 시간 초과로 걸린다.
        # 실측: ang_search 0.16rad/s 로 20° 회전은 2.2초, 40° 구간은 4.4초라 3초 한도를 넘는다.
        if abs(yaw_deg - self._turn_ref) > c.search_tol_deg:
            self._turn_ref = yaw_deg
            self._turn_since = now_s
        elif now_s - self._turn_since > c.search_step_timeout_s:
            # 바퀴 헛돎이거나 odom 정지. 전역 타임아웃까지 두면 'timeout' 으로 오분류된다.
            return self._stop("turn_stall", "ABORT")
        return Cmd(0.0, c.ang_search if err > 0 else -c.ang_search,
                   "SEARCH", False, "sweep")

    def _do_homing(self, o: MarkerObs, dt: float, forward_m: float, now_s: float) -> Cmd:
        if not self._forward_progress_ok(forward_m, now_s):
            return self._stop("blocked", "ABORT")
        return Cmd(self.cfg.lin_homing, self._steer(o.ex, dt), "HOMING", False, "approach")

    def _do_axis_align(self, o: MarkerObs, now_s: float, forward_m: float) -> Cmd:
        c = self.cfg
        err = self._align_error(o)
        aligned = err <= 1.0
        self._last_aligned = aligned
        self._aligned_run = self._aligned_run + 1 if aligned else 0
        # 정체 판정은 **오차가 줄었는가**로 한다. 경과 시간으로 자르면 정상 수렴을
        # 중단시킨다: 45° 를 steer_ang_max(0.08rad/s) 로 8° 안까지 줄이려면 8.1초다.
        #
        # 기준값은 '역대 최소'가 아니라 '현재 국면의 최소'다. 역대 최소로 두면 좋은
        # 프레임 하나(오차 0)에 갇혀서, 이후 어떤 실제 개선도 진전으로 안 잡힌다.
        # 오차가 유의하게 나빠지면 새 국면으로 보고 예산을 새로 준다.
        # (±eps 로 계속 진동하는 병적인 경우는 전역 timeout_s 가 받는다.)
        if (self._align_best is None or err < self._align_best - c.align_progress_eps
                or err > self._align_best + c.align_progress_eps):
            self._align_best = err
            self._align_since = now_s
        elif not aligned and now_s - self._align_since > c.align_stall_s:
            return self._stop("align_stall", "ABORT")
        # 마커 법선축에 올라타는 라인추종 제어: **교차오차 − 헤딩오차**.
        #
        #   ω = k_lat·lateral − k_yaw·(yaw/90)
        #
        # 두 항을 더하면 안 된다. 관측상 yaw ≈ −(로봇 자세각)이라, 축 쪽으로 몸을
        # 틀수록 yaw 가 같은 부호로 커진다. 더하면 회전이 자기 자신을 부추겨
        # 계속 돌다가 마커를 시야 밖으로 날린다(폐루프 시뮬레이션에서 실제로 발산).
        # 빼야 헤딩이 교차오차를 상쇄하는 지점에서 평형이 잡힌다.
        u = c.pose_kp_lat * o.lateral_m - c.pose_kp_yaw * (self._yaw(o) / 90.0)
        ang = c.steer_sign * max(-c.steer_ang_max, min(c.steer_ang_max, u))
        if o.z_m <= c.stop_m + c.front_offset_m:
            # 목표 거리에 닿았다. 정렬까지 됐으면 끝. 틀어져 있으면 전진을 멈추고
            # **제자리 회전**으로 마저 맞춘다 — 비뚤게 도착하면 거리가 맞아도 실패다.
            #
            # 단 이 시도에는 한도를 둔다. 이 거리(7cm 마커 기준 12cm 안쪽)에서는
            # 마커가 화면을 넘기 시작해 자세 추정이 깜빡인다. 무한정 맞추려 들면
            # 정렬↔상실을 반복하다 탐색으로 빠져 영영 안 끝난다(시뮬레이션에서 확인).
            # 정렬의 진짜 보증은 더 멀리서, 무시각 전진에 들어가기 전에 받는다.
            if aligned:
                return self._stop("reached", "DONE")
            if self._final_align_since is None:
                self._final_align_since = now_s
            if now_s - self._final_align_since > c.align_stall_s:
                # 거리는 맞췄지만 정면각을 못 맞췄다. 도킹 제어기가 이걸 성공으로
                # 보고하면 거짓 성공이다 — 멈추되 실패로 남긴다.
                return self._stop("final_align_failed", "ABORT")
            return Cmd(0.0, ang, "AXIS_ALIGN", False, "final_align")
        # 근거리는 코너 추정이 가장 불안정한 구간이다. 한 프레임만 보고 눈을 감으면
        # 튄 값 하나에 비뚤게 밀고 들어간다 — 연속으로 맞아야 넘어간다.
        if self._aligned_run >= c.aligned_frames_needed and o.z_m <= c.lost_near_m:
            self._enter_blind(o.z_m, forward_m)
            return Cmd(0.0, 0.0, "BLIND_PUSH", False, "near")
        if now_s >= self._pulse_next:
            self._pulse_until = now_s + c.move_pulse_s
            self._pulse_next = now_s + c.move_pulse_s + c.move_pause_s
        lin = c.lin_pulse if now_s < self._pulse_until else 0.0
        # 진행 감시는 **전진을 명령하는 동안만** 잰다. 펄스가 쉬는 구간까지 세면
        # 정상적인 '조금 가고 멈춰 보기' 리듬이 막힘으로 오판된다.
        if lin > 0.0 and not self._forward_progress_ok(forward_m, now_s):
            return self._stop("blocked", "ABORT")
        return Cmd(lin, ang, "AXIS_ALIGN", False, "align")

    def _enter_blind(self, last_z: float, forward_m: float) -> None:
        """무시각 전진 시작.

        기준점은 진입 시점이 아니라 **마지막으로 마커를 본 시점**의 전진 누적값이다.
        상실 유예(기본 8프레임) 동안 로봇이 관성으로 조금 더 가는데, 진입 시점을
        기준 삼으면 그만큼 더 밀어 목표를 지나친다.
        """
        c = self.cfg
        self.phase = "BLIND_PUSH"
        self._blind_target = max(0.0, last_z - (c.stop_m + c.front_offset_m))
        self._blind_fwd0 = self._fwd_at_obs if self._fwd_at_obs is not None else forward_m
        self._progress_ref = None

    def _do_blind_push(self, forward_m: float, now_s: float) -> Cmd:
        """마커가 시야를 벗어난 마지막 구간을 odom 으로 간다.

        **헤딩에 투영한 전진 성분**만 센다. 누적 경로 길이는 제자리 진동으로도 늘고,
        시작점 대비 직선거리는 옆으로 밀린 거리까지 진행으로 세기 때문에, 앞으로
        가지 않았는데 도달로 읽힌다.
        """
        c = self.cfg
        if self._blind_fwd0 is None:
            self._blind_fwd0 = forward_m
        gone = forward_m - self._blind_fwd0
        if gone >= self._blind_target:
            return self._stop("reached", "DONE")
        if not self._forward_progress_ok(forward_m, now_s):
            return self._stop("blocked", "ABORT")
        return Cmd(c.lin_pulse, 0.0, "BLIND_PUSH", False, "blind")

    # ---- 진입점 ----------------------------------------------------------
    def step(self, obs: MarkerObs | None, *, yaw_deg: float, forward_m: float,
             front_m: float | None, now_s: float) -> Cmd:
        """한 틱의 판단.

        yaw_deg : odom 누적 방위각(도, 좌회전 +)
        forward_m: 헤딩에 투영한 부호 있는 전진 누적값(m) — 무시각 구간 거리 판정용
        front_m : 원본 /scan 전방 최소거리(m). 아직 스캔이 없으면 None
        now_s   : 단조 증가 초
        """
        c = self.cfg
        if self._t0 is None:
            self._t0 = now_s
        if self.phase in ("DONE", "ABORT"):
            return Cmd(0.0, 0.0, self.phase, True, "finished")
        if not _finite(now_s, yaw_deg, forward_m):
            return self._stop("bad_odom", "ABORT")
        if now_s - self._t0 > c.timeout_s:
            return self._stop("timeout", "ABORT")
        if front_m is not None:
            if not _finite(front_m):
                front_m = None                    # 값이 깨졌으면 '없음'으로 취급
            elif front_m < c.scan_guard_m:
                return self._stop("scan_guard", "ABORT")
        # dt 는 실제 경과 시간이다. 1/loop_hz 로 고정하면 검출이 늘어져 루프가
        # 0.5s 로 벌어져도 적분에 83ms 만 반영돼 제어가 시간을 속인다.
        dt = (now_s - self._t_prev) if self._t_prev is not None else (1.0 / c.loop_hz)
        dt = min(max(dt, 1e-3), 1.0)
        self._t_prev = now_s

        seen = _valid_obs(obs) and obs.marker_id == c.marker_id
        if seen:
            self._lost = 0
            self._last_z = obs.z_m
            self._fwd_at_obs = forward_m
            if self.phase in ("SEARCH", "HOMING"):
                # 이 분기는 phase 가 SEARCH/HOMING 일 때만 온다 — AXIS_ALIGN 재진입은 없다
                nxt = "AXIS_ALIGN" if obs.z_m <= c.axis_gate_m else "HOMING"
                if nxt == "AXIS_ALIGN":
                    self._align_since = None     # 재진입마다 정렬 시간을 새로 준다
                self.phase = nxt
            if self.phase == "HOMING":
                return self._do_homing(obs, dt, forward_m, now_s)
            if self.phase == "AXIS_ALIGN":
                return self._do_axis_align(obs, now_s, forward_m)
            if self.phase == "BLIND_PUSH":
                return self._do_blind_push(forward_m, now_s)
            return Cmd(0.0, 0.0, self.phase, False, "hold")

        if self.phase == "BLIND_PUSH":
            return self._do_blind_push(forward_m, now_s)
        if self.phase in ("HOMING", "AXIS_ALIGN"):
            self._lost += 1
            if self._lost < c.lost_grace:
                return Cmd(0.0, 0.0, self.phase, False, "lost_grace")
            # 마지막 관측 거리로 갈린다.
            if self._last_z is not None and self._last_z <= c.stop_m + c.front_offset_m:
                # 이미 목표 거리 안이다. 마커가 시야를 벗어난 것뿐이고 갈 곳이 없다.
                return self._stop("reached", "DONE")
            if self._last_z is not None and self._last_z <= c.lost_near_m:
                # 시야 이탈로 인한 정상 상실은 무시각 전진으로 잇는다. 단 마지막
                # 관측이 틀어져 있었다면 그대로 밀면 비뚤게 박는다. 그렇다고 벽
                # 코앞에서 탐색을 돌리는 것도 무의미하다 — 멈추고 이유를 남긴다.
                if self._last_aligned:
                    self._enter_blind(self._last_z, forward_m)
                    return self._do_blind_push(forward_m, now_s)
                return self._stop("lost_misaligned", "ABORT")
            self.phase = "SEARCH"
            self._yaw0 = None
            self._target_i = 0
            self._step_started = None
            self._step_err0 = None
            self._settle_until = 0.0
            self._align_since = None     # 다음 정렬 시도는 시간을 새로 받는다
            self._align_best = None
            self._aligned_run = 0
            self._last_aligned = False
            self._reset_steering()
            return Cmd(0.0, 0.0, "SEARCH", False, "lost_far")
        return self._do_search(yaw_deg, now_s)
