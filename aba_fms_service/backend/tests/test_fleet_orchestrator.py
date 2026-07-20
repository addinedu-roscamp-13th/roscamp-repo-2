"""fleet_orchestrator 코어 단위테스트 — ROS·DB·로봇 없이 시퀀스 로직 전부 검증.

dispatch_leg 를 페이크로 주입한다: 호출을 기록하고 증가하는 cmd_id 를 돌려준다.
이걸로 "주행→팔→주행→팔 순서로 하나씩", "완료 보고에 다음 다리", 실패/취소/재시도,
강제전진, 늦은 결과 무시를 확인한다."""
import pytest

from app.fleet_orchestrator import (
    Leg,
    LegType,
    Orchestrator,
    TaskStatus,
    decompose_delivery,
)


class FakeDispatcher:
    """다리를 실제로 내보내는 대신 기록. cmd_id 는 c1, c2, … 로 증가.

    fail_ids 에 넣은 다리 순번(1부터)은 dispatch 시 예외(내보내기 자체 실패)."""

    def __init__(self, raise_on=()):
        self.calls = []            # (task_id, robot, leg_type, params)
        self._n = 0
        self._raise_on = set(raise_on)

    def __call__(self, task_id, robot, leg):
        self._n += 1
        self.calls.append((task_id, robot, leg.type, dict(leg.params)))
        if self._n in self._raise_on:
            raise RuntimeError("boom")
        return f"c{self._n}"

    @property
    def leg_types(self):
        return [t for (_, _, t, _) in self.calls]


# ── 분해 ─────────────────────────────────────────────────────────────────────

def test_delivery_decomposes_into_four_legs():
    legs = decompose_delivery(book="B1", pickup=7, dropoff=3)
    assert [l.type for l in legs] == [
        LegType.NAVIGATE, LegType.PERFORM_ACTION, LegType.NAVIGATE, LegType.PERFORM_ACTION,
    ]
    assert legs[0].params == {"waypoint": 7}
    assert legs[1].params == {"action": "pick", "book": "B1", "at": 7}
    assert legs[3].params == {"action": "place", "book": "B1", "at": 3}


# ── 접수 / 큐 ─────────────────────────────────────────────────────────────────

def test_submit_delivery_queues_pending():
    orc = Orchestrator(FakeDispatcher())
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3, requester="사서")
    task = orc.get(tid)
    assert task.status == TaskStatus.PENDING
    assert len(task.legs) == 4
    assert [t["id"] for t in orc.pending()] == [tid]


def test_submit_empty_legs_rejected():
    orc = Orchestrator(FakeDispatcher())
    with pytest.raises(ValueError):
        orc.submit("delivery", [])


# ── 정상 경로 ─────────────────────────────────────────────────────────────────

def test_assign_starts_first_leg():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    task = orc.get(tid)
    assert task.status == TaskStatus.EXECUTING
    assert task.robot == "pinky3"
    assert d.leg_types == [LegType.NAVIGATE]          # 첫 다리만 나감
    assert task.id not in [t["id"] for t in orc.pending()]  # 큐에서 빠짐


def test_happy_path_sequences_all_four_legs_in_order():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")

    # 한 번에 하나씩 — 완료 보고해야 다음이 나간다.
    orc.on_result("c1", ok=True)      # navigate(pickup) 완료 → pick 나감
    orc.on_result("c2", ok=True)      # pick 완료 → navigate(dropoff)
    orc.on_result("c3", ok=True)      # navigate 완료 → place
    assert orc.get(tid).status == TaskStatus.EXECUTING
    orc.on_result("c4", ok=True)      # place 완료 → COMPLETED

    assert orc.get(tid).status == TaskStatus.COMPLETED
    assert d.leg_types == [
        LegType.NAVIGATE, LegType.PERFORM_ACTION, LegType.NAVIGATE, LegType.PERFORM_ACTION,
    ]


def test_next_leg_not_dispatched_before_current_completes():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    # 완료 보고 없이 두 번째 다리가 나가면 안 된다.
    assert len(d.calls) == 1


# ── 실패 / 재시도 ─────────────────────────────────────────────────────────────

def test_leg_failure_retries_same_leg_then_fails():
    d = FakeDispatcher()
    orc = Orchestrator(d, retry_max=1)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")                 # attempt 1: c1 (navigate)

    orc.on_result("c1", ok=False)             # 실패 → 재시도(attempt 2): c2, 같은 navigate
    assert orc.get(tid).status == TaskStatus.EXECUTING
    assert d.leg_types == [LegType.NAVIGATE, LegType.NAVIGATE]

    orc.on_result("c2", ok=False)             # 재시도도 실패 → retry_max 초과 → FAILED
    task = orc.get(tid)
    assert task.status == TaskStatus.FAILED
    assert task.reason


