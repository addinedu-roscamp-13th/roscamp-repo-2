"""야간 침입 추종 잎 — 기존 드라이버를 순서대로 부르는 배선만 검증한다.

추종 알고리즘 자체는 libi_perception 것이고 여기서 시험하지 않는다. 여기서 시험하는
것은 **누가 언제 어떤 순서로 불리는가**다.
"""
import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.intruder_chase import ChasePolicy, IntruderChase
from test.fakes import FakeDriver

POLICY = ChasePolicy(trigger_size=100.0, sustain_sec=1.5, lose_sec=5.0,
                     max_chase_sec=60.0, release_grace_sec=1.0,
                     failure_backoff_sec=10.0)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _leaf(follow=None, nav_stop=None, clock=None):
    clock = clock or Clock()
    leaf = IntruderChase(POLICY, follow_driver=follow or FakeDriver(),
                         nav_stop_driver=nav_stop or FakeDriver(), now_fn=clock)
    leaf.setup()
    return leaf, clock


def _size(v):
    bb = py_trees.blackboard.Client(name="test")
    bb.register_key(key=Keys.FRONT_PERSON_SIZE, access=py_trees.common.Access.WRITE)
    bb.set(Keys.FRONT_PERSON_SIZE, v)


def test_사람이_없으면_실패를_돌려_순찰이_돈다():
    leaf, _ = _leaf()
    _size(0.0)
    assert (leaf.tick_once() or leaf.status) is Status.FAILURE


def test_지속시간_미만이면_아직_시작하지_않는다():
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0)
    leaf.tick_once()
    clock.t += 1.4
    assert (leaf.tick_once() or leaf.status) is Status.FAILURE
    assert follow.start_count == 0


def test_트리거하면_주행취소_다음_추종_순서로_불린다():
    """nav2 가 /cmd_vel 을 놓기 전에 추종이 시작되면 둘이 싸운다."""
    order = []
    follow, nav_stop = FakeDriver(), FakeDriver()
    follow.start = lambda: order.append("follow")
    nav_stop.start = lambda: order.append("nav_stop")

    leaf, clock = _leaf(follow=follow, nav_stop=nav_stop)
    _size(140.0)
    leaf.tick_once()
    clock.t += 1.6
    assert (leaf.tick_once() or leaf.status) is Status.RUNNING
    assert order == ["nav_stop", "follow"]


def test_상실_후_lose_sec_이_지나면_세션을_닫는다():
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0)
    clock.t += 4.0; leaf.tick_once()
    assert follow.stop_count == 0
    clock.t += 1.5; leaf.tick_once()
    assert follow.stop_count == 1


def test_stop_을_낸_그_틱에는_아직_RUNNING_이다():
    """follow_stop 은 결과를 안 기다린다. 즉시 순찰로 넘기면 /cmd_vel 발행자가 둘이 된다."""
    leaf, clock = _leaf()
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0); clock.t += 5.5
    assert (leaf.tick_once() or leaf.status) is Status.RUNNING          # RELEASE — 아직 안 넘긴다
    clock.t += 1.1
    assert (leaf.tick_once() or leaf.status) is Status.FAILURE          # 유예 뒤에야 순찰


def test_max_chase_sec_상한이_걸린다():
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    for _ in range(70):
        clock.t += 1.0
        leaf.tick_once()
    assert follow.stop_count >= 1


def test_정상_소실_뒤에는_곧바로_다시_쫓는다():
    """로봇 쿨다운은 일부러 없다 — 다시 보이면 다시 쫓는 게 맞다."""
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0); clock.t += 5.5; leaf.tick_once()
    clock.t += 1.1; leaf.tick_once()
    _size(140.0); leaf.tick_once(); clock.t += 1.6
    assert (leaf.tick_once() or leaf.status) is Status.RUNNING
    assert follow.start_count == 2


def test_FAILURE_를_돌려주는_동안_지속시간이_쌓인다():
    """⚠️ py_trees 는 상태가 RUNNING 이 아니면 **매 tick initialise() 를 다시 부른다.**
    거기서 `_seen_since` 를 지우면 1.5초가 영영 안 쌓여 추종이 시작되지 않는다.
    """
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0)
    # 첫 틱이 무장(_seen_since=0)만 하고 FAILURE 를 돌려주므로, 문턱(1.5초)을 넘기려면
    # 그 뒤로 0.2초씩 8번(=1.6초) 더 지나야 한다 — 총 9틱. 매 tick FAILURE 다가 마지막에 넘는다.
    for _ in range(9):
        leaf.tick_once()
        clock.t += 0.2
    assert follow.start_count == 1


def test_세션이_failure_로_끝나면_백오프가_걸린다():
    """owner 가 없으면 트리거→실패→재트리거가 무한 반복해 순찰이 마비된다."""
    follow = FakeDriver(poll_sequence=("failure",))
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6
    leaf.tick_once()                       # 시작
    leaf.tick_once()                       # poll -> failure -> RELEASE
    clock.t += 1.1; leaf.tick_once()       # 유예 지나 FAILURE

    # ⚠️ 여기가 핵심 — **여러 tick 을 돌리며** 지속시간을 다시 채워도 재시작하면 안 된다.
    #    terminate(FAILURE) 가 `_state` 를 지우면 이 단언이 깨진다.
    for _ in range(40):                    # 8초분(0.2초 × 40) — 백오프 10초 안
        leaf.tick_once()
        clock.t += 0.2
    assert follow.start_count == 1, "백오프 중인데 재시작했다 — 순찰이 마비된다"

    clock.t += 3.0                         # 백오프(10초) 경과
    for _ in range(10):
        leaf.tick_once()
        clock.t += 0.2
    assert follow.start_count == 2         # 백오프가 지나면 다시 시작


def test_브랜치_이탈_뒤_다시_들어오면_백오프가_풀린다():
    """관제 정지 → 야간 재진입은 새 상황이다. 옛 백오프를 물고 오면 안 된다."""
    follow = FakeDriver(poll_sequence=("failure",))
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6
    leaf.tick_once(); leaf.tick_once()
    clock.t += 1.1; leaf.tick_once()
    leaf.stop(Status.INVALID)              # 브랜치 이탈
    for _ in range(10):
        leaf.tick_once()
        clock.t += 0.2
    assert follow.start_count == 2


def test_트리에서_빠질_때_세션을_닫는다():
    """관제 「정지」·배터리 저하로 끌려가도 follow_stop 이 나가야 한다.

    이걸 안 하면 libi_perception 의 제어 루프가 20Hz 로 /cmd_vel 을 계속 민다
    (working_actions.py `_abandon` 의 2026-07-28 실측 사고와 같은 자리).
    """
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    leaf.stop(Status.INVALID)
    assert follow.stop_count == 1
