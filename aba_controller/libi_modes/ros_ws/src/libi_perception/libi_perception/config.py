# All values are reference starting points for tuning — not hard requirements.

# ---- distance PID (linear_x) ----
TARGET_SIZE = 360.0            # sqrt(area) setpoint
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
AVOID_DIST = 0.40             # side-arc shy-away threshold (m)
AVOID_KP = 0.50
FRONT_ARC_DEG = 15            # +/- degrees around 0 (front)
SIDE_ARC = (20, 71)          # degrees range for a side arc (start, stop-exclusive)

# ---- miss / search ----
N_MISS_FRAMES = 40            # consecutive None before TRACKING -> SEARCHING
SEARCH_HOLD_SEC = 10.0        # phase 1: hold/wait before scanning
SEARCH_SCAN_SEC = 4.0         # duration of a +/-30 deg scan sweep
ANGULAR_Z_SEARCH = 0.35       # rad/s during search rotation
SEARCH_TURN_ANGLE = 3.14159   # phase 2: ~180 deg turn (radians)

# ---- loop ----
TICK_HZ = 20.0
FRAME_DT = 0.05               # nominal seconds per tick

# ---- transport ----
DETECTION_TCP_HOST = '0.0.0.0'
DETECTION_TCP_PORT = 6000
SCAN_TOPIC = '/scan'
CMD_VEL_TOPIC = '/cmd_vel'