def test_dispatch_exception_is_treated_as_leg_failure():
    d = FakeDispatcher(raise_on=(1,))          # 첫 dispatch 자체가 예외
    orc = Orchestrator(d, retry_max=0)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    assert orc.get(tid).status == TaskStatus.FAILED


def test_retry_max_zero_fails_on_first_failure():
    d = FakeDispatcher()
    orc = Orchestrator(d, retry_max=0)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    orc.on_result("c1", ok=False)
    assert orc.get(tid).status == TaskStatus.FAILED


# ── 취소 ─────────────────────────────────────────────────────────────────────

def test_cancel_pending_task():
    orc = Orchestrator(FakeDispatcher())
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.cancel(tid)
    assert orc.get(tid).status == TaskStatus.CANCELLED
    assert orc.pending() == []


def test_cancel_executing_ignores_late_result():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    orc.cancel(tid)
    # 로봇이 뒤늦게 c1 완료를 보고해도 진행되면 안 된다.
    orc.on_result("c1", ok=True)
    task = orc.get(tid)
    assert task.status == TaskStatus.CANCELLED
    assert len(d.calls) == 1                   # 다음 다리 안 나감


def test_cancel_terminal_is_noop():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    for c in ("c1", "c2", "c3", "c4"):
        orc.on_result(c, ok=True)
    assert orc.get(tid).status == TaskStatus.COMPLETED
    orc.cancel(tid)                            # 완료된 걸 취소 시도 → 무시
    assert orc.get(tid).status == TaskStatus.COMPLETED


# ── 강제전진 (디버그) ─────────────────────────────────────────────────────────

def test_force_advance_moves_to_next_leg():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")                  # navigate 진행 중(c1)
    orc.force_advance(tid)                      # 로봇 없이 완료 친다 → pick 나감
    assert d.leg_types == [LegType.NAVIGATE, LegType.PERFORM_ACTION]
    assert orc.get(tid).status == TaskStatus.EXECUTING


def test_force_advance_through_all_legs_completes():
    orc = Orchestrator(FakeDispatcher())
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    for _ in range(4):
        if orc.get(tid).status == TaskStatus.EXECUTING:
            orc.force_advance(tid)
    assert orc.get(tid).status == TaskStatus.COMPLETED


def test_force_advance_ignores_the_forced_legs_late_result():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    orc.force_advance(tid)                      # c1(navigate) 강제완료 → pick(c2)
    orc.on_result("c1", ok=True)               # 늦게 온 c1 은 무시돼야
    assert orc.get(tid).status == TaskStatus.EXECUTING
    assert d.leg_types == [LegType.NAVIGATE, LegType.PERFORM_ACTION]  # c2 이후 안 밀림


def test_force_advance_requires_executing():
    orc = Orchestrator(FakeDispatcher())
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)   # PENDING
    with pytest.raises(ValueError):
        orc.force_advance(tid)


# ── 잘못된 입력 / stale ──────────────────────────────────────────────────────

def test_unknown_cmd_result_ignored():
    orc = Orchestrator(FakeDispatcher())
    orc.on_result("nope", ok=True)             # 예외 없이 조용히 무시
    orc.on_result("nope", ok=False)


def test_assign_non_pending_rejected():
    orc = Orchestrator(FakeDispatcher())
    tid = orc.submit_delivery(book="B1", pickup=7, dropoff=3)
    orc.assign(tid, "pinky3")
    with pytest.raises(ValueError):
        orc.assign(tid, "pinky1")              # 이미 EXECUTING


def test_assign_unknown_task_raises():
    orc = Orchestrator(FakeDispatcher())
    with pytest.raises(KeyError):
        orc.assign("nope", "pinky3")


# ── 다중 task 독립 ────────────────────────────────────────────────────────────

def test_multiple_tasks_tracked_independently():
    d = FakeDispatcher()
    orc = Orchestrator(d)
    a = orc.submit_delivery(book="A", pickup=1, dropoff=2)
    b = orc.submit_delivery(book="B", pickup=3, dropoff=4)
    orc.assign(a, "pinky1")     # c1
    orc.assign(b, "pinky2")     # c2
    orc.on_result("c1", ok=True)   # a 의 navigate 완료 → a 의 pick(c3)
    assert orc.get(a).status == TaskStatus.EXECUTING
    assert orc.get(b).status == TaskStatus.EXECUTING
    # b 는 c1 보고에 영향 안 받음
    assert orc.get(b).leg_idx == 0
