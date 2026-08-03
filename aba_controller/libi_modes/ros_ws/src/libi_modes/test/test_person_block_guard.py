"""PersonBlockGuard. 트리 안에서의 계약만 본다 — 특히 절대 SUCCESS 를 안 낸다."""
import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.person_block import PersonBlockGuard, PersonBlockPolicy


class FakeDriver:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start(self, args=None):
        self.starts += 1

    def poll(self):
        return "running"

    def stop(self):
        self.stops += 1


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _guard(**over):
    clock = Clock()
    calls = []
    camera_calls = []
    policy = PersonBlockPolicy(stop_size=209.0, sustain_sec=10.0,
                               resume_grace_sec=1.0)
    stop_driver = FakeDriver()
    g = PersonBlockGuard(policy, stop_driver=stop_driver,
                         block_fn=lambda node: calls.append(node),
                         camera_driver=lambda v: camera_calls.append(v),
                         now_fn=clock)
    g.setup()
    # 다른 시험이 읽기 편하게 인스턴스에 그대로 매달아 둔다 — `_guard()` 의 반환 튜플
    # 자리수를 안 늘려도 된다(기존 5-unpack 호출부가 전부 그대로 산다).
    g.test_camera_calls = camera_calls
    bb = py_trees.blackboard.Client(name="test")
    for k in (Keys.FRONT_PERSON_SIZE, Keys.MOVING_STRAIGHT, Keys.COMMITTED_NODE,
              Keys.COMMITTED_IS_DESTINATION, Keys.ACTIVE_COMMAND):
        bb.register_key(key=k, access=py_trees.common.Access.WRITE)
    bb.set(Keys.FRONT_PERSON_SIZE, None)
    bb.set(Keys.MOVING_STRAIGHT, True)
    bb.set(Keys.COMMITTED_NODE, 9)
    bb.set(Keys.COMMITTED_IS_DESTINATION, over.get("is_destination", False))
    # Task 17 — 이 leaf 는 navigate 중에만 돈다. 기존 시험은 전부 그 경로를 보므로
    # 기본값을 "navigate" 로 둔다(게이트 자체를 보는 시험만 override 한다).
    bb.set(Keys.ACTIVE_COMMAND, over.get("active_command", "navigate"))
    # guard 가 쓰는 키 — 시험은 읽기만 한다.
    bb.register_key(key=Keys.PERSON_BLOCKED, access=py_trees.common.Access.READ)
    return g, bb, clock, stop_driver, calls


def test_returns_running_when_nothing_is_seen():
    g, _bb, _c, _d, _calls = _guard()
    assert g.update() == Status.RUNNING


def test_never_returns_success_even_when_blocking():
    g, bb, clock, _d, _calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    for t in (0.0, 5.0, 10.0, 15.0):
        clock.t = t
        assert g.update() == Status.RUNNING


def test_halt_sends_stop_once():
    g, bb, clock, driver, _calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 1.0
    g.update()
    assert driver.starts == 1


def test_block_reports_the_committed_node():
    g, bb, clock, _d, calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    assert calls == [9]


def test_block_without_a_committed_node_is_not_reported():
    g, bb, clock, _d, calls = _guard()
    bb.set(Keys.COMMITTED_NODE, None)
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    assert calls == []


def test_missing_size_key_does_not_raise():
    g, bb, _c, _d, _calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, None)
    assert g.update() == Status.RUNNING


def test_initialise_rearms_the_policy():
    g, bb, clock, _d, calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    g.initialise()
    clock.t = 20.0
    bb.set(Keys.MOVING_STRAIGHT, True)
    g.update()
    clock.t = 30.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    assert calls == [9, 9]


def test_destination_node_is_not_blocked():
    """R8-2 (Story 13) — 커밋 노드가 이번 다리의 목적지면 차단을 보고하지 않는다."""
    g, bb, clock, _d, calls = _guard(is_destination=True)
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    assert calls == []


