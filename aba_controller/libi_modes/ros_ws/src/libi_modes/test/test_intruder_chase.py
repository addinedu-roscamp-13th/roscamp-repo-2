"""야간 침입 추종 잎 — 기존 드라이버를 순서대로 부르는 배선만 검증한다.

추종 알고리즘 자체는 libi_perception 것이고 여기서 시험하지 않는다. 여기서 시험하는
것은 **누가 언제 어떤 순서로 불리는가**다.
"""
import py_trees
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.common.intruder_chase import ChasePolicy, IntruderChase
from test.fakes import FakeDriver

POLICY = ChasePolicy(trigger_size=100.0, sustain_sec=1.5, max_chase_sec=60.0,
                     release_grace_sec=1.0,
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


def _lost_driver(n_running=0):
    """`n_running` 틱 동안 "running", 그 뒤 "failure" — **회복 탐색이 끝났다**는 신호.

    ⚠️ [2026-08-07] 예전 시험들은 `lose_sec`(5초) 시계를 흘려 세션을 닫았다. 그 시계를
       걷어냈으므로(`ChasePolicy` 머리말) 이제는 인지 쪽 결과로 닫는다.
    """
    return FakeDriver(poll_sequence=["running"] * n_running + ["failure"])


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


def test_추종_시작시_session_kind_security_태그를_싣는다():
    """[2026-08-04 요구사항] 관리자 추종(패널 버튼)과 같은 follow_admin 액션을 쓰지만,
    이 태그가 있어야 follow_node 가 role=security 를 붙여 자세(옆모습) 게이트를
    빼고 bbox 크기만으로 판단한다(session.py 머리말). 태그가 없으면 관리자 추종과
    구분이 안 돼 게이트가 계속 걸린다."""
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    assert follow.last_args == {"session_kind": "security"}


def test_트리거하면_주행취소_다음_추종_순서로_불린다():
    """nav2 가 /cmd_vel 을 놓기 전에 추종이 시작되면 둘이 싸운다."""
    order = []
    follow, nav_stop = FakeDriver(), FakeDriver()
    follow.start = lambda args=None: order.append("follow")
    nav_stop.start = lambda: order.append("nav_stop")

    leaf, clock = _leaf(follow=follow, nav_stop=nav_stop)
    _size(140.0)
    leaf.tick_once()
    clock.t += 1.6
    assert (leaf.tick_once() or leaf.status) is Status.RUNNING
    assert order == ["nav_stop", "follow"]


def test_상실만으로는_세션을_안_닫는다():
    """⚠️ [2026-08-07] 예전엔 `lose_sec`(5초) 시계가 여기서 끊었다. 그러면 인지 쪽
    회복 트리에 **1.6초**밖에 안 남아 탐색이 사실상 안 돌았다(`ChasePolicy` 머리말).

    이제 소실은 인지 쪽이 판정하고, 회복이 다 끝나면 그 결과가 `poll()` 로 온다.
    되돌리면(= 시계를 되살리면) 이 시험이 빨개진다."""
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0)
    clock.t += 30.0; leaf.tick_once()          # 옛 lose_sec 의 6배를 흘려도
    assert follow.stop_count == 0, "아직 회복 탐색이 도는 중이다 — 끊으면 안 된다"


def test_회복이_끝났다는_결과가_오면_세션을_닫는다():
    """정상 종료 경로. `GiveUp → session 'failure' → /fleet_cmd_result` 가 여기로 온다."""
    follow = _lost_driver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0)
    clock.t += 0.2; leaf.tick_once()
    assert follow.stop_count == 1


def test_상한을_넘으면_결과가_안_와도_끊는다():
    """`max_chase_sec` 이 이제 **유일한 시계**다 — 밤에 무한정 도는 것을 막는 마지막 방벽."""
    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0)
    clock.t += POLICY.max_chase_sec + 1.0; leaf.tick_once()
    assert follow.stop_count == 1


def test_stop_을_낸_그_틱에는_아직_RUNNING_이다():
    """follow_stop 은 결과를 안 기다린다. 즉시 순찰로 넘기면 /cmd_vel 발행자가 둘이 된다."""
    leaf, clock = _leaf(follow=_lost_driver())
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0); clock.t += 0.2
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
    # 회복이 **성공** 으로 끝난 경우다 — 백오프가 안 걸려야 곧바로 다시 쫓는다.
    follow = FakeDriver(poll_sequence=["success"])
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    _size(0.0); clock.t += 0.2; leaf.tick_once()
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


def _active_command():
    bb = py_trees.blackboard.Client(name="test-read")
    bb.register_key(key=Keys.ACTIVE_COMMAND, access=py_trees.common.Access.READ)
    return bb.get(Keys.ACTIVE_COMMAND)


def test_추종이_release로_끝나면_active_command를_비운다():
    """실기 재현 2026-08-04: `follow_driver.start()` 가 내는 `/fleet_cmd{follow_admin}`
    을 이 노드 자신이 도로 구독해 `active_command="follow_admin"` 을 찍는다
    (providers.py `_on_cmd`). `CommandDrivenAction._release()` 만 이 값을 지우는데,
    이 잎은 그 클래스가 아니라서 지운 적이 없었다 — 추종이 끝나도 값이 영원히 박제돼
    `PatrolNavigation` 이 "실행 중인 follow_admin 를 선점하지 않는다"로 순찰 재진입을
    영구히 막았다(실측 로그: 21초마다 반복, `max_chase_sec` 지나도 안 풀림)."""
    bb = py_trees.blackboard.Client(name="test-write")
    bb.register_key(key=Keys.ACTIVE_COMMAND, access=py_trees.common.Access.WRITE)
    bb.set(Keys.ACTIVE_COMMAND, "follow_admin")   # /fleet_cmd 자기 메아리를 흉내낸다

    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    assert _active_command() == "follow_admin"    # 추종 중에는 그대로 있어야 한다

    _size(0.0)
    clock.t += POLICY.max_chase_sec + 1.0; leaf.tick_once()   # 상한 → _release()
    assert _active_command() is None


def test_트리에서_빠질_때도_active_command를_비운다():
    """추종 도중 관제 「정지」·배터리 저하로 통째로 끌려가는 경우 — _release() 를
    거치지 않으므로 terminate(INVALID) 에서도 똑같이 비워야 한다."""
    bb = py_trees.blackboard.Client(name="test-write2")
    bb.register_key(key=Keys.ACTIVE_COMMAND, access=py_trees.common.Access.WRITE)
    bb.set(Keys.ACTIVE_COMMAND, "follow_admin")

    follow = FakeDriver()
    leaf, clock = _leaf(follow=follow)
    _size(140.0); leaf.tick_once(); clock.t += 1.6; leaf.tick_once()
    leaf.stop(Status.INVALID)
    assert _active_command() is None
