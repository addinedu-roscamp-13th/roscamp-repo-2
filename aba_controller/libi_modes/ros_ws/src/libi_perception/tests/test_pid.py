from types import SimpleNamespace
from libi_perception.pid import FollowPID, clamp


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
                IMAGE_WIDTH=640, KP_ANGLE=0.0010, KI_ANGLE=0.0, KD_ANGLE=0.0,
                INTEGRAL_ANGLE_CLAMP=200.0, ANGLE_DEADZONE=45.0, ANGULAR_Z_MAX=0.60,
                ANGULAR_SMOOTHING=1.0)   # smoothing=1 -> deterministic single-step
    base.update(over)
    return SimpleNamespace(**base)


def test_clamp():
    assert clamp(5, 0, 1) == 1
    assert clamp(-5, 0, 1) == 0
    assert clamp(0.5, 0, 1) == 0.5


def test_far_target_drives_forward():
    pid = FollowPID(_cfg())
    lin, _ = pid.compute(cx=320.0, area=100.0, dt=0.05)   # sqrt=10 << 360
    assert lin > 0


def test_too_close_target_reverses_and_is_bounded():
    pid = FollowPID(_cfg())
    lin, _ = pid.compute(cx=320.0, area=1_000_000.0, dt=0.05)  # sqrt=1000 >> 360
    assert lin < 0
    assert lin >= -0.06        # bounded by LINEAR_X_REVERSE_MAX


def test_target_left_of_center_turns_left():
    pid = FollowPID(_cfg())
    _, ang = pid.compute(cx=0.0, area=100.0, dt=0.05)  # cx < width/2 -> err>0 -> +ang
    assert ang > 0


def test_deadzone_zeroes_small_bearing_error():
    pid = FollowPID(_cfg())
    _, ang = pid.compute(cx=320.0 - 10.0, area=100.0, dt=0.05)  # |err|=10 < 45
    assert ang == 0.0


def test_angular_clamped():
    pid = FollowPID(_cfg(KP_ANGLE=1.0))
    _, ang = pid.compute(cx=0.0, area=100.0, dt=0.05)
    assert ang <= 0.60