def test_halt_pauses_the_nav_arrive_timer():
    """정지가 곧 nav 도착 시계 일시정지다 — 서 있는 시간이 arrive_timeout 을 먹으면
    안 된다(task-7b). 다시 직진하면 시계도 다시 돈다."""
    clock = Clock()
    policy = PersonBlockPolicy(stop_size=209.0, sustain_sec=10.0, resume_grace_sec=1.0)

    class FakeNavLeaf:
        def __init__(self):
            self.paused = []

        def pause_arrive_timer(self, paused):
            self.paused.append(paused)

    nav_leaf = FakeNavLeaf()
    g = PersonBlockGuard(policy, nav_leaf=nav_leaf, now_fn=clock)
    g.setup()
    bb = py_trees.blackboard.Client(name="test-navleaf")
    for k in (Keys.FRONT_PERSON_SIZE, Keys.MOVING_STRAIGHT, Keys.COMMITTED_NODE,
              Keys.COMMITTED_IS_DESTINATION, Keys.ACTIVE_COMMAND):
        bb.register_key(key=k, access=py_trees.common.Access.WRITE)
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    bb.set(Keys.MOVING_STRAIGHT, True)
    bb.set(Keys.COMMITTED_NODE, 9)
    bb.set(Keys.COMMITTED_IS_DESTINATION, False)
    bb.set(Keys.ACTIVE_COMMAND, "navigate")

    g.update()
    assert nav_leaf.paused == [True]

    # 비켰다 — resume_grace_sec(1.0)을 확실히 넘겨야 정책이 DRIVE 로 돌아온다.
    clock.t = 2.0
    bb.set(Keys.FRONT_PERSON_SIZE, None)
    g.update()
    clock.t = 3.5
    g.update()
    assert nav_leaf.paused == [True, False]


# ── Task 17 — navigate 중에만 돈다 ──────────────────────────────────────────

def test_guard_does_nothing_unless_navigating():
    """`guide`/`follow_admin`/그 밖의 명령 중에는 크기가 아무리 커도 손을 안 댄다.

    그 모드들은 각자 정지·회복 로직이 이미 있다(GuideExec, 추종 회복 BT) — 여기서
    끼어들면 두 주체가 같은 `/cmd_vel`·같은 `stop_driver` 를 다툰다.
    """
    g, bb, clock, driver, calls = _guard(active_command="guide")
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    for t in (0.0, 5.0, 10.0, 15.0):
        clock.t = t
        assert g.update() == Status.RUNNING
    assert driver.starts == 0, "guide 중에 정지 드라이버를 부르면 안 된다"
    assert calls == [], "guide 중에 차단을 보고하면 안 된다"
    assert g.test_camera_calls == [], "guide 중에 카메라를 요청하면 안 된다"


def test_camera_is_requested_while_navigating():
    g, _bb, _clock, _d, _calls = _guard()
    g.update()
    assert g.test_camera_calls == ["front"]


def test_camera_request_is_rate_limited():
    """매 tick(5Hz) 그대로 내보내면 토픽이 찬다 — `_CAMERA_RENEW_SEC` 안에서는 참는다."""
    g, _bb, clock, _d, _calls = _guard()
    g.update()
    clock.t = 0.5                      # _CAMERA_RENEW_SEC(1.0) 안 — 아직 안 나간다
    g.update()
    assert g.test_camera_calls == ["front"]
    clock.t = 1.1                      # 넘겼다 — 다시 나간다
    g.update()
    assert g.test_camera_calls == ["front", "front"]


def test_camera_request_stops_on_terminate():
    g, _bb, _clock, _d, _calls = _guard()
    g.update()
    assert g.test_camera_calls == ["front"]
    g.terminate(Status.INVALID)
    assert g._camera_requested_at is None, "종료 뒤에는 재발행 기준 시각도 비워야 한다"


def test_policy_resets_when_the_command_changes():
    """navigate → guide → navigate 로 오가면 누적된 지속시간이 리셋돼야 한다.

    안 그러면 이전 배달에서 쌓인 blocked_since 를 다음 배달이 그대로 이어받아,
    새 배달을 시작하자마자 (실제로는 막 sustain_sec 를 지난 적이 없는데도) BLOCK 으로
    오판할 수 있다.
    """
    g, bb, clock, _d, calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    clock.t = 0.0
    g.update()                          # blocked_since = 0.0
    clock.t = 5.0
    g.update()                          # 아직 sustain_sec(10) 전 — HALT 뿐
    assert calls == []

    bb.set(Keys.ACTIVE_COMMAND, "guide")
    g.update()                          # 게이트 밖 — policy.reset() 이 불려야 한다

    bb.set(Keys.ACTIVE_COMMAND, "navigate")
    clock.t = 10.0                       # 리셋 안 됐으면 10.0 - 0.0 >= 10 이라 즉시 BLOCK
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    assert calls == [], "명령이 바뀌었으면 지속시간이 처음부터 다시 세져야 한다"


