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
