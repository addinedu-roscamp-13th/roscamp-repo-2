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
                SEARCH_HOLD_SEC=5.0, SEARCH_SWEEP_ANGLE=3.14159, ANGULAR_Z_SWEEP=0.55,
                ANGULAR_Z_SEARCH=0.35,
                SEARCH_TURN_ANGLE=3.14159, FRAME_DT=0.05)
    base.update(over)
    return SimpleNamespace(**base)


def _det():
    return Detection(cx=320.0, cy=240.0, area=100.0, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9, is_predicted=False)


def _clear_scan():
    """장애물 없는 스캔.

    ⚠️ 예전엔 여기가 `lambda: []` 였다. [2026-07-31] 부터 **빈 스캔은 "라이다를 못
    본다"**는 뜻이고 전진이 막힌다(`lidar_avoidance.apply_avoidance`). "앞이 뚫렸다"를
    말하려면 실제로 뚫린 스캔을 줘야 한다.
    """
    return [10.0] * 360


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
    loop = ControlLoop(get_detection=lambda: _det(), get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(), now=_Clock())
    loop.tick()
    assert loop.state == 'TRACKING'
    assert pub.calls[-1][0] > 0


def test_transitions_to_searching_after_misses():
    det_box = {'v': _det()}
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: det_box['v'], get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=3), now=_Clock())
    loop.tick()                       # tracking
    det_box['v'] = None
    for _ in range(3):
        loop.tick()
    assert loop.state == 'SEARCHING'


def test_searching_reacquire_returns_to_tracking():
    det_box = {'v': _det()}
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: det_box['v'], get_scan=_clear_scan,
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
    loop = ControlLoop(get_detection=lambda: None, get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=1), now=clock)
    # need one detection first so tracking starts; use a togglable source
    calls = {'n': 0}

    def src():
        calls['n'] += 1
        return _det() if calls['n'] == 1 else None

    loop2 = ControlLoop(get_detection=src, get_scan=_clear_scan, publish=pub,
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
    loop = ControlLoop(lambda: _blocked_det(), _clear_scan, pub, _cfg(), now=_Clock())
    loop.tick()
    assert pub.calls[-1] == (0.0, 0.0)


def test_motion_blocked_stays_tracking():
    pub = _Pub()
    cfg = _cfg(N_MISS_FRAMES=3)
    loop = ControlLoop(lambda: _blocked_det(), _clear_scan, pub, cfg, now=_Clock())
    for _ in range(cfg.N_MISS_FRAMES + 5):
        loop.tick()
    assert loop.state == 'TRACKING'          # 정지일 뿐 소실이 아니다


def test_motion_blocked_does_not_count_as_miss():
    pub = _Pub()
    loop = ControlLoop(lambda: _blocked_det(), _clear_scan, pub, _cfg(), now=_Clock())
    for _ in range(10):
        loop.tick()
    assert loop.miss == 0


def test_motion_allowed_again_resumes_tracking():
    pub = _Pub()
    dets = [_blocked_det(), _blocked_det(), _det()]
    loop = ControlLoop(lambda: dets.pop(0) if dets else _det(),
                       _clear_scan, pub, _cfg(), now=_Clock())
    loop.tick(); loop.tick()
    assert pub.calls[-1] == (0.0, 0.0)
    loop.tick()
    assert pub.calls[-1] != (0.0, 0.0)         # 다시 몰기 시작한다


def test_missing_motion_ok_field_does_not_block():
    """옛 payload(필드 없음)에서 로봇이 영영 안 움직이면 안 된다."""
    pub = _Pub()
    loop = ControlLoop(lambda: _det(), _clear_scan, pub, _cfg(), now=_Clock())
    loop.tick()
    assert pub.calls[-1] != (0.0, 0.0)


# ── 등록 전에는 탐색하지 않는다 (2026-07-28 실측) ─────────────────────────────
# 잃어버리려면 먼저 가지고 있어야 한다. 등록 전에는 검출이 계속 None 이라, 이 검사가
# 없으면 세션을 연 지 2초(N_MISS_FRAMES 40 @20Hz) 만에 회복 BT 가 돌았다 —
# 아무도 등록하지 않았는데 로봇이 혼자 돌며 앞뒤 캠을 번갈아 켰다.

def test_never_searches_before_the_first_detection():
    """이 파일의 존재 이유. 사람은 아직 화면에서 자기를 등록하는 중이다."""
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: None, get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=3), now=_Clock())
    for _ in range(50):               # 문턱을 한참 넘긴다
        loop.tick()
    assert loop.state == 'TRACKING', "등록 전에 탐색으로 빠졌다 — 로봇이 혼자 돈다"
    assert loop.search_tree is None, "회복 BT 가 만들어졌다"


def test_holds_still_while_waiting_for_registration():
    """기다리는 동안 바퀴가 돌면 안 된다."""
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: None, get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=3), now=_Clock())
    for _ in range(20):
        loop.tick()
    assert pub.calls, "아무것도 발행하지 않았다"
    assert all(c == (0.0, 0.0) for c in pub.calls), f"정지가 아닌 명령: {set(pub.calls)}"


def test_searches_once_the_target_has_been_seen():
    """등록 뒤에 놓친 것은 진짜 놓친 것 — 그때는 탐색해야 한다."""
    det_box = {'v': None}
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: det_box['v'], get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=3), now=_Clock())
    for _ in range(10):               # 등록 전 — 탐색 안 함
        loop.tick()
    assert loop.state == 'TRACKING'

    det_box['v'] = _det()             # 등록됨
    loop.tick()
    det_box['v'] = None               # 놓침
    for _ in range(3):
        loop.tick()
    assert loop.state == 'SEARCHING', "등록 뒤 놓쳤는데 탐색을 안 한다"


def test_acquisition_survives_a_recovery_round():
    """회복에 성공해 다시 놓치면, 두 번째도 탐색해야 한다(플래그가 리셋되면 안 된다)."""
    det_box = {'v': _det()}
    pub = _Pub()
    loop = ControlLoop(get_detection=lambda: det_box['v'], get_scan=_clear_scan,
                       publish=pub, cfg=_cfg(N_MISS_FRAMES=1), now=_Clock())
    loop.tick()
    det_box['v'] = None
    loop.tick()
    assert loop.state == 'SEARCHING'
    det_box['v'] = _det()
    loop.tick()                       # 재획득
    assert loop.state == 'TRACKING'
    det_box['v'] = None
    loop.tick()
    assert loop.state == 'SEARCHING', "두 번째 놓침에서 탐색을 안 한다"
