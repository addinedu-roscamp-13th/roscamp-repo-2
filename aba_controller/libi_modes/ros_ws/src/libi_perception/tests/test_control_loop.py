from types import SimpleNamespace
from libi_perception.detection import Detection
from libi_perception.control_loop import ControlLoop


def _cfg(**over):
    base = dict(TARGET_SIZE=360.0, KP_DIST=0.0030, KI_DIST=0.0, KD_DIST=0.0,
                INTEGRAL_DIST_CLAMP=50.0, DIST_DEADZONE=0.0,
                LINEAR_X_MAX=0.12, LINEAR_X_REVERSE_MAX=0.06,
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


# ── 예측 검출은 안내에서만 '놓쳤다' 다 ────────────────────────────────────────
# 안내의 가시성 발행(`requester_visible`)이 예측을 거부하는데 여기가 TRACKING 을
# 유지하면 한 프로세스 안에 "놓쳤다" 가 두 개가 된다. 정지는 0.5초, 탐색 진입은
# 4초가 되어 3.5초 동안 로봇이 서서 한쪽 캠만 본다.
#
# ⚠️ 회복 트리도 같은 필터를 봐야 한다 — TRACKING 분기만 고치면 SEARCHING 이
# 예측 bbox 한 프레임에 즉시 TRACKING 으로 되돌아간다(codex 2026-08-01 발견).

def _predicted_det():
    return Detection(cx=320.0, cy=240.0, area=100.0, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9, is_predicted=True)


def test_guide_counts_a_predicted_detection_as_a_miss():
    pub = _Pub()
    dets = [_det()]                      # 먼저 한 번 잡아야 소실이 성립한다
    loop = ControlLoop(lambda: dets.pop(0) if dets else _predicted_det(),
                       _clear_scan, pub, _cfg(N_MISS_FRAMES=2), now=_Clock(),
                       role="guide")
    loop.tick()                          # 실검출 → TRACKING
    loop.tick(); loop.tick()             # 예측 2회 → 놓침 2회
    assert loop.state == 'SEARCHING'


def test_guide_recovery_does_not_reacquire_on_a_predicted_detection():
    """이게 진짜 시험이다 — SEARCHING 중에도 예측 bbox 는 재획득이 아니다.

    `_start_search()` 가 필터 안 걸린 `get_detection` 을 `SearchContext` 에
    넘기면 이 시험이 깨진다. 회복 트리의 `CheckReacquired` 는 자기가 받은
    콜백을 그대로 믿을 뿐이라, 필터는 반드시 콜백 자체에 있어야 한다.
    """
    pub = _Pub()
    dets = [_det()]
    src = {'n': 0}

    def get_detection():
        src['n'] += 1
        if src['n'] <= 1:
            return dets.pop(0)                    # 첫 tick: 실검출 → TRACKING
        return _predicted_det()                   # 이후 계속 예측만 나옴

    loop = ControlLoop(get_detection, _clear_scan, pub,
                       _cfg(N_MISS_FRAMES=2), now=_Clock(), role="guide")
    loop.tick()                          # TRACKING (실검출)
    loop.tick(); loop.tick()             # 예측 2회 → SEARCHING
    assert loop.state == 'SEARCHING'
    loop.tick()                          # 회복 중 예측 bbox 한 번 더
    assert loop.state == 'SEARCHING', (
        "예측 bbox 로 재획득되면 안 된다 — TRACKING 으로 돌아갔다면 회복 트리가"
        " 필터 안 걸린 get_detection 을 직접 받고 있다는 뜻이다")


def test_follow_still_tracks_a_predicted_detection():
    """추종은 코스팅을 그대로 쓴다 — 이 시험이 그 보장이다."""
    pub = _Pub()
    dets = [_det()]
    loop = ControlLoop(lambda: dets.pop(0) if dets else _predicted_det(),
                       _clear_scan, pub, _cfg(N_MISS_FRAMES=2), now=_Clock(),
                       role="follow")
    loop.tick()
    loop.tick(); loop.tick(); loop.tick()
    assert loop.state == 'TRACKING'
    assert loop.miss == 0


def test_watch_still_tracks_a_predicted_detection():
    """등록감시도 예전 그대로 — 여기서 탐색이 빨라지면 패널 화면이 앞캠으로 뒤집힌다."""
    pub = _Pub()
    dets = [_det()]
    loop = ControlLoop(lambda: dets.pop(0) if dets else _predicted_det(),
                       _clear_scan, pub, _cfg(N_MISS_FRAMES=2), now=_Clock(),
                       role="watch")
    loop.tick()
    loop.tick(); loop.tick(); loop.tick()
    assert loop.state == 'TRACKING'


# ── 안내 회복은 세션이 살아 있는 한 끝나지 않는다 ────────────────────────────
# `ENDED` 에는 빠져나오는 길이 없다(switch._TRANSITIONS 에 restart 만 있고 안내
# 경로에서 아무도 안 부른다). 거기 떨어지면 사람이 돌아와도 재획득 판정이 죽는다.
# 회복 타임라인(≈32.8초)이 guide_lost_timeout_sec(45초)보다 짧아 실제로 밟힌다.

def _search_exhausted(role):
    """한 번 잡았다가 놓치고 탐색까지 소진시킨 루프를 돌려준다."""
    clock = _Clock()
    calls = {'n': 0}

    def src():
        calls['n'] += 1
        return _det() if calls['n'] == 1 else None

    loop = ControlLoop(get_detection=src, get_scan=_clear_scan, publish=_Pub(),
                       cfg=_cfg(N_MISS_FRAMES=1), now=clock, role=role)
    clock.t = 0.0
    loop.tick()                 # TRACKING
    loop.tick()                 # 놓침 -> SEARCHING
    clock.t = 10_000.0
    loop.tick()                 # 탐색 소진
    return loop, clock


def test_guide_restarts_the_search_instead_of_ending():
    loop, _ = _search_exhausted("guide")
    assert loop.state == 'SEARCHING'


def test_guide_restart_resets_the_search_timeline():
    """되감지 않으면 다음 tick 에 또 소진돼 사실상 아무것도 안 한다."""
    loop, clock = _search_exhausted("guide")
    clock.t = 10_001.0
    loop.tick()
    assert loop.state == 'SEARCHING'


def test_guide_can_still_reacquire_after_a_restart():
    """이 변경의 요점 — 사람이 돌아오면 추종으로 복귀한다.

    ⚠️ tick 이 **두 번** 필요하다(브리프 원안은 한 번이었으나 캠 계승 시험과
    같은 근거로 고쳤다 — 아래 참고). 재시작 직후의 첫 tick 에서는
    `CheckReacquired` 가 아직 이전 캠(front)에 머문 상태라 이 검출을 거부한다
    (`test_guide_restart_carries_over_the_actual_camera` 가 잡는 바로 그 창).
    그 tick **안에서** `HoldFront` 가 캠을 home(back)으로 되돌리지만, Selector
    는 `CheckReacquired` → `SearchPhases` 순으로 매 tick 한 번씩만 훑으므로
    갱신된 캠은 다음 tick 에야 보인다. 그래서 두 번째 tick 에서 캠(back)과
    검출이 함께 맞아떨어져야 재획득한다.
    """
    loop, clock = _search_exhausted("guide")
    loop.get_detection = lambda: _det()
    loop.tick()
    assert loop.state == 'SEARCHING', (
        "첫 tick 은 아직 이전 캠(front)에 머문 상태 — 여기서 바로 재획득되면"
        " 캠 계승 전 상태를 재획득으로 오인하는 버그가 되돌아온 것이다")
    loop.tick()
    assert loop.state == 'TRACKING'


def test_follow_still_ends_when_the_search_is_exhausted():
    """추종은 예전 그대로 — 회복이 소진되면 세션이 실패로 끝나야 한다."""
    loop, _ = _search_exhausted("follow")
    assert loop.state == 'ENDED'


def test_watch_still_ends_when_the_search_is_exhausted():
    """등록감시에는 45초 종결자가 없다 — 무한 반복시키면 영영 안 끝난다."""
    loop, _ = _search_exhausted("watch")
    assert loop.state == 'ENDED'


def test_guide_restart_carries_over_the_actual_camera():
    """재시작 **직후, 트리가 한 번도 안 돈 시점**의 캠 상태를 잰다.

    `_search_exhausted()` 는 마지막 tick 안에서 소진과 재시작이 **같은 tick**에
    다 일어난다(가이드는 FAILURE 를 받는 즉시 `_start_search()` 를 다시 부르므로).
    그 순간, 새 컨텍스트가 "나는 이미 home(back)" 이라고 낙관적으로 가정하면,
    다음 tick 에 `CheckReacquired` 가 **아직 실제로는 front 인** 캠에서 온
    검출을 "정위치에서 봤다"로 오인할 수 있다(codex 2026-08-01 발견).

    ⚠️ 여기서 tick 을 한 번 더 돌리면 `HoldFront` 페이즈가 자기 차례에 캠 전환을
    직접 요청해 값이 `back` 으로 바뀐다 — 그건 **정상 동작**이고 이 시험이
    잡으려는 창이 아니다. 그래서 재시작 **직후**, 추가 tick 없이 확인한다.
    """
    loop, _ = _search_exhausted("guide")
    # 소진된 회복의 마지막 구간(SweepBackHome)은 peek 캠(front)에서 끝난다.
    # 새 컨텍스트가 그 사실을 물려받았어야 한다 — home_camera(back)라고
    # 낙관적으로 가정했다면 여기서 'back' 이 나온다(고쳐지지 않았다는 뜻).
    assert loop._search_ctx.camera_now() == 'front'


def test_guide_recovery_gives_up_after_one_restart():
    """docking 이 빌려 쓰는 guide_watch 세션(GuideExec 이 아니라 BackCamOn 이 연다)에는
    45초 종결자가 없다. 무제한 재시작을 두면 그 세션에서 캠이 도킹 내내 앞뒤로 튄다
    (2026-08-01 최종 브랜치 리뷰 발견 — 실제로 도킹을 막을 수 있는 버그였다).
    재시작은 1회만 허용하고, 두 번째 소진에서는 ENDED 로 끝낸다.
    """
    loop, clock = _search_exhausted("guide")   # 1차 소진 -> 이미 재시작 1회 씀
    assert loop.state == 'SEARCHING'
    clock.t = 20_000.0                          # 재시작한 트리도 소진시킨다
    loop.tick()
    assert loop.state == 'ENDED'
