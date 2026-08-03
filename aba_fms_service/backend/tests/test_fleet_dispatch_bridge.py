

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


def test_no_arm_leg_key_is_silently_dropped(monkeypatch):
    """`leg.params` 의 키를 **하나하나 명시적으로 복사**하는 구조라 새 키가 조용히 사라진다.

    실제로 그렇게 잃었다: 예전엔 `action`·`book`·`at` 셋만 복사했고 팔 계약 필드
    (`object`/`from_place`/`to_place`/`tier`/`row`/`slot`)를 orchestrator 에 넣어도 로봇까지
    안 갔다 — 에러도 없었다. orchestrator 에 키를 더하면 이 테스트가 먼저 깨진다.
    """
    from app.fleet_orchestrator import decompose_collection, decompose_delivery

    sent = {}
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.update(args=args) or "cmd-1")
    monkeypatch.setattr(bridge, "ARM_VIA_BT", True)

    legs = decompose_delivery(book="B1", pickup="문학서가", dropoff="1번테이블",
                              tier=2, row=3) + decompose_collection()
    for leg in [l for l in legs if l.type == LegType.PERFORM_ACTION]:
        bridge.real_dispatch("t1", "Pinky-3", leg)
        missing = set(leg.params) - set(sent["args"])
        assert not missing, f"{leg.params['action']} 다리의 키가 사라졌다: {missing}"


def test_numeric_at_is_sent_as_a_vertex_name(monkeypatch):
    """숫자 정점은 이름으로 되돌려 보낸다.

    로봇 쪽 중계가 `at` **이름**에서 장소 종류를 유도한다(`*테이블`→테이블). 숫자를 그대로
    보내면 유도가 실패해 팔 goal 이 아예 안 나가고, 원인은 주행 중에야 드러난다.
    """
    sent = {}
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.update(args=args) or "cmd-1")
    monkeypatch.setattr(bridge, "ARM_VIA_BT", True)
    monkeypatch.setattr(bridge, "_load_vertex_index", lambda: {"1번테이블": 7})

    bridge.real_dispatch("t1", "Pinky-3",
                         Leg(LegType.PERFORM_ACTION,
                             {"action": "place", "book": "B1", "at": 7}))
    assert sent["args"]["at"] == "1번테이블"


def test_unknown_numeric_at_is_left_alone(monkeypatch):
    """navgraph 에 없으면 지어내지 않는다 — 중계가 실패시키는 게 낫다."""
    monkeypatch.setattr(bridge, "_load_vertex_index", lambda: {"1번테이블": 7})
    assert bridge.vertex_name(99) == "99"
    assert bridge.vertex_name("문학서가") == "문학서가"


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


# ── 주문 취소가 로봇까지 닿는가 (2026-07-28) ──────────────────────────────────
# set_robot_mode 는 fleet_node 의 task 만 취소한다. 로봇에게 /fleet_cmd 를 안 보내면
# 이미 내려간 goal 이 살아 있어 **현재 목표까지 간 뒤에야** 멈춘다.

def _release_spy(monkeypatch):
    sent, modes = [], []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append((robot, action)) or "c1")
    monkeypatch.setattr(bridge.fleet_link, "set_robot_mode",
                        lambda robot, mode: modes.append((robot, mode)) or {"ok": True})
    return sent, modes


def test_release_sends_mission_stop_to_the_robot(monkeypatch):
    """이 테스트의 존재 이유 — 예전엔 로봇에 아무것도 안 갔다."""
    sent, modes = _release_spy(monkeypatch)
    bridge.real_release("Pinky-3")
    assert ("Pinky-3", "mission_stop") in sent, "취소했는데 로봇이 계속 간다"
    assert modes == [("Pinky-3", bridge.RELEASE_MODE)], "fleet_node 해제도 그대로 해야 한다"


def test_release_still_frees_fleet_node_when_stop_fails(monkeypatch):
    """정지를 못 보내도 점유 해제는 계속해야 한다 — 둘 다 실패하면 로봇이 영영 묶인다."""
    modes = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("링크 끊김")))
    monkeypatch.setattr(bridge.fleet_link, "set_robot_mode",
                        lambda robot, mode: modes.append((robot, mode)) or {"ok": True})
    bridge.real_release("Pinky-3")
    assert modes == [("Pinky-3", bridge.RELEASE_MODE)]


def test_release_ignores_empty_robot(monkeypatch):
    sent, modes = _release_spy(monkeypatch)
    bridge.real_release("")
    assert sent == [] and modes == []


