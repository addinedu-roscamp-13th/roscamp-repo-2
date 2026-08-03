"""라이다 노치 도킹의 현장값 — 한 곳에 모은다.

## ★ 표시 둘은 현장에서 재기 전까지 값이 없는 것과 같다

  · `steer_sign`  라이다가 z축 π 회전으로 장착돼 있어 좌우가 로봇 기준과 뒤집힌다
                  (`pinky.urdf.xacro:201`). 계산으로 못 정한다. 기존 ArUco 도킹도
                  같은 이유로 `_STEER_SIGN` 을 현장에서 재서 정했다
  · `stop_m`      로봇을 손으로 밀어 넣어 **실제로 충전이 시작되는 지점**에서 /scan 을
                  읽는다. 그러면 이 값이 곧 "접점이 붙는 위치"의 정의가 되어, 접촉 확인
                  신호 없이도 측정이 검증을 겸한다

## clamped() 가 있는 이유

params.yaml 은 사람이 손으로 고치는 파일이다. 여기 들어오는 값 중 몇 개는 물리적
하한이 있고, 그 아래로 적으면 **조용히 안 움직인다** — 명령은 정상적으로 나가는데
바퀴가 안 돈다. 로그도 예외도 없다. 그래서 들어올 때 한 번 올려 준다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace

#: 실측 모터 데드밴드(m/s). 이 아래로는 명령이 나가도 바퀴가 안 돈다.
#: 근거: config/params.yaml ⑤ 주석 · marker 도킹 현장 튜닝의 `lin_pulse`.
MOTOR_DEADBAND_MPS = 0.05

#: 한 제어 주기(10Hz)에 데드밴드 속도로 가는 거리 = 최소 이동량.
#: 도달 정밀도의 하한이 여기서 나온다 — 센서가 아니라 모터가 정한다.
MIN_STEP_S = 0.10


@dataclass(frozen=True)
class LidarDockConfig:
    # ── 섹터 ──────────────────────────────────────────────────────────────
    sector_half_deg: float = 60.0
    range_max_m: float = 1.5
    min_points: int = 20
    # ── 벽 직선 (RANSAC) ──────────────────────────────────────────────────
    #   ★ 현장 실측 완료 (2026-08-03, pinky-3). 원래 값(50회·1cm)은 벽거리 9~11cm
    #     구간(노치가 ±60도 섹터에서 차지하는 비중이 커지는 근접 구간)에서 실제로
    #     5/5 스캔 전부 잘못된 벽(yaw 실제 ~1~2도인데 -7도로 오판)을 잡았다 —
    #     RANSAC 임의 2점 표본이 노치 점을 섞어 잡을 확률이 반복이 적을수록 커진다.
    #     100회·8mm 부터 5/5 전부 정상 검출, 200회·6mm 이후로는 300·500회와 결과가
    #     똑같아 수렴 확인 — 여유를 둬서 200회·6mm 로 잡는다.
    ransac_iters: int = 200
    ransac_inlier_m: float = 0.006
    ransac_min_inlier_ratio: float = 0.30
    wall_yaw_max_rad: float = 0.61          # 35°
    wall_rms_max_m: float = 0.015
    # ── 노치 ──────────────────────────────────────────────────────────────
    smooth_rays: int = 5
    notch_thresh_m: float = 0.012           # 깊이 2.5cm 의 절반
    notch_depth_min_m: float = 0.015
    notch_depth_max_m: float = 0.040
    notch_width_min_m: float = 0.03
    notch_width_max_m: float = 0.10
    # ── NEAR 모드 (벽이 min range 아래로 사라진 뒤) ───────────────────────
    near_half_deg: float = 35.0
    near_gap_m: float = 0.02
    near_width_min_m: float = 0.02          # 가장자리가 잘려 FAR 보다 느슨하다
    near_width_max_m: float = 0.10
    # ── 필터 ──────────────────────────────────────────────────────────────
    #: 0.3 이 아니라 0.5 다. 지연 시뮬레이션(scratchpad/proto_latency2.py)에서
    #: 0.3 은 편향 −10mm 에 과주행 중단 6/60, 1.0(평활 없음)은 편향은 작아도
    #: 산포가 4.4mm 였다. **편향은 stop_m 이 흡수하고 분산은 안 지워진다.**
    ema_coef: float = 0.5
    #: 튐 게이트는 **경과 시간에 비례**한다. 고정값이면 스캔이 한 번 빠진 뒤의 정상
    #: 관측이 버려지고, 반대로 정지 중의 4.9cm 오검출은 통과한다(codex P1 #5).
    jump_speed_max_mps: float = 0.5
    jump_gate_y_m: float = 0.03
    jump_gate_yaw_rad: float = 0.17
    confirm_frames: int = 3
    lost_frames_max: int = 10
    # ── 국면 ──────────────────────────────────────────────────────────────
    wall_alive_min_m: float = 0.08
    #: FAR 가 살아 있는 동안 이만큼 안에 들어와야 FINAL 로 넘어간다. NEAR 는 근거리에서
    #: 노치 가장자리가 창 밖으로 잘려 좌우 오차가 커진다(실측: 2cm→12mm, 3cm→21mm).
    y_max_enter_final_m: float = 0.015
    stop_m: float = 0.065                   # ★ 현장 실측
    overshoot_margin_m: float = 0.010
    y_max_final_m: float = 0.030
    # ── 속도 ──────────────────────────────────────────────────────────────
    v_far_mps: float = 0.10
    v_near_mps: float = MOTOR_DEADBAND_MPS
    v_far_dist_m: float = 0.30
    pulse_dist_m: float = 0.15
    pulse_min_s: float = MIN_STEP_S
    pulse_max_s: float = 0.40
    pulse_pause_s: float = 0.30
    # ── 조향 ──────────────────────────────────────────────────────────────
    steer_sign: float = 1.0                 # ★ 현장 실측
    k_y: float = 1.2
    k_yaw_align: float = 0.8
    k_yaw_approach: float = 0.4
    k_yaw_final: float = 0.0                # 벽이 없다. 0 이 아닌 값은 의미가 없다
    ang_max_rad_s: float = 0.08
    # ── 시간 ──────────────────────────────────────────────────────────────
    settle_sec: float = 3.0
    acquire_timeout_s: float = 3.0
    timeout_s: float = 90.0
    scan_timeout_s: float = 0.5
    loop_hz: float = 10.0

    @classmethod
    def from_params(cls, params: dict | None, on_unknown=None) -> "LidarDockConfig":
        """params.yaml 조각에서 만든다. `None` 은 무시한다.

        모르는 키를 예외로 올리지 않는 이유: params.yaml 에 주석용 키가 늘어나도
        노드가 죽으면 안 된다.

        ⚠️ 다만 **조용히 버리지도 않는다**(codex P1 #6). `stop_mm` 같은 오타는 기본
           `stop_m` 이 쓰여 아무 증상 없이 로봇이 엉뚱한 데 서는 종류의 실패다.
           `on_unknown` 으로 알린다 — 노드는 로거를, 시험은 리스트를 넘긴다.
        """
        known = {f.name for f in fields(cls)}
        unknown = sorted(k for k in (params or {}) if k not in known)
        if unknown and on_unknown is not None:
            on_unknown(unknown)
        kw = {k: v for k, v in (params or {}).items() if k in known and v is not None}
        return cls(**kw).clamped()

    def clamped(self) -> "LidarDockConfig":
        v_near = max(float(self.v_near_mps), MOTOR_DEADBAND_MPS)
        return replace(
            self,
            v_near_mps=v_near,
            v_far_mps=max(float(self.v_far_mps), v_near),
            pulse_min_s=max(float(self.pulse_min_s), MIN_STEP_S),
            pulse_max_s=max(float(self.pulse_max_s), max(float(self.pulse_min_s), MIN_STEP_S)),
            smooth_rays=max(1, int(self.smooth_rays)),
            confirm_frames=max(1, int(self.confirm_frames)),
            min_points=max(4, int(self.min_points)),
            ransac_iters=max(1, int(self.ransac_iters)),
            ang_max_rad_s=abs(float(self.ang_max_rad_s)),
            steer_sign=math.copysign(1.0, float(self.steer_sign) or 1.0),
            # 최종 국면에는 벽이 min range 아래로 사라져 yaw 를 못 잰다.
            # 값을 적어 놔도 죽은 센서값을 믿는 것이라 0 으로 강제한다.
            k_yaw_final=0.0,
        )
