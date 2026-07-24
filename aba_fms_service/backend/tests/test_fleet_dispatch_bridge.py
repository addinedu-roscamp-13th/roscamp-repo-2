

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


# ── 주행을 로봇 BT 로 (R2) ──────────────────────────────────────────────────
#
# fleet_node 가 허가한 다음 노드를 /fleet_cmd{navigate} 로 내려보내면 libi_modes 의
# WorkingBranch ▸ NavigationExec 이 실행한다. 예전엔 로봇 쪽 path_request_driver 가
# PathRequest 를 직접 받아 nav2 로 넣어 **BT 를 우회**했고, 그래서 FSM 이 WORKING 으로
# 가지 않아 관제가 배달 중인 로봇을 "배차 가능"으로 표시했다.

def _reset_nav():
    with bridge._nav_lock:
        bridge._last_nav.clear()


def test_path_request_goes_to_bt_as_navigate(monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append((robot, action, args)) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()

    bridge.on_path_request("Pinky-3", [(0.519, -1.356, 1.57)])
    assert sent == [("Pinky-3", "navigate", {"x": 0.519, "y": -1.356, "yaw": 1.57})]


def test_repeated_same_destination_is_ignored(monkeypatch):
    """fleet_node 는 이동 중 같은 경로를 ~1초마다 재발행한다(놓친 명령 자가 복구).

    그대로 흘려보내면 nav2 목표가 매초 선점돼 주행이 끊긴다.
    """
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append(args) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()

    for _ in range(5):
        bridge.on_path_request("Pinky-3", [(0.5, -1.3, 0.0)])
    assert len(sent) == 1, "같은 목적지는 한 번만 나간다"


def test_stalled_robot_is_re_driven_after_the_resend_window():
    """같은 목적지라도 오래 지나면 한 번 더 보낸다 — 멈춘 로봇을 다시 출발시킨다.

    nav2 주행은 로봇이 도착하지 않은 채 끝날 수 있다(ABORTED, 또는 선점 순간 직전
    목표의 완료가 새 목표의 완료로 보고되는 경우). BT 의 NavigationExec 은 명령이
    **접수**되면 끝나므로 그걸 알지 못하고, fleet_node 의 재발행은 위 걸러내기에
    막힌다. 그래서 아무도 다시 몰지 않는다 — 실측으로 6분 40초를 서 있었다.
    """
    _reset_nav()
    key = (0.5, -1.3)
    assert bridge.should_send_nav("Pinky-3", key, 100.0) is True
    assert bridge.should_send_nav("Pinky-3", key, 100.0 + bridge.NAV_RESEND_SEC - 0.1) is False
    assert bridge.should_send_nav("Pinky-3", key, 100.0 + bridge.NAV_RESEND_SEC) is True


def test_resend_window_restarts_on_each_send():
    """재전송한 시각이 새 기준이 된다 — 안 그러면 그 뒤로 매번 통과한다."""
    _reset_nav()
    key = (0.5, -1.3)
    w = bridge.NAV_RESEND_SEC
    assert bridge.should_send_nav("Pinky-3", key, 0.0) is True
    assert bridge.should_send_nav("Pinky-3", key, w) is True          # 재전송
    assert bridge.should_send_nav("Pinky-3", key, w + 0.1) is False   # 창이 다시 시작


def test_new_destination_is_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append(args) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()

    bridge.on_path_request("Pinky-3", [(0.5, -1.3, 0.0)])
    bridge.on_path_request("Pinky-3", [(0.6, -1.4, 0.0)])      # 다음 노드
    assert len(sent) == 2


def test_task_end_clears_the_destination_memory(monkeypatch):
    """다음 주문이 같은 노드에서 시작할 때 걸러지면 로봇이 출발하지 않는다."""
    sent = []
    # real_lifecycle 도 같은 함수로 task_done 을 보내므로 navigate 만 센다.
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append(action) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()

    bridge.on_path_request("Pinky-3", [(0.5, -1.3, 0.0)])
    bridge.real_lifecycle("Pinky-3", "done")
    bridge.on_path_request("Pinky-3", [(0.5, -1.3, 0.0)])      # 같은 곳이지만 새 주문
    assert sent.count("navigate") == 2, "주문이 끝났으면 같은 목적지도 다시 나가야 한다"
    assert "task_done" in sent


def test_robots_do_not_share_the_destination_memory(monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append(robot) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()

    bridge.on_path_request("Pinky-3", [(0.5, -1.3, 0.0)])
    bridge.on_path_request("Pinkysim", [(0.5, -1.3, 0.0)])     # 같은 목적지, 다른 로봇
    assert sent == ["Pinky-3", "Pinkysim"]


def test_disabled_flag_falls_back_to_old_path(monkeypatch):
    """LIBI_NAV_VIA_BT=0 이면 예전 경로(path_request_driver)로 되돌린다."""
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append(args) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", False)
    _reset_nav()

    bridge.on_path_request("Pinky-3", [(0.5, -1.3, 0.0)])
    assert sent == []


def test_empty_points_are_ignored(monkeypatch):
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()
    bridge.on_path_request("Pinky-3", [])        # 예외가 나면 구독 스레드가 죽는다
