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


# ── motion_ok 게이트 ─────────────────────────────────────────────────────────
# "보이지만 가면 안 된다"(누움 / 코앞 / 자세 측정 중)와 "놓쳤다"는 다르다.
# 전자를 후자로 처리하면 눈앞에 멀쩡히 보이는 대상을 두고 탐색 회전을 시작한다.

def _blocked_det():
    return Detection(cx=320.0, cy=240.0, area=100.0, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9, is_predicted=False,
                     posture="Lying", motion_ok=False)


def test_motion_blocked_publishes_zero():
    pub = _Pub()
    loop = ControlLoop(lambda: _blocked_det(), lambda: [], pub, _cfg(), now=_Clock())
    loop.tick()
    assert pub.calls[-1] == (0.0, 0.0)


def test_motion_blocked_stays_tracking():
    pub = _Pub()
    cfg = _cfg(N_MISS_FRAMES=3)
    loop = ControlLoop(lambda: _blocked_det(), lambda: [], pub, cfg, now=_Clock())
    for _ in range(cfg.N_MISS_FRAMES + 5):
        loop.tick()
    assert loop.state == 'TRACKING'          # 정지일 뿐 소실이 아니다


def test_motion_blocked_does_not_count_as_miss():
    pub = _Pub()
    loop = ControlLoop(lambda: _blocked_det(), lambda: [], pub, _cfg(), now=_Clock())
    for _ in range(10):
        loop.tick()
    assert loop.miss == 0


def test_motion_allowed_again_resumes_tracking():
    pub = _Pub()
    dets = [_blocked_det(), _blocked_det(), _det()]
    loop = ControlLoop(lambda: dets.pop(0) if dets else _det(),
                       lambda: [], pub, _cfg(), now=_Clock())
    loop.tick(); loop.tick()
    assert pub.calls[-1] == (0.0, 0.0)
    loop.tick()
    assert pub.calls[-1] != (0.0, 0.0)         # 다시 몰기 시작한다


def test_missing_motion_ok_field_does_not_block():
    """옛 payload(필드 없음)에서 로봇이 영영 안 움직이면 안 된다."""
    pub = _Pub()
    loop = ControlLoop(lambda: _det(), lambda: [], pub, _cfg(), now=_Clock())
    loop.tick()
    assert pub.calls[-1] != (0.0, 0.0)
