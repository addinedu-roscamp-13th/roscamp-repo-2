"""Preview of the /cmd_vel values a controller WOULD publish for the owner.

This does NOT publish ROS — it only computes the numbers so they can be shown
on screen. Later these become the real published values (or are replaced by
follower_control's PID, which is the smooth version of this bang-bang preview).

Direction: split the frame width into thirds (left/center/right).
Distance:  sqrt(bbox area) vs a target size (larger box = closer).

ROS convention: angular.z > 0 = turn left (CCW), linear.x > 0 = forward.
Reference for the real values: follower_control/config.py (KP_ANGLE, TARGET_SIZE…).
"""

# Tunable demo defaults (start here, adjust to taste).
ANGULAR_SPEED = 0.25          # rad/s applied when owner is off-center (matches search)
LINEAR_SPEED = 0.06           # m/s forward when too far   (was 0.075)
LINEAR_SPEED_REVERSE = 0.04   # m/s backward when too close (was 0.05)
TARGET_SIZE = 300.0           # target sqrt(area) in px == desired follow distance (closer)
SIZE_DEADBAND = 30.0          # px band around target where the robot holds (stop)


def compute_cmd_vel(det, frame_w):
    """Return {linear_x, angular_z, drive, turn} for the owner Detection.
    No owner -> all-stop."""
    if det is None or not getattr(det, "is_owner", False):
        return {"linear_x": 0.0, "angular_z": 0.0, "drive": "STOP", "turn": "-"}

    # --- direction: which third is the owner's center x in? ---
    third = frame_w / 3.0
    if det.cx < third:
        angular_z, turn = ANGULAR_SPEED, "LEFT"      # owner on left -> turn left
    elif det.cx > 2.0 * third:
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
