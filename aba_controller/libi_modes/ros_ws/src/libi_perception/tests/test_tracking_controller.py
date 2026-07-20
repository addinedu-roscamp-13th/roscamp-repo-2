from types import SimpleNamespace
from libi_perception.detection import Detection
from libi_perception.tracking_controller import TrackingController


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
                IMAGE_WIDTH=640, KP_ANGLE=0.0010, KI_ANGLE=0.0, KD_ANGLE=0.0,
                INTEGRAL_ANGLE_CLAMP=200.0, ANGLE_DEADZONE=45.0, ANGULAR_Z_MAX=0.60,
                ANGULAR_SMOOTHING=1.0, MIN_DIST=0.20, AVOID_DIST=0.40, AVOID_KP=0.50,
                FRONT_ARC_DEG=15, SIDE_ARC=(20, 71))
    base.update(over)
    return SimpleNamespace(**base)


def _det(cx=320.0, area=100.0):
    return Detection(cx=cx, cy=240.0, area=area, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9, is_predicted=False)


class _Pub:
    def __init__(self):
        self.calls = []

    def __call__(self, lin, ang):
        self.calls.append((lin, ang))


def test_step_publishes_forward_for_far_target():
    pub = _Pub()
    ctrl = TrackingController(pub, _cfg())
    ctrl.step(_det(area=100.0), scan=[], dt=0.05)
    assert pub.calls[-1][0] > 0            # forward


def test_step_records_turn_direction():
    pub = _Pub()
    ctrl = TrackingController(pub, _cfg())
    ctrl.step(_det(cx=0.0), scan=[], dt=0.05)   # target far left -> +ang
    assert ctrl.last_direction == 1.0


def test_front_obstacle_slows_publish():
    pub = _Pub()
    ctrl = TrackingController(pub, _cfg())
    scan = [10.0] * 360
    scan[0] = 0.10
    ctrl.step(_det(area=100.0), scan=scan, dt=0.05)
    lin_blocked = pub.calls[-1][0]
    pub2 = _Pub()
    TrackingController(pub2, _cfg()).step(_det(area=100.0), scan=[], dt=0.05)
    assert lin_blocked < pub2.calls[-1][0]     # slowed vs unobstructed