def test_release_uses_async_send(monkeypatch):
    """동기 전송은 코어 락을 쥔 채 ROS 왕복을 기다려 주문 큐 전체를 멈춘다."""
    called = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: called.append("async") or "c1")
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_for_robot",
                        lambda *a, **k: called.append("sync"))
    monkeypatch.setattr(bridge.fleet_link, "set_robot_mode", lambda r, m: {"ok": True})
    bridge.real_release("Pinky-3")
    assert called == ["async"]


# ── 관리자 추종 중에는 주행을 배차하지 않는다 ────────────────────────────────
#
# 추종은 FSM 을 안 거쳐서 fleet_node 는 이 로봇이 사람을 따라가는 중인 걸 모른다.
# 그래서 순회 경로 요청이 계속 들어오고, navigate 를 내보내면 로봇 쪽에서
# active_command 가 덮여 FollowExec 이 밀려나고, 그때 나가는 stop 이 추종 세션을 닫는다.
# 실측 2026-07-28: 추종 시작 20초 뒤 재배차가 들어와 세션이 끊겼다.

def test_no_nav_dispatch_while_admin_follow_is_granted(monkeypatch):
    """이 시험의 존재 이유 — 화면은 '추종 중'인데 로봇이 사람을 안 따라오던 원인."""
    from app import fleet_dispatch_bridge as fdb
    from app.routers import admin_follow

    sent = []
    monkeypatch.setattr(fdb.fleet_telemetry, "send_command_async",
                        lambda robot, action, args: sent.append((robot, action)) or "id-1")
    monkeypatch.setattr(fdb, "NAV_VIA_BT", True)

    with admin_follow._grants_lock:
        admin_follow._grants["Pinky-3"] = {"granted_at": 0.0}
    try:
        fdb.on_path_request("Pinky-3", [(1.0, 2.0, 0.0)])
        assert sent == [], "추종 중인 로봇에 주행을 배차했다 — 세션이 끊긴다"
    finally:
        with admin_follow._grants_lock:
            admin_follow._grants.pop("Pinky-3", None)


def test_guide_hop_is_sent_as_guide_not_navigate(monkeypatch):
    """길잡이도 홉을 받되 **액션 이름이 `guide`** 여야 한다.

    ⚠️ [2026-08-02] 계약이 바뀌었다. 예전엔 여기서 아무것도 안 보냈다(길잡이를 교통관제
       밖에 뒀다). 그러면 예약·간선 잠금·관제 지도의 경로선이 전부 없었다.

    ⚠️ `navigate` 로 보내면 `providers` 가 `active_command` 를 "navigate" 로 세워
       dispatch Selector 앞의 `NavigationExec` 이 가로챈다 — `GuideExec` 이 영영 안 돌아
       요청자를 놓쳐도 아무도 멈추지 않는다. 이 시험이 그 회귀를 잡는다.
    """
    from app import fleet_dispatch_bridge as fdb

    sent = []
    monkeypatch.setattr(fdb.fleet_telemetry, "send_command_async",
                        lambda robot, action, args: sent.append((robot, action, args)) or "id-1")
    monkeypatch.setattr(fdb, "NAV_VIA_BT", True)
    monkeypatch.setattr(fdb.fleet_link, "has_guide_target", lambda robot: robot == "Pinky-3")
    monkeypatch.setattr(fdb.fleet_link, "guide_target",
                        lambda robot: {"x": 9.0, "y": 9.0, "name": "과학-인문학서가"})
    _reset_nav()

    fdb.on_path_request("Pinky-3", [(1.0, 2.0, 0.0)])
    assert len(sent) == 1, "길잡이에 홉을 안 보냈다 — 교통관제를 못 탄다"
    robot, action, args = sent[0]
    assert action == "guide", "navigate 로 보내면 NavigationExec 이 가로채 GuideExec 이 죽는다"
    assert (args["x"], args["y"]) == (1.0, 2.0)
    assert args["name"] == "과학-인문학서가", "표시용 목적지 이름이 안 실렸다"


