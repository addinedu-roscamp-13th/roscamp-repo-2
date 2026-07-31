# All values are reference starting points for tuning — not hard requirements.

# ---- distance PID (linear_x) ----
# [2026-07-28] 360 → 180. **360 은 사실상 도달 불가능한 목표였다 — 로봇이 들이박았다.**
#
#   size = sqrt(bbox 폭 × 높이) 이고 거리에 반비례한다. 프레임은 640×480 이다.
#   사람(키 1.7m·폭 0.45m)이 화면 높이를 **정확히 꽉 채우는** 거리에서:
#       h = 480,  w ≈ 0.45/1.7 × 480 = 127      size = sqrt(480×127) = 247
#   즉 화면을 세로로 가득 채워도 247 이다. 목표가 360 이면 오차가 계속 양수라
#   PID 는 bbox 가 잘려 폭이 더 넓어질 때까지 — 사람 코앞까지 — 전진을 멈추지 않는다.
#   후진(LINEAR_X_REVERSE_MAX)은 영영 안 걸린다.
#
#   **280 은 사용자가 예전에 쓰던 실측값이다.** 계산상 247("화면을 세로로 꽉 채움")보다
#   조금 크므로 bbox 가 살짝 잘릴 만큼은 다가간다. 그래도 360 과 달리 도달 가능하고,
#   아래 STOP_DIST 하드 스톱이 밑을 받친다.
#   (내가 처음 넣은 180 은 계산만으로 고른 값이라 너무 멀었다 — 실측이 이긴다)
#
#   ⚠️ 조정 규칙: **거리에 반비례**한다.
#       새 값 = 지금 값 × (지금 멈추는 거리 ÷ 원하는 거리)
#      더 멀리 서게 하려면 **낮추고**, 더 붙게 하려면 올린다.
TARGET_SIZE = 280.0            # sqrt(area) setpoint
KP_DIST = 0.0030
KI_DIST = 0.0001
KD_DIST = 0.0
INTEGRAL_DIST_CLAMP = 50.0
LINEAR_X_MAX = 0.12            # forward max (m/s)
LINEAR_X_REVERSE_MAX = 0.06   # reverse max (m/s) — smaller: LiDAR blind rear

# ---- bearing PID (angular_z) ----
# ⚠️ 폴백값일 뿐이다. 검출 payload 에 `image_width` 가 있으면 그쪽이 이긴다
#    (`detection.py` / `pid.compute(image_width=...)`).
#    실제 로봇 카메라는 480x360 이다 — `robot_agent/app/hardware/camera_stream.py:338`
#    Picamera2 `main={"size": (480, 360)}`, rpicam-vid 도 `--width 480 --height 360`.
#    (V4L2 폴백 경로만 640x480 을 요청한다: 같은 파일 :400-401)
#    소스가 해상도를 안 실어 보내는 동안에는 이 값이 쓰이므로, **소스를 고치는 것이 우선**이다.
IMAGE_WIDTH = 640
KP_ANGLE = 0.0010
KI_ANGLE = 0.0
KD_ANGLE = 0.0
INTEGRAL_ANGLE_CLAMP = 200.0
ANGLE_DEADZONE = 45.0         # px
ANGULAR_Z_MAX = 0.60          # rad/s
ANGULAR_SMOOTHING = 0.3       # low-pass: 0=frozen, 1=no smoothing

# ---- LiDAR avoidance ----
MIN_DIST = 0.20               # front-arc slowdown threshold (m)
# [2026-07-28] **하드 스톱**. 이보다 가까우면 전진을 0 으로 만든다.
#
#   MIN_DIST 는 비례 감속만 한다 — `linear_x *= front / MIN_DIST` 라
#   0.10m 에서 50%, 0.05m 에서 25% 로 줄 뿐 **0 이 되는 건 거리 0 에서다.**
#   관성까지 더하면 그대로 부딪힌다(2026-07-28 "그냥 들이박네").
#   비례 감속은 부드러움을 위해 남기고, 그 아래에 끊는 선을 하나 둔다.
#
#   ⚠️ 전진만 막는다. 후진은 살려 둔다 — 너무 붙었을 때 빠져나올 수단이 사라진다.
STOP_DIST = 0.25              # front-arc hard stop (m). 0 이면 끔
AVOID_DIST = 0.40             # side-arc shy-away threshold (m)
AVOID_KP = 0.50
FRONT_ARC_DEG = 15            # +/- degrees around 0 (front)
# [2026-07-31] 20 → 16. **어느 아크에도 안 들어가는 각도가 있었다.**
#   전방은 345~359°·0~15°, 측면은 20~70°·290~340° 였다 —
#   **16~19° 와 341~344° 가 비어** 그 방향의 장애물은 감속에도 조향에도 안 잡혔다.
#   16 으로 붙이면 좌 16~70°, 우 290~344° 가 되어 공백도 중복도 없다.
SIDE_ARC = (16, 71)          # degrees range for a side arc (start, stop-exclusive)

