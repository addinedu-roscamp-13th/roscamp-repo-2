"""도착 알림 — 상태가 아니라 **사건**이 필요한 이유와 그 계약.

## 왜 이게 따로 있어야 하나

관제도 회원 앱도 주문 **상태**를 폴링한다(`/api/fleet/orders`, `/api/member/requests`).
상태만으로는 "방금 도착했다"를 표현할 수 없다 — 책이 자리에 도착한 순간과 도착한 지
10분 지난 순간이 화면에서 똑같이 보인다. 알림을 띄우려면 사건이 있어야 한다.

사건은 상태와 달리 **놓치면 끝이다.** 그래서 보관하고 `since(seq)` 로 되받을 수 있게 한다.
"""
import pytest

from app import fleet_events
from app.fleet_dispatch_bridge import on_orchestrator_event
from app.fleet_orchestrator import Orchestrator, TaskStatus


@pytest.fixture(autouse=True)
def _clean():
    fleet_events.reset()
    yield
    fleet_events.reset()


class _FakeLoop:
    """call_soon_threadsafe 만 흉내낸다 — 발행 즉시 큐에 넣는다."""

    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


class _FakeQueue:
    def __init__(self, maxsize=64):
        self.items = []
        self.maxsize = maxsize

    def full(self):
        return len(self.items) >= self.maxsize

    def put_nowait(self, item):
        self.items.append(item)

    def get_nowait(self):
        return self.items.pop(0)


# ── 사건 보관·조회 ───────────────────────────────────────────────────────────

def test_since_returns_only_what_the_caller_has_not_seen():
    """놓친 것부터 받아 갈 수 있어야 한다 — 화면이 잠깐 닫혀 있어도 알림이 사라지지 않게."""
    fleet_events.publish("task_done", task_id="t1")
    seen = fleet_events.latest_seq()
    fleet_events.publish("task_done", task_id="t2")

    fresh = fleet_events.since(seen)
    assert [e["task_id"] for e in fresh] == ["t2"]


