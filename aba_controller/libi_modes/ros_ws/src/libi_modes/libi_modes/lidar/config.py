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

#: 실측 회전 데드밴드(rad/s). 정지 상태 제자리 회전은 이 아래로 안 돈다
#: (approach.py 의 SEARCH 국면 머리말 참고). 주행 중 조향(`ang_max_rad_s`)과는
#: 다른 값이다 — 그쪽은 이미 정지마찰이 깨진 채로 도니 더 작아도 먹는다.
ROTATE_DEADBAND_RAD_S = 0.16


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
    #   ⚠️ [2026-08-04 실측 정정] 0.30·35°·15mm 이던 값 — 실기: 검출 실패가
    #     너무 잦고("[실패] 벽 fit 조차 안 됨") 성공하는 거리도 짧았다. 오검출은
    #     사용자가 화면으로 직접 보면서 잡기로 하고(실측: "이상하게 잡히면
    #     말할게"), 문턱을 넓혀 성공률을 올린다.
    ransac_min_inlier_ratio: float = 0.20
    wall_yaw_max_rad: float = 0.87          # 35°→50°
    wall_rms_max_m: float = 0.025
    #   ⚠️ [2026-08-04 실측, 세 번째 자리] `detect()` 도 SEARCH 와 같은 결함을
    #     겪는다 — 좁은 60도 창 안에서도 순수 inlier 최다 기준이 옆벽에 진다.
    #     실측: 정면(bearing 1.9°)에 진짜 도크 벽(56점)이 있는데 bearing
    #     45~50도대 옆벽이 inlier 수로 이겨서 통째로 `None` 이 났다. SEARCH 의
    #     `search_bearing_tol_rad`(60°, 임의 자세를 감안해 넓다)는 여기 쓰기엔
    #     너무 넓다 — 이 국면은 이미 어느 정도 정면을 보고 있어야 정상이라
    #     훨씬 좁게 잡는다.
    wall_bearing_tol_rad: float = 0.35      # 20도
    # ── 노치 ──────────────────────────────────────────────────────────────
    smooth_rays: int = 5
    notch_thresh_m: float = 0.012           # 깊이 2.5cm 의 절반
    notch_depth_min_m: float = 0.013        # notch_thresh_m(0.012) 보다는 커야 시험 가능
    notch_depth_max_m: float = 0.045
    notch_width_min_m: float = 0.025
    notch_width_max_m: float = 0.12
    # ── 탐색 (SEARCH, 임의 자세에서 벽·노치 찾기) ─────────────────────────
    #   ★ 현장 실측 (2026-08-03, pinky-3) — 접근 전 회전(FaceApproachYaw)이 목표각
    #     못 미친 채 다음 단계로 넘어온 사례가 있었다(실측 벽 yaw -40.7도, 아래
    #     `wall_yaw_max_rad` 35도 초과). nav2 자체 회전 정밀도에 기대지 않고, 라이다
    #     스스로 훨씬 넓은 창으로 벽+노치를 찾아 그 방향으로 정렬한 뒤 ACQUIRE 로
    #     넘어간다 — `sector_points`/`fit_wall` 은 그대로 두고 이 국면에서만 `sector_half_deg`·
    #     `wall_yaw_max_rad` 대신 이 값들로 바꿔 부른다(`_refit` 의 offset>=0 부호 고정이
    #     180도 모호성을 없애므로 거의 전 방향을 봐도 방향 판정이 흔들리지 않는다).
    search_half_deg: float = 175.0
    search_wall_yaw_max_rad: float = 3.2
    #   ⚠️ [2026-08-04 실측 정정] 0.4(22.9도) → 0.15(8.6도). 클릭 라벨링으로 실측한
    #     결과, 노치 y 오차가 벽 yaw 에 거의 선형 비례했다(상관계수 0.93,
    #     1도당 약 2.4mm, 23도에서 60mm까지). 설계 당시 시뮬레이션은 "23도까지
    #     오차 2mm"였는데 실측은 그 30배 — 믹스드픽셀 등 시뮬레이션에 없던
    #     물리가 낀 것으로 보인다(원인 조사는 별도, scripts/demo/dock_scan_visualize
    #     클릭 로그로 계속 검증 중). 원인을 다 몰라도 이 값을 낮추면 ACQUIRE
    #     인수인계 시점의 편향 위험 구간 자체가 줄어든다 — 값싼 안전마진.
    #: 이 안으로 들어오면 ACQUIRE 로 넘긴다. `wall_yaw_max_rad`(0.61)보다 빡빡해야
    #: 넘어간 바로 다음 tick 에 좁은 창의 `detect()` 도 곧장 성공한다.
    search_align_tol_rad: float = 0.15
    search_rot_rad_s: float = 0.25
    search_timeout_s: float = 30.0
    #   ⚠️ [2026-08-04 실측 정정] `fit_wall_near_bearing()` 전용 — inlier 최다
    #     기준만으로는 도크와 무관한 방의 다른 벽을 우선할 수 있다(실측:
    #     200점 옆벽이 110점 도크벽을 이김, codex P1 확인). 벽의 **기울기**
    #     (search_wall_yaw_max_rad)는 여전히 넓게 두되, 벽 점들이 있는
    #     **방향·거리**는 예상 범위로 제한한다.
    #     아직 현장 값 몇 개로 잡은 1차 추정치다 — 라벨 데이터 쌓이면 좁힐 것.
    search_bearing_tol_rad: float = 1.05          # 60도
    search_wall_dist_min_m: float = 0.05
    search_wall_dist_max_m: float = 1.0
    #   ⚠️ [2026-08-04 실측, 두 번째 결함] `ransac_min_inlier_ratio`(비율)를
    #     `fit_wall_near_bearing` 에도 그대로 썼더니, 넓힌 섹터(175도)라 전체
    #     점 수 n 이 커져서(실측 689) 그 30%(≈207)를 요구하게 됐다. 그런데
    #     실제 도크 벽은 그 넓은 섹터 안에서도 점이 87개뿐이다(벽 자체 크기는
    #     안 변하는데 주변 잡음만 늘어난 것) — 방향(bearing)·거리(dist) 게이트는
    #     다 통과하는데 순전히 이 비율 때문에 계속 `None` 이 나왔다(화면엔 벽이
    #     또렷이 보이는데 SEARCH 만 "searching" 을 반복 — dock_scan_visualize
    #     15초 연속 캡처로 100% 재현 확인). 섹터 폭에 안 흔들리는 절대 개수로
    #     바꾼다 — 87 은 통과하고 잡음은 걸러지는 선에서 여유 있게 잡는다.
    search_wall_min_inliers: int = 30
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
    #: [2026-08-04, 사용자 지적] `(d,y)` 를 재는 기준선(그 프레임의 RANSAC
    #: `wall.yaw`) 자체가 프레임마다 흔들리는 문제를 잡는 별도 필터의 계수다
    #: — `ema_coef` 보다 훨씬 느리게 잡는다(그건 검출값 자체의 잡음을 줄이고,
    #: 이건 "무엇을 기준으로 쟀는가"를 안정시킨다 — 역할이 다르다).
    #: `approach.py:_stabilize()` 참고.
    wall_yaw_ref_coef: float = 0.08
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
    #   ⚠️ [2026-08-04 실측] y 만 보고 FINAL 을 승인하면 안 된다 — 노치 y 오차가
    #     벽 yaw 에 거의 선형 비례한다(상관계수 0.93, 1도당 약 2.4mm, §10 라벨
    #     실측). yaw 가 크면(실측 12.8도) y=24mm 라는 값 자체를 못 믿는데, 실제로는
    #     그 상태로 FINAL 진입 후 물리 접촉이 났다(y 안전 문턱은 통과했지만 실제
    #     y 는 더 컸을 가능성). SEARCH 가 이미 8.6도로 좁힌 것과 같은 값을 쓴다.
    #   ⚠️ [2026-08-04 실기, 재정정] 8.6도로 진입시킨 실기에서 yaw 가 못
    #     고쳐진 채(FINAL 은 yaw 를 안 본다 — k_yaw_final=0, 벽이 사라져 애초에
    #     못 잰다) 오히려 6.2°→16.4°로 더 벌어진 채 도착했다. FINAL 진입
    #     이후엔 교정할 방법이 없으므로, 진입 시점에 최대한 0에 가깝게
    #     좁힌다 — 사용자가 "완전히 0"을 요구, 실측 가능한 선에서 최대한.
    #   ⚠️ [2026-08-04 실기, 3차] 2도가 너무 빡빡해서 y=11mm·yaw=-2.0°(사실상
    #     거의 다 맞은 케이스)까지 걸러냈다 — 3도로 살짝 푼다. 큰 각도
    #     (실기: yaw=-21.3°)는 이 값을 더 풀어도 안 통과하니(문턱 문제가
    #     아니라 그 전 단계에서 못 줄인 것) 이건 별개 문제로 남겨 둔다.
    yaw_max_enter_final_rad: float = 0.0524   # 3도
    #   ⚠️ [2026-08-04, codex 검토] "정지해서 y를 맞춘다"는 물리적으로 반박당했다 —
    #     제자리 회전은 yaw만 바꾸고 y(횡위치)는 거의 못 바꾼다(실제 lateral
    #     offset은 이동+조향으로만 줄어든다). 그 대신 codex 권고대로: 로봇은
    #     계속 움직이며 조향하게 두고(_linear/_angular 그대로), 근접 구간
    #     (pulse_dist_m 안)에서 "정렬됐다"를 한 프레임이 아니라 **연속
    #     N프레임**으로 확인하고, 그마저도 못 채우면 90초 전체 타임아웃까지
    #     안 끌고 여기서 먼저 ABORT한다(실패 판정을 앞당겨 재시도 여지를
    #     남긴다).
    waypoint_align_confirm_frames: int = 3
    waypoint_align_timeout_s: float = 5.0
    #   ⚠️ [2026-08-04, 사용자 요청] 정렬 게이트(`not_aligned_for_waypoint`·
    #     `not_aligned_for_final`·`waypoint_align_timeout`)가 막으면 바로
    #     ABORT 하지 말고, **그 트리거가 걸렸을 때만** 예외적으로 짧게
    #     전진(멀어져)해서 재시도한다 — `Cmd.linear` 의 "항상 ≤0" 불변식은
    #     전역으로 안 풀고, RETREAT 라는 새 국면 안에서만 예외를 둔다. 물러나면
    #     거리가 늘어나 `k_yaw_far_scale` 우선순위 램프가 다시 "먼 구간"으로
    #     보고 재정렬 기회를 준다. 무한루프 방지로 횟수를 제한한다 — 그마저
    #     다 쓰면 원래대로 ABORT.
    waypoint_retry_max: int = 2
    #   ⚠️ [2026-08-04, 사용자 요청] 물러나는 거리는 시간이 아니라 **`v_far_dist_m`
    #     까지**로 정한다 — `_angular()` 우선순위 램프가 이미 그 지점을 "먼
    #     구간"(yaw 게인 낮음, y 가 자연스럽게 맞춰질 여지 큼) 기준으로 쓰므로,
    #     거기까지 물러나면 다음 시도가 그 여유를 곧바로 온전히 받는다. 이 값은
    #     그 거리 판정을 위한 **최대 시간 상한**(안전판)일 뿐이다 — 물러나는
    #     동안 검출이 끊겨도 무한정 밀리지 않는다.
    waypoint_retreat_s: float = 5.0
    stop_m: float = 0.058                   # ★ 최종 정지거리 5.8cm (2026-08-06)
    overshoot_margin_m: float = 0.010
    #   ⚠️ [2026-08-04, 사용자 요청] 노치 검출(`d`)은 오검출·yaw 오차가 낀
    #     추정값이다 — 오늘 밤 여러 번 그걸로 실제 접촉이 났다. 목표를
    #     "정확한 위치"에서 "**절대 안 부딪힘**"으로 낮추고, 판정을 추정이
    #     아니라 `min_range_m()`(원시 최소거리, 필터 없음)에 맡긴다. 이 값
    #     아래로 어떤 것이든 가까워지면 국면·정렬 상태와 무관하게 즉시 선다.
    safety_clearance_m: float = 0.01
    #   ⚠️ 참고: 정렬(yaw)을 0 으로 강제하는 별도 로직은 안 넣는다 — 이 안전
    #     정지는 라이다가 실제로 잰 최소 거리 하나만 보므로 yaw 가 얼마든
    #     상관없이 "가장 가까운 것이 1cm"를 그대로 지킨다. yaw 를 못 믿는
    #     근접 구간(NEAR)에서 정렬을 흉내 내는 것보다 이쪽이 더 확실하다.
    y_max_final_m: float = 0.030
    #   ⚠️ [2026-08-04, 웨이포인트] 정지 목표 바로 앞까지 계속 y 를 붙잡고 조향하면
    #     "옆으로 밀린 채 벽에 평행하게 오다가 마지막에 확 꺾이는" 위험한 경로가
    #     된다(사용자 스케치 — 도크 옆 구조물에 스칠 수 있다). `stop_m` 앞
    #     `waypoint_margin_m` 지점부터는 y·yaw 조향을 완전히 끄고 **직진만** 한다 —
    #     그 지점까지 이미 정렬을 끝내 뒀어야 한다(FINAL 진입 게이트가 그걸 확인).
    #     5cm → 10cm → 0.5cm → 6cm → 7cm — 0.5cm 로 좁혔더니 이번엔 조향
    #     종료점이 정지 목표 바로 옆까지 붙어 실기에서 다시 벌려
    #     달라는 요청. y 를 확실히 맞추고 싶어서 1cm 더(사용자 요청,
    #     6차 yaw 우선순위 램프의 "가까움" 기준점이기도 하다 — 늘리면
    #     그 램프가 완전히 다 켜질 거리도 그만큼 더 여유가 생긴다).
    #     ⚠️ [2026-08-06, 사용자 요청] 조향 종료점을 11cm 로 좁힌다 —
    #     stop_m(6cm) + 이 값(5cm) = 11cm 지점에서 조향을 끈다.
    waypoint_margin_m: float = 0.05
    # ── 속도 ──────────────────────────────────────────────────────────────
    v_far_mps: float = 0.10
    v_near_mps: float = MOTOR_DEADBAND_MPS
    v_far_dist_m: float = 0.30
    pulse_dist_m: float = 0.15
    pulse_min_s: float = MIN_STEP_S
    pulse_max_s: float = 0.40
    #   근접 구간(15cm 안)은 정지-재출발 펄스 대신 연속 저속 후진한다.
    #   stop_m/overshoot/safety_clearance 조건은 별도로 즉시 정지한다.
    pulse_pause_s: float = 0.0
    # ── 조향 ──────────────────────────────────────────────────────────────
    steer_sign: float = 1.0                 # ★ 현장 실측
    #   ⚠️ [2026-08-04 실측 정정, 2차] 1.2·0.08 → 2.0·0.12 로 한 번 올렸는데도
    #     실기에서 중앙에 못 붙는다는 관찰이 있어 한 번 더 올린다. 계속
    #     부족하면 더 올릴 수 있음 — 진동/오버슈트 징후(좌우로 홱홱 튀는 것)
    #     보이면 그건 역으로 낮춰야 한다는 신호다.
    #   ⚠️ [2026-08-04 실측 정정, 4차] 조향 종료점을 12cm 로 넓히니(위 waypoint_margin_m)
    #     그 지점 전에 y 수렴을 못 끝내 FINAL 에서 lateral_too_large 로 ABORT
    #     (실기: yaw=15.6° 진입, d=112mm 인데 y=-28mm 못 줄임). 12cm 까지 더 빨리
    #     수렴하도록 세게 올린다 — 진동 보이면 낮춘다.
    k_y: float = 4.0
    #   ⚠️ [2026-08-04 실기, 5차] `k_y`(4.0)가 `k_yaw_*` 보다 훨씬 커서 합쳐진
    #     조향(`k_y*y + k_yaw*yaw`)이 y 쪽으로 압도적으로 쏠렸다 — 실기: y=4mm
    #     (거의 다 맞음)인데 yaw=-10.6° 는 그대로 남은 채 존 진입 게이트(2도)에
    #     계속 막혔다. y 전형값(수cm)·yaw 전형값(수~십도=수백 mrad) 규모 차이를
    #     감안해 yaw 항의 실제 기여가 y 항과 비슷해지도록 올린다.
    k_yaw_align: float = 2.0
    k_yaw_approach: float = 1.2
    #   ⚠️ [2026-08-04 실기, 6차] `k_y*y + k_yaw*yaw` 를 그냥 더하면 두 항이
    #     서로 싸운다 — y 를 줄이려면 로봇이 잠깐 비스듬히 틀어야 하는데
    #     (움직이면서 도는 것만 y 를 바꾼다), yaw 항이 동시에 그 비스듬함을
    #     0 으로 계속 당긴다. 실기: d=343mm 에서 yaw=+0.4°(거의 완벽)인데
    #     y=+29mm 로 그대로였다 — yaw 게인을 올릴수록(5차) 역효과였다.
    #     멀 때는(`v_far_dist_m`) yaw 게인을 이 비율까지 낮춰 각을 허용하고,
    #     존 경계(`stop_m+waypoint_margin_m`)에 가까워질수록 원래 값으로
    #     램프업해 똑바로 세운다(`approach.py:_angular()`).
    k_yaw_far_scale: float = 0.25
    k_yaw_final: float = 0.0                # 벽이 없다. 0 이 아닌 값은 의미가 없다
    ang_max_rad_s: float = 0.35             # [2026-08-04, 4차] 0.22 → 0.35
    #   ⚠️ [2026-08-04, codex 검토] "각속도 max 를 풀자"(P 만이라 D 없음, 그대로
    #     풀면 노이즈가 그대로 튄다) 대신 **출력 변화율**을 제한한다 — 관측을
    #     미분하지 않으니 노이즈를 증폭하지 않으면서 좌우로 홱홱 튀는 것만
    #     억누른다. rad/s². 0.35 왕복(0.7)을 0.35초 안에 다 쓸 수 있는 크기.
    ang_slew_max_rad_s2: float = 2.0
    # ── 시간 ──────────────────────────────────────────────────────────────
    settle_sec: float = 3.0
    acquire_timeout_s: float = 3.0
    # 노치를 확정하지 못했을 때 같은 자세에서 재시작하면 같은 스캔을 반복할
    # 뿐이다. 안전한 제자리 스윕으로 빔 입사각만 바꿔 다시 확정한다. 전진·후진은
    # 하지 않으므로 도크와의 거리는 변하지 않는다.
    acquire_recovery_max: int = 3
    acquire_recovery_sweep_rad: float = 0.25
    acquire_recovery_settle_s: float = 0.30
    #   ⚠️ [2026-08-04, 사용자 요청] 90초 → 300초. RETREAT 재시도(최대 2회,
    #     매번 최대 waypoint_retreat_s 만큼 왕복)까지 감안하면 90초는 실기에서
    #     거의 다 맞춰놓고도(d=100mm, y≈0, yaw=0.7°) 이 전체 타임아웃에
    #     먼저 걸릴 수 있었다.
    timeout_s: float = 300.0
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
            acquire_recovery_max=max(0, int(self.acquire_recovery_max)),
            acquire_recovery_sweep_rad=max(0.0, abs(float(self.acquire_recovery_sweep_rad))),
            acquire_recovery_settle_s=max(0.0, float(self.acquire_recovery_settle_s)),
            min_points=max(4, int(self.min_points)),
            ransac_iters=max(1, int(self.ransac_iters)),
            ang_max_rad_s=abs(float(self.ang_max_rad_s)),
            ang_slew_max_rad_s2=abs(float(self.ang_slew_max_rad_s2)),
            k_yaw_far_scale=max(0.0, min(1.0, float(self.k_yaw_far_scale))),
            search_rot_rad_s=max(abs(float(self.search_rot_rad_s)), ROTATE_DEADBAND_RAD_S),
            steer_sign=math.copysign(1.0, float(self.steer_sign) or 1.0),
            # 최종 국면에는 벽이 min range 아래로 사라져 yaw 를 못 잰다.
            # 값을 적어 놔도 죽은 센서값을 믿는 것이라 0 으로 강제한다.
            k_yaw_final=0.0,
        )