def test_lost_requester_pauses_the_reservation(monkeypatch):
    """`requester_lost` 결과 → 붙잡기 + **예약 해제. 단 PATROL 을 밀지 않는다.**

    ⚠️ `real_release()` 를 쓰면 두 가지가 같이 터진다(codex 검토 2026-08-02):
         · 내부 `_release_hold()` 가 방금 건 붙잡기를 곧바로 푼다
         · `set_robot_mode(PATROL)` 이 fleet 쪽 모드를 순찰로 바꿔, 로봇 FSM 이 아직
           WORKING 인데도 순회가 시작될 수 있다
       그래서 **현재 상태(WORKING)로** 모드를 다시 세워 태스크만 취소한다.
    """
    from app import fleet_dispatch_bridge as fdb

    calls = []
    monkeypatch.setattr(fdb.fleet_link, "set_robot_hold",
                        lambda robot, hold, ttl=0.0: calls.append(("hold", robot, hold)))
    monkeypatch.setattr(fdb.fleet_link, "set_robot_mode",
                        lambda robot, mode: calls.append(("mode", robot, mode)) or {"ok": True})
    monkeypatch.setattr(fdb, "real_release",
                        lambda robot: calls.append(("real_release", robot)))
    monkeypatch.setattr(fdb.fleet_link, "guide_target",
                        lambda robot: {"x": 0.0, "y": 0.0, "name": "출입구"})
    fdb._guide_hop_robot["hop-9"] = "Pinky-3"

    fdb.on_cmd_result({"id": "hop-9", "ok": False, "msg": "requester_lost"})

    assert calls == [("hold", "Pinky-3", True), ("mode", "Pinky-3", "WORKING")], \
        "붙잡기가 먼저여야 하고, 모드는 WORKING 이어야 한다(PATROL 이면 순회가 시작된다)"
    assert ("real_release", "Pinky-3") not in calls, \
        "real_release 는 붙잡기를 풀고 PATROL 을 민다 — 회복 경로에서 쓰면 안 된다"
    assert fdb._guide_paused.get("Pinky-3") == "출입구", "재획득 때 돌아갈 목적지를 안 기억했다"


def test_guide_bookkeeping_is_cleared_on_release():
    """안내가 끝나면 기억을 지운다 — 안 지우면 나중에 유령 재배차가 튄다."""
    from app import fleet_dispatch_bridge as fdb

    fdb._guide_paused["Pinky-3"] = "출입구"
    fdb._guide_hop_robot["hop-1"] = "Pinky-3"
    fdb._guide_hop_robot["hop-2"] = "Pinky-1"

    fdb.clear_guide_bookkeeping("Pinky-3")

    assert "Pinky-3" not in fdb._guide_paused
    assert "hop-1" not in fdb._guide_hop_robot
    assert fdb._guide_hop_robot.get("hop-2") == "Pinky-1", "남의 로봇 기억까지 지웠다"


def test_reacquire_redispatches_then_releases_hold(monkeypatch):
    """재획득 → **태스크를 다시 낸 뒤에** 붙잡기를 푼다."""
    from app import fleet_dispatch_bridge as fdb

    calls = []
    monkeypatch.setattr(fdb.fleet_link, "set_robot_hold",
                        lambda robot, hold, ttl=0.0: calls.append(("hold", robot, hold)))
    import app.routers.guide as guide_router
    monkeypatch.setattr(guide_router, "_submit_guide_task",
                        lambda robot, wp: calls.append(("submit", robot, wp)) or "task-3")
    monkeypatch.setattr(fdb.fleet_link, "has_guide_target", lambda robot: True)
    fdb._guide_paused["Pinky-3"] = "출입구"

    fdb.on_requester_visible("Pinky-3", True)

    assert calls == [("submit", "Pinky-3", "출입구"), ("hold", "Pinky-3", False)], \
        "붙잡기를 먼저 풀면 경로 없이 순회로 떠난다"


def test_reacquire_after_the_guide_ended_only_releases_the_hold(monkeypatch):
    """회복 중 취소·소진으로 안내가 끝났으면 **재배차하지 않는다.**

    ⚠️ 안 막으면 지나가던 사람이 뒷캠에 잡히기만 해도 로봇이 옛 목적지로 떠난다 —
       아무도 요청하지 않은 주행이다.
    """
    from app import fleet_dispatch_bridge as fdb

    calls = []
    monkeypatch.setattr(fdb.fleet_link, "set_robot_hold",
                        lambda robot, hold, ttl=0.0: calls.append(("hold", robot, hold)))
    import app.routers.guide as guide_router
    monkeypatch.setattr(guide_router, "_submit_guide_task",
                        lambda robot, wp: calls.append(("submit", robot, wp)) or "task-3")
    monkeypatch.setattr(fdb.fleet_link, "has_guide_target", lambda robot: False)
    fdb._guide_paused["Pinky-3"] = "출입구"

    fdb.on_requester_visible("Pinky-3", True)

    assert calls == [("hold", "Pinky-3", False)], "끝난 안내를 유령 재배차했다"
    assert "Pinky-3" not in fdb._guide_paused, "기억이 안 지워지면 다음에 또 튄다"


