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


def test_guide_bypasses_the_motion_gate():
    """길잡이는 뒷카메라 자세로 nav2 감시/회복을 막지 않는다."""
    pub = _Pub()
    loop = ControlLoop(lambda: _blocked_det(), _clear_scan, pub, _cfg(),
                       now=_Clock(), role="guide")
    loop.tick()
    assert pub.calls[-1][0] > 0


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
    """⚠️ [2026-08-02] **유예(`GUIDE_COAST_SEC`)가 끝난 뒤부터** 놓침으로 친다.

    예전엔 예측 bbox 를 첫 프레임부터 거부해 곧바로 놓침이었다. 지금은 안내도
    추종과 같은 1.4초를 코스팅한다 — 그 안에서는 놓침이 아니다. 유예를 넘긴
    뒤에는 예전과 똑같이 놓침으로 쌓여 SEARCHING 으로 간다.
    """
    pub = _Pub()
    clock = _Clock()
    dets = [_det()]                      # 먼저 한 번 잡아야 소실이 성립한다
    loop = ControlLoop(lambda: dets.pop(0) if dets else _predicted_det(),
                       _clear_scan, pub, _cfg(N_MISS_FRAMES=2, GUIDE_COAST_SEC=1.4),
                       now=clock, role="guide")
    loop.tick()                          # 실검출 → TRACKING
    loop.tick()                          # 예측 — 유예 안이라 아직 놓침 아니다
    assert loop.state == 'TRACKING', "유예 안인데 벌써 놓쳤다고 한다"
    clock.t = 2.0                        # 유예(1.4s) 초과
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

    clock = _Clock()
    loop = ControlLoop(get_detection, _clear_scan, pub,
                       _cfg(N_MISS_FRAMES=2, GUIDE_COAST_SEC=1.4),
                       now=clock, role="guide")
    loop.tick()                          # TRACKING (실검출)
    loop.tick()                          # 예측 — 여기서 코스팅 유예가 시작된다(t=0)
    clock.t = 2.0                        # 유예(1.4s)를 넘긴다
    loop.tick(); loop.tick()             # 예측 2회 → 놓침 2회 → SEARCHING
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


# ── 길잡이: 코스팅 → 대기 → 회복 BT, 한 라운드 (사용자 스펙 2026-08-02) ──────
#
#   [0, coast)          α-β 로 계속 간다        ← GuideExec 이 nav2 를 안 끊는다
#   [coast, +wait)      정지하고 기다린다        ← SEARCHING 이지만 **트리가 없다**
#   [coast+wait, …)     회복 BT 한 라운드        ← rotation_granted() 가 시작한다
#   소진                 ENDED → /libi/guide_search_failed → 안내 종료
#
# 대기 구간의 길이는 `GuideExec`(`guide_wait_sec`)이 쥔다. 이쪽은 "허가 전에는 탐색을
# 시작하지 않는다"만 지킨다 — 트리를 미리 돌리면 바퀴가 묶인 채 시계만 흘러서 한
# 라운드(32.8초)의 앞부분이 통째로 낭비된다(실측: 실제 회전 13초).

def _lost(role):
    """한 번 잡았다가 놓쳐 SEARCHING 에 들어간 루프."""
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
    return loop, clock


def _search_exhausted(role):
    """탐색까지 소진시킨 루프. 길잡이는 허가를 받아야 탐색이 시작된다."""
    loop, clock = _lost(role)
    if role == "guide":
        loop.rotation_granted()
    clock.t = 10_000.0
    loop.tick()                 # 탐색 소진
    return loop, clock


def test_guide_waits_without_a_search_tree():
    """대기 구간에는 **트리가 없다.** 돌리면 바퀴가 묶인 채 시계만 흐른다."""
    loop, _ = _lost("guide")
    assert loop.state == 'SEARCHING'
    assert loop.search_tree is None, "허가 전인데 탐색이 시작됐다"


def test_guide_stands_still_while_waiting():
    loop, clock = _lost("guide")
    clock.t = 5.0
    loop.tick()
    n = len(loop.publish.calls)
    loop.tick(); loop.tick()
    assert all(c == (0.0, 0.0) for c in loop.publish.calls[n:]), "대기 중에 바퀴가 돌았다"


def test_guide_search_starts_on_the_grant():
    loop, clock = _lost("guide")
    clock.t = 21.4              # 코스팅 1.4 + 대기 20
    loop.rotation_granted()
    assert loop.search_tree is not None
    assert loop._search_ctx.start == 21.4, "탐색 시계가 허가 시점에서 시작해야 한다"


def test_guide_ends_when_the_search_is_exhausted():
    """한 라운드를 다 훑으면 끝난다 — 그 `ENDED` 가 안내를 끝내는 신호다
    (`follow_node._publish_guide_search_failed` → `GuideExec`).

    되돌림 감지: 길잡이 전용 재시작을 다시 넣으면 SEARCHING 으로 남아 빨개진다.
    """
    loop, _ = _search_exhausted("guide")
    assert loop.state == 'ENDED'