# ── 패널이 "왜 섰나" 를 아는 통로 (Keys.PERSON_BLOCKED → fsm_state.person_blocked) ──

def test_person_blocked_is_false_before_anything_happens():
    g, bb, _c, _d, _calls = _guard()
    assert bb.get(Keys.PERSON_BLOCKED) is False


def test_person_blocked_turns_on_while_halted():
    g, bb, _c, _d, _calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    assert bb.get(Keys.PERSON_BLOCKED) is True


def test_person_blocked_turns_off_when_the_way_clears():
    g, bb, clock, _d, _calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    assert bb.get(Keys.PERSON_BLOCKED) is True, "먼저 켜져야 '꺼진다'가 의미를 가진다"
    # 비켰다 — 유예(resume_grace_sec)를 넘긴 **다음** tick 에야 정지가 풀린다
    # (`test_halt_pauses_the_nav_arrive_timer` 와 같은 두 tick 규칙).
    bb.set(Keys.FRONT_PERSON_SIZE, None)
    clock.t = 2.0
    g.update()
    clock.t = 3.5
    g.update()
    assert bb.get(Keys.PERSON_BLOCKED) is False


def test_person_blocked_is_off_when_not_navigating():
    """길잡이·추종 중에는 이 leaf 가 판정하지 않는다 — 배지도 꺼져 있어야 한다."""
    g, bb, _c, _d, _calls = _guard(active_command="guide")
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    assert bb.get(Keys.PERSON_BLOCKED) is False


def test_terminate_clears_person_blocked():
    """안 끄면 브랜치를 떠난 뒤에도 패널에 빨간 배지가 영원히 남는다."""
    g, bb, _c, _d, _calls = _guard()
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    assert bb.get(Keys.PERSON_BLOCKED) is True
    g.terminate(Status.INVALID)
    assert bb.get(Keys.PERSON_BLOCKED) is False


# ── 재탐색 사유를 화면 기록에 남기기 (2026-08-03, 사용자 요구) ────────────────
# 관제 기록에서 "이 재계획이 사람 때문인가 지연 때문인가" 를 가리려면, 로봇이 실제로
# 알린 순간이 기록에 남아야 한다. 레벨(PERSON_BLOCKED)로 남기면 임계 근처 깜빡임이
# 그대로 로그가 되므로 **알린 횟수**로 낸다.

def _seq(bb):
    return bb.get(Keys.PERSON_BLOCK_SEQ)


def test_block_seq_starts_at_zero():
    g, bb, _c, _d, _calls = _guard()
    bb.register_key(key=Keys.PERSON_BLOCK_SEQ, access=py_trees.common.Access.READ)
    bb.register_key(key=Keys.PERSON_BLOCK_NODE, access=py_trees.common.Access.READ)
    assert _seq(bb) == 0
    assert bb.get(Keys.PERSON_BLOCK_NODE) is None


def test_block_seq_increments_only_when_actually_reported():
    g, bb, clock, _d, calls = _guard()
    bb.register_key(key=Keys.PERSON_BLOCK_SEQ, access=py_trees.common.Access.READ)
    bb.register_key(key=Keys.PERSON_BLOCK_NODE, access=py_trees.common.Access.READ)
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()                       # HALT — 아직 안 알렸다
    assert _seq(bb) == 0
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()                       # BLOCK — 알렸다
    assert calls == [9]
    assert _seq(bb) == 1
    assert bb.get(Keys.PERSON_BLOCK_NODE) == 9


def test_block_seq_does_not_move_when_the_report_is_suppressed():
    """목적지라 보고를 접었는데 화면에 '재탐색 요청' 이라고 쓰면 거짓말이 된다."""
    g, bb, clock, _d, calls = _guard(is_destination=True)
    bb.register_key(key=Keys.PERSON_BLOCK_SEQ, access=py_trees.common.Access.READ)
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    assert calls == []
    assert _seq(bb) == 0


def test_block_seq_survives_reentering_the_branch():
    """`initialise()` 로 0 으로 되돌리면 화면이 '늘었다' 를 못 알아본다."""
    g, bb, clock, _d, _calls = _guard()
    bb.register_key(key=Keys.PERSON_BLOCK_SEQ, access=py_trees.common.Access.READ)
    bb.set(Keys.FRONT_PERSON_SIZE, 300.0)
    g.update()
    clock.t = 10.0
    bb.set(Keys.MOVING_STRAIGHT, False)
    g.update()
    assert _seq(bb) == 1
    g.initialise()
    assert _seq(bb) == 1