def test_reacquire_without_a_paused_guide_does_nothing(monkeypatch):
    """평소 가시성 신호로 아무 일도 일어나면 안 된다 — 이 토픽은 계속 온다."""
    from app import fleet_dispatch_bridge as fdb

    calls = []
    monkeypatch.setattr(fdb.fleet_link, "set_robot_hold",
                        lambda robot, hold, ttl=0.0: calls.append(("hold", robot, hold)))
    fdb._guide_paused.pop("Pinky-3", None)

    fdb.on_requester_visible("Pinky-3", True)
    assert calls == []


def test_nav_dispatch_resumes_after_release(monkeypatch):
    """해제하면 다시 배차돼야 한다 — 안 그러면 로봇이 영영 순회를 못 한다."""
    from app import fleet_dispatch_bridge as fdb

    sent = []
    monkeypatch.setattr(fdb.fleet_telemetry, "send_command_async",
                        lambda robot, action, args: sent.append((robot, action)) or "id-1")
    monkeypatch.setattr(fdb, "NAV_VIA_BT", True)
    monkeypatch.setattr(fdb, "_last_nav", {})

    fdb.on_path_request("Pinky-3", [(1.0, 2.0, 0.0)])
    assert sent == [("Pinky-3", "navigate")]


# ── 자동배차 로봇 선택 ────────────────────────────────────────────────────────
#
# 관제 프런트(dispatch-shared.ts:pickRobot)와 같은 규칙이어야 한다 — 두 곳에 각자
# 만들면 반드시 어긋난다. 여기선 파이썬 쪽 구현만 순수 함수로 검증한다.

def test_pick_robot_prefers_patrol_over_idle():
    from app.fleet_dispatch_bridge import pick_robot

    robots = [
        {"name": "idle-bot", "state": "IDLE", "busy": False, "battery": 100, "stale": False},
        {"name": "patrol-bot", "state": "PATROL", "busy": False, "battery": 10, "stale": False},
    ]
    assert pick_robot(robots) == "patrol-bot"


def test_pick_robot_prefers_higher_battery_within_same_state():
    from app.fleet_dispatch_bridge import pick_robot

    robots = [
        {"name": "low", "state": "PATROL", "busy": False, "battery": 30, "stale": False},
        {"name": "high", "state": "PATROL", "busy": False, "battery": 90, "stale": False},
    ]
    assert pick_robot(robots) == "high"


def test_pick_robot_excludes_stale_and_busy_and_wrong_state():
    from app.fleet_dispatch_bridge import pick_robot

    robots = [
        {"name": "stale", "state": "PATROL", "busy": False, "battery": 100, "stale": True},
        {"name": "busy", "state": "IDLE", "busy": True, "task_id": "orchestrator:t1", "battery": 100, "stale": False},
        {"name": "error", "state": "ERROR", "busy": False, "battery": 100, "stale": False},
    ]
    assert pick_robot(robots) is None


# ── 다리 표시 라벨 ────────────────────────────────────────────────────────────
#
# task_type 마다 다리 뜻이 다르다(수거 4다리 ≠ 배달 4다리). 화면이 leg_idx 로
# 라벨을 추측하던 옛 방식(LEG_STEPS) 대신, 실제 leg 값으로 여기서 만든다.

def test_leg_label_navigate_shows_destination():
    from app.fleet_dispatch_bridge import snapshot_leg_label

    leg = {"type": "navigate", "params": {"waypoint": "수거함"}}
    assert snapshot_leg_label(leg) == "주행 → 수거함"


def test_leg_label_known_arm_actions_are_readable():
    from app.fleet_dispatch_bridge import snapshot_leg_label

    cases = {
        "pick": "책 집기 (안네데스크)",
        "place": "책 놓기 (안네데스크)",
        "unload_to_floor": "바구니 내려놓기 (안네데스크)",
        "load_from_box": "바구니 싣기 (안네데스크)",
        "refill_box": "바구니 채우기 (안네데스크)",
    }
    for action, expected in cases.items():
        leg = {"type": "perform_action", "params": {"action": action, "at": "안네데스크"}}
        assert snapshot_leg_label(leg) == expected


def test_leg_label_unknown_action_falls_back_to_generic_text():
    from app.fleet_dispatch_bridge import snapshot_leg_label

    leg = {"type": "perform_action", "params": {"action": "who_knows", "at": "수거함"}}
    assert snapshot_leg_label(leg) == "작업 (수거함)"


def test_pick_robot_allows_patrol_task_to_be_preempted():
    """fleet_node 자체 순회(P-*)는 배차로 선점 가능해야 한다."""
    from app.fleet_dispatch_bridge import pick_robot

    robots = [
        {"name": "patrolling", "state": "PATROL", "busy": True, "task_id": "P-patrolling",
         "battery": 80, "stale": False},
    ]
    assert pick_robot(robots) == "patrolling"