# ---- LiDAR 건강성 (fail-safe) ----
# [2026-07-31] 회피가 **조용히 꺼지는** 두 경우를 막는다.
#
#   예전에는 스캔이 비어 있으면 `apply_avoidance` 가 속도를 그대로 통과시켰다.
#   즉 라이다가 죽거나 아직 안 올라왔으면 **회피 없이 전속 전진**이었고, 그 사실이
#   어디에도 안 드러났다. 앞을 못 보면 앞으로 가지 않는 것이 맞다.
#
#   ⚠️ **전진만** 막는다. 후진과 회전은 살린다 — 못 보는 것은 앞이고, 이미 붙어 있을 때
#      빠져나올 수단까지 없애면 안 된다. (STOP_DIST 와 같은 규칙이다)
#
#   ⚠️ /scan 이 없는 곳에 추종 노드를 띄우면 이 값 때문에 **전진이 아예 안 된다.**
#      그건 의도다. 라이다 없이 굴릴 거면 0 으로 꺼라.
SCAN_MAX_AGE_SEC = 1.0        # 이보다 오래된 스캔은 없는 것으로 본다. 0 이면 검사 끔
# 전방 아크(±FRONT_ARC_DEG = 31칸)에서 이만큼도 못 채우면 "앞을 못 본다"로 본다.
# sllidar C1 은 스캔당 1000+ 포인트라 정상이면 31칸이 거의 다 찬다. 0 이면 검사 끔.
FRONT_MIN_SAMPLES = 5

# ---- miss / search ----
N_MISS_FRAMES = 40            # consecutive None before TRACKING -> SEARCHING
# 탐색(회복) 타임라인.
#   HoldFront 5s(앞,정지) → HoldBack 5s(뒤,정지)
#   → SweepFront(좌우로 훑고 원위치) → SweepBack(뒤캠으로 같은 왕복) → GiveUp
# 어느 구간에 있든 앞캠에서 보이면 즉시 재개, 뒤캠에서 잡히면 즉시 180° 회전한다
# (recovery_bt 의 Selector 우선순위). 구간을 다 돌 필요가 없다.
SEARCH_HOLD_SEC = 5.0         # 앞/뒤 각각 서서 보는 시간
SEARCH_SWEEP_ANGLE = 3.14159  # 훑는 전체 폭(rad). 중앙 기준 ±90°
ANGULAR_Z_SWEEP = 0.55        # rad/s — 훑기 각속도 (ANGULAR_Z_MAX 아래로 유지)
ANGULAR_Z_SEARCH = 0.35       # rad/s — 180° 정렬 회전(AlignHeading) 각속도
SEARCH_TURN_ANGLE = 3.14159   # AlignHeading 이 도는 각도 (radians)

# ---- loop ----
TICK_HZ = 20.0
FRAME_DT = 0.05               # nominal seconds per tick


# ---- 세션 ----
# 이 시간 갱신이 없으면 세션을 닫는다. 패널이 죽어 stop 이 안 오는 경우 대비.
SESSION_LEASE_SEC = 60.0
# camera_select 재발행 주기(Hz). 송출기 쪽 만료(CAMERA_SELECT_EXPIRY_SEC)보다
# **촘촘해야** 한다 — 안 그러면 정상 세션 중에 카메라가 깜빡인다.
CAMERA_SELECT_HZ = 2.0
# 송출기가 이 시간 갱신을 못 받으면 스스로 none 으로 떨어진다(참고용 — 실제 값은
# camera_sender 쪽 --select-expiry 다). 여기 적어두는 이유는 둘의 관계가 규칙이기 때문.
CAMERA_SELECT_EXPIRY_SEC = 3.0

# ---- transport ----
DETECTION_TCP_HOST = '0.0.0.0'
DETECTION_TCP_PORT = 6000
SCAN_TOPIC = '/scan'
# ⚠️ `/cmd_vel` 직접 발행 금지. twist_mux 가 중재하는 입력으로 낸다
#    (pinky_bringup/config/twist_mux.yaml — follow, priority 100).
#    직접 내면 중재를 우회해 nav2·수동조작과 마지막 메시지 싸움이 된다.
CMD_VEL_TOPIC = '/cmd_vel_follow'
CAMERA_SELECT_TOPIC = '/libi/camera_select'
REQUESTER_VISIBLE_TOPIC = '/libi/requester_visible'
REQUESTER_AREA_TOPIC = '/libi/requester_area'
