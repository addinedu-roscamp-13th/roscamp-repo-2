

"""fleet_dispatch_bridge — orchestrator 와 fleet_node 사이 배선의 순수 로직.

ROS 없이 도는 부분만 여기서 본다(고아 task 판정). 실제 dispatch/release 는 fleet_node 가
필요해 sim 으로 검증한다.
"""
from __future__ import annotations

# ── 고아 task 화해 ───────────────────────────────────────────────────────────
#
# orchestrator 의 주문은 메모리에만 있다. 백엔드를 재기동하면 주문은 전부 사라지는데
# fleet_node 는 살아 있어 `orchestrator:t10` 을 계속 붙잡는다 → 그 로봇은 busy 로 고정돼
# 영원히 배차를 못 받고, 패널엔 "IDLE 1대 / 배차 가능 0대" 라는 말이 안 되는 화면이 뜬다.

from app.fleet_dispatch_bridge import TASK_PREFIX, find_orphans


def test_orphan_is_task_fleet_holds_but_orchestrator_forgot():
    robots = [{"name": "Pinkysim", "busy": True, "task_id": f"{TASK_PREFIX}t10"}]
    assert find_orphans(robots, live_ids=set()) == [("Pinkysim", f"{TASK_PREFIX}t10")]


def test_live_task_is_not_orphan():
    robots = [{"name": "Pinkysim", "busy": True, "task_id": f"{TASK_PREFIX}t10"}]
    assert find_orphans(robots, live_ids={"t10"}) == []


def test_patrol_task_is_never_touched():
    """fleet_node 자체 순회를 우리가 지우면 안 된다 — 우리 일이 아니다."""
    robots = [{"name": "Pinkysim", "busy": True, "task_id": "P-Pinkysim"}]
    assert find_orphans(robots, live_ids=set()) == []


def test_foreign_task_is_never_touched():
    """다른 주체(콘솔 수동 배차 등)가 낸 일도 건드리지 않는다."""
    robots = [{"name": "Pinkysim", "busy": True, "task_id": "T-7"}]
    assert find_orphans(robots, live_ids=set()) == []


def test_idle_robot_is_not_orphan():
    robots = [{"name": "Pinkysim", "busy": False, "task_id": ""}]
    assert find_orphans(robots, live_ids=set()) == []


# ── 팔 다리는 로봇 BT 로 간다 ────────────────────────────────────────────────
#
# 예전엔 파이썬 타이머로 "성공했다 치기" 를 해서 로봇 BT(WorkingBranch/ArmExec)가 아예
# 안 돌았다. 그러면 FSM 이 WORKING 으로 전이되지 않아 관제 상태·배차 판정이 실제와
# 어긋나고, BT 의 CommandTimeout·FaultDetected 방어도 걸리지 않는다.

import app.fleet_dispatch_bridge as bridge
from app.fleet_orchestrator import Leg, LegType


def _arm_leg():
    return Leg(LegType.PERFORM_ACTION, {"action": "pick", "book": "B1", "at": "문학-1"})


def test_arm_leg_goes_to_robot_bt(monkeypatch):
    sent = {}

    def fake_send(robot, action, args=None):
        sent.update(robot=robot, action=action, args=args)
        return "cmd-123"

    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async", fake_send)
    monkeypatch.setattr(bridge, "ARM_VIA_BT", True)

    cmd_id = bridge.real_dispatch("t1", "Pinky-3", _arm_leg())
    assert cmd_id == "cmd-123", "fleet_cmd 가 준 id 를 그대로 cmd_id 로 쓴다"
    assert sent["robot"] == "Pinky-3"
    assert sent["action"] == "perform_action"
    assert sent["args"]["action"] == "pick"


def test_arm_leg_falls_back_to_stub_without_link(monkeypatch):
    """브릿지 미기동·로봇 오프라인이면 조용히 멈추지 말고 스텁으로 내려간다."""
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: None)
    monkeypatch.setattr(bridge, "ARM_VIA_BT", True)
    monkeypatch.setattr(bridge, "ARM_AUTO", False)

    cmd_id = bridge.real_dispatch("t1", "Pinky-3", _arm_leg())
    assert cmd_id.startswith("arm-"), "스텁 id 로 떨어져야 한다"


def test_cmd_result_completes_the_leg(monkeypatch):
    """/fleet_cmd_result 가 오면 그 id 로 다리를 완료 처리한다."""
    calls = []
    monkeypatch.setattr(bridge, "_orc",
                        lambda: type("O", (), {"on_result": lambda s, i, ok, m="": calls.append((i, ok))})())
    bridge.on_cmd_result({"id": "cmd-123", "ok": True, "status": 200})
    assert calls == [("cmd-123", True)]


def test_cmd_result_without_id_is_ignored():
    bridge.on_cmd_result({"ok": True})       # 예외가 나면 구독 스레드가 죽는다