def test_follow_searches_immediately():
    """추종은 바퀴가 처음부터 자기 것이라 기다릴 이유가 없다."""
    loop, _ = _lost("follow")
    assert loop.search_tree is not None


def test_watch_searches_immediately():
    loop, _ = _lost("watch")
    assert loop.search_tree is not None


def test_follow_still_ends_when_the_search_is_exhausted():
    """추종은 예전 그대로 — 회복이 소진되면 세션이 실패로 끝나야 한다."""
    loop, _ = _search_exhausted("follow")
    assert loop.state == 'ENDED'


def test_watch_still_ends_when_the_search_is_exhausted():
    """등록감시도 같다. 무한 반복시키면 캠이 영영 앞뒤로 튄다."""
    loop, _ = _search_exhausted("watch")
    assert loop.state == 'ENDED'


def test_rotation_grant_is_a_noop_outside_searching():
    """추종 중이나 끝난 뒤에 탐색을 세우면 **없던 회복이 생긴다.**"""
    loop, _ = _search_exhausted("guide")
    assert loop.state == 'ENDED'
    loop.rotation_granted()
    assert loop.state == 'ENDED'
    assert loop.search_tree is None


def test_second_loss_carries_over_the_actual_camera():
    """두 번째 소실의 탐색은 앞 탐색이 **실제로 남겨 둔 캠**을 물려받는다.

    새 컨텍스트가 "나는 이미 home" 이라고 낙관하면, `CheckReacquired` 가 아직
    반대 캠인 상태의 검출을 "정위치에서 봤다"로 오인한다(codex 2026-08-01 발견).
    """
    loop, clock = _lost("guide")
    loop.rotation_granted()
    clock.t = 40.0
    loop.tick()                                  # 첫 탐색을 peek 캠에서 끝낸다
    left_at = loop._search_ctx.camera_now()
    loop.switch.restart()                        # 다시 잡았다 치고
    loop.get_detection = lambda: None
    loop.miss = 0
    loop.tick(); loop.tick()                     # 다시 놓침 -> 대기
    loop.rotation_granted()
    assert loop._search_ctx.camera_now() == left_at


# ── 측면은 전진만 막는다 (2026-08-01, 2026-07-26 설계로 복귀) ─────────────────

def _side_det(cx=320.0):
    """측면으로 판정된 대상. `motion_ok=False` 인 것은 누움과 같지만 처리는 다르다."""
    return Detection(cx=cx, cy=240.0, area=100.0, bbox=(0, 0, 10, 10),
                     track_id=1, is_owner=True, confidence=0.9, is_predicted=False,
                     posture="Side", motion_ok=False)


def test_side_blocks_forward_but_keeps_bearing():
    """⚠️ **따라가는 사람은 대부분 등·옆을 보인다.**

    측면을 누움과 똑같이 완전 정지로 묶으면 화면은 FOLLOWING 인데 바퀴는 0 인
    상태가 계속된다(실측 2026-08-01). 측면에서 못 믿는 것은 거리(√area)뿐이고
    방위(cx)는 멀쩡하므로, 전진만 막고 사람은 화면에 붙잡아 둔다.
    """
    pub = _Pub()
    # 중앙에서 크게 벗어나 방위 오차가 데드존을 넘게 둔다.
    loop = ControlLoop(lambda: _side_det(cx=40.0), _clear_scan, pub, _cfg(), now=_Clock())
    loop.tick()
    lin, ang = pub.calls[-1]
    assert lin == 0.0, f"측면에서는 전진하면 안 된다: {lin}"
    assert ang != 0.0, "측면이라도 방위는 따라가야 한다 — 안 그러면 사람을 놓친다"


def test_side_centered_still_publishes_zero_angular():
    """가운데 있으면 돌 이유가 없다 — 측면이라고 억지로 돌지 않는다."""
    pub = _Pub()
    loop = ControlLoop(lambda: _side_det(cx=320.0), _clear_scan, pub, _cfg(), now=_Clock())
    loop.tick()
    assert pub.calls[-1] == (0.0, 0.0)


def test_lying_still_stops_completely():
    """누움은 예전 그대로 **완전 정지**다 — 쓰러진 사람 쪽으로 돌지도 않는다."""
    pub = _Pub()
    det = Detection(cx=40.0, cy=240.0, area=100.0, bbox=(0, 0, 10, 10),
                    track_id=1, is_owner=True, confidence=0.9, is_predicted=False,
                    posture="Lying", motion_ok=False)
    loop = ControlLoop(lambda: det, _clear_scan, pub, _cfg(), now=_Clock())
    loop.tick()
    assert pub.calls[-1] == (0.0, 0.0)


def test_side_does_not_count_as_miss_either():
    """측면도 "놓친 것"이 아니다 — 탐색 회전을 시작하면 안 된다."""
    pub = _Pub()
    loop = ControlLoop(lambda: _side_det(cx=40.0), _clear_scan, pub, _cfg(), now=_Clock())
    for _ in range(10):
        loop.tick()
    assert loop.miss == 0
    assert loop.state == 'TRACKING'