def test_seq_never_goes_backwards():
    seqs = [fleet_events.publish("task_done", task_id=f"t{i}")["seq"] for i in range(5)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5


def test_events_are_capped_so_memory_does_not_grow():
    for i in range(fleet_events.MAX_EVENTS + 50):
        fleet_events.publish("leg_done", task_id=f"t{i}")
    assert len(fleet_events.since(0, limit=10_000)) == fleet_events.MAX_EVENTS


# ── 밀어주기 ─────────────────────────────────────────────────────────────────

def test_listener_gets_pushed():
    q = _FakeQueue()
    fleet_events.add_listener(_FakeLoop(), q)
    try:
        fleet_events.publish("task_done", task_id="t1", text="배달 완료")
    finally:
        fleet_events.remove_listener(_FakeLoop(), q)  # 동등 비교라 새 객체로도 안 지워짐
    assert [e["text"] for e in q.items] == ["배달 완료"]


def test_slow_listener_drops_the_oldest_instead_of_blocking():
    """구독자 하나가 느리다고 발행이 막히면 **배차 전체가 멈춘다** (오케스트레이터 락 안이다).

    그래서 밀린 큐는 오래된 것부터 버린다 — 최신 알림을 지키는 쪽이 맞다.
    """
    q = _FakeQueue(maxsize=2)
    fleet_events.add_listener(_FakeLoop(), q)
    for i in range(5):
        fleet_events.publish("leg_done", task_id=f"t{i}")
    assert len(q.items) == 2
    assert [e["task_id"] for e in q.items] == ["t3", "t4"]


def test_publish_never_raises_even_if_a_listener_explodes():
    class _Boom:
        def call_soon_threadsafe(self, fn, *args):
            raise RuntimeError("루프가 닫혔다")

    fleet_events.add_listener(_Boom(), _FakeQueue())
    fleet_events.publish("task_done", task_id="t1")     # 예외가 새면 배차가 멈춘다
    assert fleet_events.since(0)[-1]["task_id"] == "t1"


# ── orchestrator 사건 → 사람이 읽을 알림 ──────────────────────────────────────

def _delivery_orchestrator():
    """주행→집기→주행→놓기 4다리 주문 하나를 EXECUTING 까지 만든다."""
    sent = []
    orc = Orchestrator(lambda tid, robot, leg: sent.append(leg) or f"cmd-{len(sent)}",
                       on_event=on_orchestrator_event)
    task_id = orc.submit_delivery(book="B1", pickup="복도-5", dropoff="테이블-1번-좌", requester="m1")
    orc.assign(task_id, "Pinkysim")
    return orc, task_id, sent


def test_arrival_at_the_pickup_is_announced():
    """첫 주행 다리가 끝난 순간이 곧 '픽업 지점 도착'이다."""
    orc, task_id, _ = _delivery_orchestrator()
    orc.on_result("cmd-1", True)

    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert "task_started" in kinds
    arrival = [e for e in fleet_events.since(0) if e["kind"] == "leg_done"][0]
    assert arrival["leg_kind"] == "navigate"
    assert "복도-5" in arrival["text"], arrival
    assert arrival["task_id"] == task_id


def test_delivery_completion_is_announced():
    """회원이 기다리는 건 이거다 — 책이 자리에 도착했다.

    ⚠️ 다리 수만큼 결과를 올려야 COMPLETED 가 된다. 2026-08-05 에 서가 복귀(`backup`)
    다리가 들어와 4 → 5 다리가 됐는데 여기 범위가 안 따라와서, 그 뒤로 이 시험은
    **완료를 한 번도 못 보고** 계속 실패하고 있었다(2026-08-07 발견).
    배달 다리 수를 바꾸면 이 범위도 같이 고친다.
    """
    orc, task_id, sent = _delivery_orchestrator()
    for i in range(1, 6):
        orc.on_result(f"cmd-{i}", True)

    done = [e for e in fleet_events.since(0) if e["kind"] == "task_done"]
    assert len(done) == 1, "완료는 한 번만 알린다"
    assert done[0]["text"] == "배달 완료"
    assert done[0]["status"] == TaskStatus.COMPLETED.value
    assert done[0]["requester"] == "m1", "누구에게 보낼 알림인지 알 수 있어야 한다"


def test_failure_carries_the_reason():
    orc, _, _ = _delivery_orchestrator()
    orc.on_result("cmd-1", False, "경로 없음")
    orc.on_result("cmd-2", False, "경로 없음")      # 재시도까지 소진

    failed = [e for e in fleet_events.since(0) if e["kind"] == "task_failed"]
    assert failed and "경로 없음" in failed[0]["text"]


def test_arm_leg_says_what_it_did_not_where():
    """팔 다리는 '어디 도착'이 아니라 '무엇을 했는가'다."""
    orc, _, _ = _delivery_orchestrator()
    orc.on_result("cmd-1", True)      # 주행 완료 → 집기 시작
    orc.on_result("cmd-2", True)      # 집기 완료

    arm = [e for e in fleet_events.since(0)
           if e["kind"] == "leg_done" and e["leg_kind"] == "perform_action"]
    assert arm and arm[0]["text"] == "책 집기 완료", arm


def test_an_event_failure_never_stops_the_order():
    """알림이 주문보다 덜 중요하다 — 여기서 터져도 배달은 끝나야 한다."""
    def boom(kind, task, leg):
        raise RuntimeError("알림 서버 죽음")

    orc = Orchestrator(lambda tid, robot, leg: "cmd-1", on_event=boom)
    task_id = orc.submit_delivery(book="B1", pickup="복도-5", dropoff="테이블-1번-좌")
    orc.assign(task_id, "Pinkysim")
    assert orc.get(task_id).status == TaskStatus.EXECUTING


def test_navigation_only_order_announces_arrival_once():
    """정리·파견처럼 다리가 1개인 주문도 도착을 알린다."""
    orc = Orchestrator(lambda tid, robot, leg: "cmd-1", on_event=on_orchestrator_event)
    task_id = orc.submit_navigation(dropoff="주차장", requester="사서")
    orc.assign(task_id, "Pinkysim")
    orc.on_result("cmd-1", True)

    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert kinds.count("task_done") == 1
    assert kinds.count("leg_done") == 1


def test_leg_summary_survives_a_leg_with_no_params():
    """params 가 비어도 알림 변환에서 터지면 안 된다 — 오케스트레이터 락 안이다."""
    orc = Orchestrator(lambda tid, robot, leg: "cmd-1", on_event=on_orchestrator_event)
    task_id = orc.submit_delivery(book="B", pickup="복도-5", dropoff="테이블-1번-좌")
    orc.assign(task_id, "Pinkysim")
    # 다리 객체를 갈아끼우면 cmd_id 대조가 어긋난다 — params 만 비운다.
    orc._tasks[task_id].legs[0].params = {}                     # noqa: SLF001
    orc.on_result("cmd-1", True)

    arrival = [e for e in fleet_events.since(0) if e["kind"] == "leg_done"][0]
    assert arrival["text"] == "목적지 도착"
