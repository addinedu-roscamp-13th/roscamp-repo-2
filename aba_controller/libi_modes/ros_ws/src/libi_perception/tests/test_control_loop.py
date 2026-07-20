from types import SimpleNamespace
from libi_perception.detection import Detection
from libi_perception.control_loop import ControlLoop


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
                IMAGE_WIDTH=640, KP_ANGLE=0.0010, KI_ANGLE=0.0, KD_ANGLE=0.0,
                INTEGRAL_ANGLE_CLAMP=200.0, ANGLE_DEADZONE=45.0, ANGULAR_Z_MAX=0.60,
                ANGULAR_SMOOTHING=1.0, MIN_DIST=0.20, AVOID_DIST=0.40, AVOID_KP=0.50,
                FRONT_ARC_DEG=15, SIDE_ARC=(20, 71), N_MISS_FRAMES=3,
                SEARCH_HOLD_SEC=10.0, SEARCH_SCAN_SEC=4.0, ANGULAR_Z_SEARCH=0.35,
                SEARCH_TURN_ANGLE=3.14159, FRAME_DT=0.05)
    base.update(over)
    return SimpleNamespace(**base)


def _det():
    return Detection(cx=320.0, cy=240.0, area=100.0, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9, is_predicted=False)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Pub:
    def __init__(self):
        self.calls = []

    def __call__(self, lin, ang):
        self.calls.append((lin, ang))


def test_tracks_when_detection_present():
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: _det(), get_scan=lambda: [],
                       publish=pub, cfg=_cfg(), now=_Clock())
    loop.tick()
    assert loop.state == 'TRACKING'
    assert pub.calls[-1][0] > 0


def test_transitions_to_searching_after_misses():
    det_box = {'v': _det()}
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: det_box['v'], get_scan=lambda: [],
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=3), now=_Clock())
    loop.tick()                       # tracking
    det_box['v'] = None
    for _ in range(3):
        loop.tick()
    assert loop.state == 'SEARCHING'


def test_searching_reacquire_returns_to_tracking():
    det_box = {'v': _det()}
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: det_box['v'], get_scan=lambda: [],
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=1), now=_Clock())
    loop.tick()
    det_box['v'] = None
    loop.tick()                       # -> SEARCHING
    assert loop.state == 'SEARCHING'
    det_box['v'] = _det()
    loop.tick()                       # reacquired
    assert loop.state == 'TRACKING'


def test_search_timeout_ends():
    clock = _Clock()
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: None, get_scan=lambda: [],
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=1), now=clock)
    # need one detection first so tracking starts; use a togglable source
    calls = {'n': 0}

    def src():
        calls['n'] += 1
        return _det() if calls['n'] == 1 else None

    loop2 = ControlLoop(get_detection=src, get_scan=lambda: [], publish=pub,
                        cfg=_cfg(N_MISS_FRAMES=1), now=clock)
    clock.t = 0.0
    loop2.tick()          # tracking
    loop2.tick()          # miss -> SEARCHING, search start=0
    clock.t = 10_000.0
    loop2.tick()          # search exhausted -> ENDED
    assert loop2.state == 'ENDED'