# ── 가운데 소실이면 LKD peek 를 건너뛴다 (2026-08-02) ───────────────────────
# 사용자 지시: "알파베타 필터가 가운데에서 사라지면 peek 가 없어도 될 것 같다."
# 가운데 소실 = 어느 쪽으로 나간 게 아니라 **가려진 것**이라, 마지막 회전 방향으로
# 90° 도는 것은 근거 없는 추측이다.

def _peek_of(loop):
    """지금 도는 회복 컨텍스트가 peek 를 켰는지."""
    return loop._search_ctx.peek


def test_center_loss_skips_the_lkd_peek():
    cfg = _cfg(N_MISS_FRAMES=1, IMAGE_WIDTH=320, ANGLE_DEADZONE_FRAC=1.0 / 24.0)
    d = _det(); d.cx = 160.0          # 정중앙
    seen = [d]
    loop = ControlLoop(lambda: seen.pop(0) if seen else None,
                       _clear_scan, _Pub(), cfg, now=_Clock())
    loop.tick()                       # 잡았다 — 가운데
    loop.tick()                       # 놓쳤다 → 탐색 시작
    assert loop.state == 'SEARCHING'
    assert _peek_of(loop) is False, "가운데에서 사라졌는데 peek 를 켰다"


def test_off_center_loss_keeps_the_lkd_peek():
    """옆으로 사라졌으면 그쪽부터 보는 것이 맞다 — 기존 동작."""
    cfg = _cfg(N_MISS_FRAMES=1, IMAGE_WIDTH=320, ANGLE_DEADZONE_FRAC=1.0 / 24.0)
    d = _det(); d.cx = 20.0           # 왼쪽 끝
    seen = [d]
    loop = ControlLoop(lambda: seen.pop(0) if seen else None,
                       _clear_scan, _Pub(), cfg, now=_Clock())
    loop.tick()
    loop.tick()
    assert loop.state == 'SEARCHING'
    assert _peek_of(loop) is True, "옆에서 사라졌는데 peek 를 껐다"


def test_peek_flag_actually_removes_the_phase_from_the_tree():
    """플래그가 트리에서 **정말** 그 구간을 없애는지 — 플래그만 세우고 끝나면 무의미하다."""
    from libi_perception.search_planner import peek_sec
    from libi_perception import config
    assert peek_sec(config, "follow", True) > 0, "추종인데 peek 시간이 0 이다"
    assert peek_sec(config, "follow", False) == 0.0, "꺼도 peek 시간이 남는다"


# ── 안내(guide) 코스팅 유예 (2026-08-02) ────────────────────────────────────
# 예전엔 안내에서 예측 bbox 를 통째로 거부했다(코스팅 0초). 안내는 로봇이 앞서고
# 요청자가 뒤따르는 구조라 잠깐 가려지는 일이 추종보다 잦다.

class _MoveClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def _pred():
    d = _det(); d.is_predicted = True; return d


def test_guide_accepts_predicted_within_the_grace():
    clock = _MoveClock()
    cfg = _cfg(GUIDE_COAST_SEC=1.4)
    loop = ControlLoop(_pred, _clear_scan, _Pub(), cfg, now=clock, role="guide")
    assert loop._filtered_detection() is not None, "유예 시작인데 거부했다"
    clock.t = 1.3
    assert loop._filtered_detection() is not None, "유예(1.4s) 안인데 거부했다"


def test_guide_drops_predicted_after_the_grace():
    clock = _MoveClock()
    cfg = _cfg(GUIDE_COAST_SEC=1.4)
    loop = ControlLoop(_pred, _clear_scan, _Pub(), cfg, now=clock, role="guide")
    loop._filtered_detection()
    clock.t = 1.5
    assert loop._filtered_detection() is None, "유예를 넘겼는데 아직 보인다고 한다"


def test_a_real_detection_rewinds_the_guide_grace():
    """진짜로 다시 보이면 유예가 되감겨야 한다 — 안 그러면 누적돼 조기에 끊긴다."""
    clock = _MoveClock()
    cfg = _cfg(GUIDE_COAST_SEC=1.4)
    seq = [_pred(), _det(), _pred()]
    loop = ControlLoop(lambda: seq[min(int(clock.t), len(seq) - 1)],
                       _clear_scan, _Pub(), cfg, now=clock, role="guide")
    loop._filtered_detection()            # t=0 예측 → 유예 시작
    clock.t = 1.0
    loop._filtered_detection()            # t=1 진짜 → 되감김
    clock.t = 2.0
    assert loop._filtered_detection() is not None, "되감기가 안 돼 조기에 끊겼다"


def test_follow_role_is_untouched_by_the_guide_grace():
    """추종은 예측을 항상 통과시킨다 — 코스팅이 제어의 연속성을 만든다."""
    clock = _MoveClock()
    loop = ControlLoop(_pred, _clear_scan, _Pub(), _cfg(GUIDE_COAST_SEC=1.4),
                       now=clock, role="follow")
    loop._filtered_detection()
    clock.t = 99.0
    assert loop._filtered_detection() is not None, "추종에서 예측이 걸러졌다"
