"""Preview of the /cmd_vel values a controller WOULD publish for the owner.

This does NOT publish ROS — it only computes the numbers so they can be shown
on screen. Later these become the real published values (or are replaced by
follower_control's PID, which is the smooth version of this bang-bang preview).

Direction: split the frame width into thirds (left/center/right).
Distance:  sqrt(bbox area) vs a target size (larger box = closer).

ROS convention: angular.z > 0 = turn left (CCW), linear.x > 0 = forward.
Reference for the real values: follower_control/config.py (KP_ANGLE, TARGET_SIZE…).
"""

# ⚠️⚠️ [2026-08-02] **이 값들은 화면 표시 전용이고, 실제로 바퀴를 돌리는 값이 아니다.**
#
#   실제 제어는 로봇의 `libi_perception/pid.py` + `config.py` 다. 여기 나오는
#   `lin.x`/`ang.z` 는 AI 서버가 **혼자 계산해 화면에 굽는 미리보기**다.
#
#   그런데 두 표가 어긋나 있으면 화면이 거짓말을 한다. 실제로 2026-08-02 에
#   `TARGET_SIZE=300` 이 남아 있어서 — 320 폭 프레임에서 √area 최대가 277 이라
#   **도달 자체가 불가능한 값** — 패널 숫자로는 원인을 못 찾고 몇 시간을 썼다.
#
#   그래서 **로봇 config 와 같은 값으로 맞춰 둔다.** 아래를 바꿀 일이 생기면
#   `libi_perception/config.py` 를 먼저 보고 거기에 맞춘다. 정본은 그쪽이다.
#
#   ⚠️ 자동 동기화가 아니다. 다른 서비스라 import 할 수 없다 — 손으로 맞춘 사본이다.
#      로봇 쪽을 고치고 여기를 안 고치면 화면이 다시 거짓말을 시작한다.
ANGULAR_SPEED = 0.25          # rad/s — 미리보기는 bang-bang 이라 대표값 하나만 쓴다
LINEAR_SPEED = 0.06           # = config.LINEAR_X_MAX
LINEAR_SPEED_REVERSE = 0.04   # = config.LINEAR_X_REVERSE_MAX
TARGET_SIZE = 169.0           # = config.TARGET_SIZE   (320 원본 픽셀 기준)
SIZE_DEADBAND = 30.0          # = config.DIST_DEADZONE
#: 방위 정지 구간을 화면 폭 비율로. = config.ANGLE_DEADZONE_FRAC
#  예전에는 화면 3등분(±w/6)을 썼는데, 로봇은 2026-08-02 부터 ±w/24 다.
ANGLE_DEADZONE_FRAC = 1.0 / 24.0


def compute_cmd_vel(det, frame_w):
    """Return {linear_x, angular_z, drive, turn} for the owner Detection.
    No owner -> all-stop."""
    if det is None or not getattr(det, "is_owner", False):
        return {"linear_x": 0.0, "angular_z": 0.0, "drive": "STOP", "turn": "-"}

    # --- direction: 중심에서 얼마나 벗어났나 ---
    # ⚠️ [2026-08-02] 예전엔 화면 3등분(±w/6)이었다. 로봇 PID 는 `ANGLE_DEADZONE_FRAC`
    #    (=1/18, ±w/18)을 쓰므로 그 값으로 맞춘다. 안 맞추면 화면은 "CENTER" 인데
    #    로봇은 돌고 있는 상태가 되어 원인 추적이 불가능해진다.
    deadzone = frame_w * ANGLE_DEADZONE_FRAC
    err = frame_w / 2.0 - det.cx
    if err > deadzone:
        angular_z, turn = ANGULAR_SPEED, "LEFT"      # owner on left -> turn left
    elif err < -deadzone:
        angular_z, turn = -ANGULAR_SPEED, "RIGHT"    # owner on right -> turn right
    else:
        angular_z, turn = 0.0, "CENTER"

    # --- distance: sqrt(area) vs target ---
    # area can be <=0 from the smoother's prediction; clamp so sqrt stays real.
    size = max(0.0, float(det.area)) ** 0.5
    if size < TARGET_SIZE - SIZE_DEADBAND:
        linear_x, drive = LINEAR_SPEED, "FWD"        # too far -> forward
    elif size > TARGET_SIZE + SIZE_DEADBAND:
        linear_x, drive = -LINEAR_SPEED_REVERSE, "BACK"  # too close -> back
    else:
        linear_x, drive = 0.0, "STOP"

    return {"linear_x": linear_x, "angular_z": angular_z, "drive": drive, "turn": turn}
