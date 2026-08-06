"""로봇 보고 → 사건 발행, 그리고 fleet_node 로의 하행/상행 배선.

ROS 없이 도는 부분만 여기서 본다 — 실제 rclpy 발행/구독은 `fleet_link` 의 `_fleet_thread()`
안(ROS2 필요)이라 여기선 `fleet_link.set_node_block` 을 monkeypatch 해서 "불렸는가" 만
확인하고, 상행은 `fleet_link.node_block_from_json`(순수 함수) 과 `_on_node_block_report`
(훅 콜백)를 직접 불러서 본다.
"""
import app.fleet_dispatch_bridge as bridge
from app import fleet_events, fleet_link
from app.fleet_dispatch_bridge import (
    on_person_blocked, on_shelf_dock_lock, on_shelf_dock_phase, real_dispatch,
)
from app.fleet_orchestrator import Leg, LegType


def setup_function():
    fleet_events.reset()
    # round 3 부터 실제 navgraph 정점 번호(작은 정수)를 쓰는 시험이 섞여, 앞선 시험이
    # 남긴 임의 번호(5, 9, 77 등)와 우연히 겹칠 수 있다 — 매번 비운다.
    bridge._node_blocks.clear()


def test_person_block_publishes_an_event():
    on_person_blocked("pinky3", 9, 60.0)
    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert "person_blocked" in kinds


def test_person_block_event_carries_robot_and_node():
    on_person_blocked("pinky3", 9, 60.0)
    ev = [e for e in fleet_events.since(0) if e["kind"] == "person_blocked"][0]
    assert ev["robot"] == "pinky3"
    assert ev["node"] == 9


def test_release_publishes_a_separate_event():
    on_person_blocked("pinky3", 9, 0.0)
    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert "node_unblocked" in kinds
    assert "person_blocked" not in kinds


def test_shelf_dock_start_and_done_are_distinct_events():
    on_shelf_dock_phase("pinky1", "문학서가", "started")
    on_shelf_dock_phase("pinky1", "문학서가", "done")
    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert kinds == ["shelf_dock_started", "shelf_dock_done"]


def test_shelf_dock_event_carries_the_shelf_name():
    on_shelf_dock_phase("pinky1", "과학-인문학서가", "started")
    ev = fleet_events.since(0)[0]
    assert ev["shelf"] == "과학-인문학서가"


def test_unknown_phase_publishes_nothing():
    on_shelf_dock_phase("pinky1", "문학서가", "wat")
    assert fleet_events.since(0) == []


# ── R8-3: TTL 자동 만료도 사건을 낸다 ────────────────────────────────────────
#
# `now` 를 직접 밀어 시계를 흉내 낸다 — 실제로 60 초를 기다리지 않는다.

def test_ttl_expiry_publishes_node_unblocked():
    """[P2-1] 시간만 지나도(**새 차단 보고 없이**) 만료 사건이 나가는지.

    ⚠️ 원래 이 시험은 두 번째·세 번째 `on_person_blocked` 호출(다른 노드)로 sweep 을
    강제했다 — 그러면 "새 보고 없이 시간만 지나도 나가는가" 는 검증되지 않는다
    (codex 최종 검토 P2-1 지적). 여기서는 스윕 함수(`_publish_ttl_expirations`)
    자체를 **새 차단을 넣지 않고** 직접 불러 "시간이 지나면 사건이 나가는가" 를 본다.

    [fix round 7] 이 시험을 고칠 당시엔 `_publish_ttl_expirations` 를 부르는 자리가
    `on_person_blocked`/`on_shelf_dock_lock` 뿐이라 **주기 sweep 이 없었다** — 이제는
    `reconcile_once()`(이미 도는 화해 루프)가 매 주기 이 함수를 불러 준다. 그 배선은
    `test_reconcile_publishes_ttl_expiry_without_a_new_report` 가 따로 검증하고,
    이 시험은 여전히 **스윕 함수 자체가 정확한지**(사건 한 번, 중복 없음)를 본다.
    """
    on_person_blocked("pinky7", 177, 1.0, now=100.0)     # node 177, 100~101 사이에 산다

    unblocked = [e for e in fleet_events.since(0)
                 if e["kind"] == "node_unblocked" and e.get("node") == 177]
    assert unblocked == []              # 아직 안 지났다 — 사건도 없다

    bridge._publish_ttl_expirations(105.0)   # 새 차단/해제 보고 없이 시계만 진행
    unblocked = [e for e in fleet_events.since(0)
                 if e["kind"] == "node_unblocked" and e.get("node") == 177]
    assert len(unblocked) == 1
    assert unblocked[0]["reason"] == "ttl"

    bridge._publish_ttl_expirations(110.0)   # 다시 스윕해도 같은 만료를 또 안 낸다
    unblocked = [e for e in fleet_events.since(0)
                 if e["kind"] == "node_unblocked" and e.get("node") == 177]
    assert len(unblocked) == 1


# ── fix round 7: reconcile_once() 에 TTL sweep 을 얹는다 ──────────────────────
#
# 위 시험은 스윕 함수 자체가 옳은지만 봤다 — 그걸 **누가 부르는지**(주기 sweep)는
# 없었다(P2-1 실측 지적). `_reconcile_loop()`/`reconcile_once()` 는 이미 도는
# 화해 루프라 새 스레드를 안 만들고 여기에 얹었다.

def test_reconcile_publishes_ttl_expiry_without_a_new_report(monkeypatch):
    """차단 하나 걸고 **새 보고 없이** 시계만 밀어 `reconcile_once()` 를 부르면
    `node_unblocked` 가 나가는지."""
    on_person_blocked("pinky8", 188, 1.0, now=200.0)     # node 188, 200~201 사이에 산다
    monkeypatch.setattr(bridge.time, "monotonic", lambda: 205.0)   # 새 보고 없이 시계만 진행

    n = bridge.reconcile_once()
    assert n == 0   # 기존 반환값(고아 정리 개수)은 그대로 — 우리가 건 것과 무관

    unblocked = [e for e in fleet_events.since(0)
                 if e["kind"] == "node_unblocked" and e.get("node") == 188]
    assert len(unblocked) == 1
    assert unblocked[0]["reason"] == "ttl"


def test_reconcile_with_nothing_expired_publishes_nothing():
    """만료된 것이 없으면 `reconcile_once()` 가 아무 사건도 안 낸다."""
    n = bridge.reconcile_once()
    assert n == 0
    assert fleet_events.since(0) == []


# ── fix round 1: 하행(FMS→fleet_node)·상행(로봇→FMS) 실배선 ────────────────────

def test_publish_reaches_fleet_link(monkeypatch):
    """`on_person_blocked` 이 `fleet_link.set_node_block` 을 실제로 부르는지, 인자가
    맞는지(owner 는 `person:<robot>`)."""
    calls = []
    monkeypatch.setattr(
        bridge.fleet_link, "set_node_block",
        lambda node, ttl_sec, reason="", robot="", owner="":
            calls.append((node, ttl_sec, reason, robot, owner)),
    )
    on_person_blocked("pinky3", 9, 60.0)
    assert calls == [(9, 60.0, "person", "pinky3", "person:pinky3")]


def test_robot_report_flows_into_on_person_blocked(monkeypatch):
    """상행 훅(`_on_node_block_report`)에 이미 검증된 payload 를 넣으면 사건이 나가고
    `set_node_block` 도 불리는지."""
    calls = []
    monkeypatch.setattr(bridge.fleet_link, "set_node_block",
                         lambda *a, **k: calls.append((a, k)))
    bridge._on_node_block_report("pinky2", {"node": 5, "ttl_sec": 30.0, "reason": "person"})
    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert "person_blocked" in kinds
    assert calls


def test_malformed_robot_payload_is_dropped():
    """깨진 JSON·`node` 없음은 예외 없이 `None` — 구독 콜백은 이걸 보고 경고만 남긴다."""
    assert fleet_link.node_block_from_json("not json") is None
    assert fleet_link.node_block_from_json('{"ttl_sec": 5, "reason": "person"}') is None
    assert fleet_link.node_block_from_json('{"node": 5}') == {
        "node": 5, "ttl_sec": 0.0, "reason": "",
    }


# ── fix round 2: 서가 도킹 잠금 (D8/B3) ──────────────────────────────────────
#
# 서가 다리가 배차되면 그 정점을 `dock:` owner 로 잠근다. 로봇이 정밀 이동을 시작하면
# `reason="shelf_dock", ttl_sec=0` 을 올려 스스로 푼다. owner 가 `person:` 과 달라
# 같은 정점에 걸린 사람 차단을 안 지운다(R4).

def test_shelf_lock_uses_a_dock_owner():
    on_shelf_dock_lock("pinky1", 200, 180.0)
    assert bridge._node_blocks.owners_of(200) == ["dock:pinky1"]


def test_person_block_and_shelf_lock_coexist_on_one_node():
    on_person_blocked("pinky3", 201, 60.0)
    on_shelf_dock_lock("pinky1", 201, 180.0)
    assert bridge._node_blocks.owners_of(201) == ["dock:pinky1", "person:pinky3"]


def test_robot_shelf_dock_release_only_releases_the_dock_owner():
    on_person_blocked("pinky3", 202, 60.0)
    on_shelf_dock_lock("pinky1", 202, 180.0)
    # 로봇이 정밀 이동을 시작하며 도킹 잠금만 푼다고 보고한다.
    bridge._on_node_block_report("pinky1", {"node": 202, "ttl_sec": 0.0, "reason": "shelf_dock"})
    assert bridge._node_blocks.owners_of(202) == ["person:pinky3"]


def test_shelf_lock_publishes_an_event():
    on_shelf_dock_lock("pinky1", 203, 180.0)
    kinds = [e["kind"] for e in fleet_events.since(0)]
    assert "shelf_node_locked" in kinds


# ── fix round 3 (수정됨, round 5 최종 리뷰 Critical 3): 서가 다리 배차 ────────
#
# ⚠️ 원래 round 3 은 여기서 fleet_node(submit_task)를 우회해 로봇에 `shelf_dock` 을
# 바로 쐈다 — **틀렸다**(최종 리뷰 Critical 3). 서가 정점도 다른 NAVIGATE 다리와 똑같이
# `fleet_link.submit_task` 로 정상 주행시키고, `on_task_state` 의 COMPLETED(도착)를
# 본 뒤에야 `shelf_dock` 을 낸다. 아래 시험들은 **고쳐진 순서**를 검증한다.
# 실제 navgraph(`arte2.navgraph.yaml`)로 정점 번호를 그대로 구한다.

def _nav_leg(waypoint: str) -> Leg:
    return Leg(LegType.NAVIGATE, {"waypoint": waypoint})


def _arm_leg_at(at: str, action: str = "pick") -> Leg:
    return Leg(LegType.PERFORM_ACTION, {"action": action, "at": at, "book": "B1"})


def _reset_nav():
    with bridge._nav_lock:
        bridge._last_nav.clear()
    bridge._nav_goal.clear()


def _mock_submit_task(monkeypatch, task_id: str = "fleet-1", accepted: bool = True):
    monkeypatch.setattr(
        bridge.fleet_link, "submit_task",
        lambda **kw: {"accepted": accepted, "task_id": task_id, "reason": "" if accepted else "no"},
    )


def _mock_send_command(monkeypatch) -> list:
    """`send_command_async` 를 가로챈다. 부를 때마다 새 cmd_id 를 돌려준다."""
    calls: list = []

    def _fake(robot, action, args=None):
        cmd_id = f"cmd-{len(calls) + 1}"
        calls.append((robot, action, dict(args or {}), cmd_id))
        return cmd_id

    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async", _fake)
    return calls


def test_shelf_leg_drives_before_docking(monkeypatch):
    """[Critical 3] 배차 즉시 shelf_dock 이 나가지 않는다 — 도착 신호
    (`on_task_state` COMPLETED) 뒤에야 나간다. 잠금은 배차 즉시 그대로 걸린다."""
    node = bridge.resolve_vertex("문학서가")
    _mock_submit_task(monkeypatch, task_id="fleet-drive-1")
    calls = _mock_send_command(monkeypatch)

    cmd_id = real_dispatch("t1", "pinky1", _nav_leg("문학서가"))
    assert cmd_id == "fleet-drive-1"          # fleet 이 준 주행 task_id 그대로
    assert calls == []                        # shelf_dock 이 아직 안 나갔다
    assert bridge._node_blocks.owners_of(node) == ["dock:pinky1"]   # 잠금은 배차 즉시

    bridge.on_task_state({"task_id": "fleet-drive-1", "state": "COMPLETED"})
    assert len(calls) == 1
    robot, action, args, _dock_cmd_id = calls[0]
    assert (robot, action, args) == ("pinky1", "shelf_dock", {"shelf": "문학서가", "node": node})


def test_shelf_leg_uses_submit_task_like_any_navigate_leg(monkeypatch):
    """서가 정점도 fleet_node CBS 로 정상 주행한다 — submit_task 인자가 일반 다리와 같다."""
    submitted = []
    monkeypatch.setattr(
        bridge.fleet_link, "submit_task",
        lambda **kw: submitted.append(kw) or {"accepted": True, "task_id": "fleet-2"},
    )
    node = bridge.resolve_vertex("문학서가")
    real_dispatch("t2", "pinky1", _nav_leg("문학서가"))
    assert submitted and submitted[0]["dropoff"] == str(node)
    assert submitted[0]["robot"] == "pinky1"


def test_non_shelf_leg_does_not_lock(monkeypatch):
    node = bridge.resolve_vertex("1번테이블")
    calls = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                         lambda *a, **k: calls.append((a, k)) or "cmd-x")
    _mock_submit_task(monkeypatch, task_id="fleet-t3")
    real_dispatch("t3", "pinky1", _nav_leg("1번테이블"))
    assert calls == []                                   # shelf_dock 이 안 나갔다
    assert bridge._node_blocks.owners_of(node) == []


def test_art_shelf_now_locks_like_the_other_two_shelves(monkeypatch):
    """실측(2026-08-05): 예술서가가 `SHELF_NODES` 밖이라 실주문으로 shelf_dock 이 전혀
    안 나갔다(status 가 시도 내내 "waiting") — 로봇은 도착만 하고 정밀 도킹 없이
    다리가 끝났다. 세 서가 다 navgraph 에 yaw 가 없는 건 동일하다(shelf_dock.py 자체
    보정 회전이 처리) — 예술서가만 뺄 근거가 없었다. 이제 다른 두 서가와 같이 잠근다."""
    node = bridge.resolve_vertex("예술서가")
    _mock_submit_task(monkeypatch, task_id="fleet-t4")
    real_dispatch("t4", "pinky1", _nav_leg("예술서가"))
    assert bridge._node_blocks.owners_of(node) == ["dock:pinky1"]


def test_robot_release_unlocks_what_dispatch_locked(monkeypatch):
    """배차가 잠근 것을 로봇의 `ttl=0` 보고가 정확히 푸는 왕복."""
    node = bridge.resolve_vertex("과학-인문학서가")
    _mock_submit_task(monkeypatch, task_id="fleet-t5")
    real_dispatch("t5", "pinky2", _nav_leg("과학-인문학서가"))
    assert bridge._node_blocks.owners_of(node) == ["dock:pinky2"]

    bridge._on_node_block_report("pinky2", {"node": node, "ttl_sec": 0.0, "reason": "shelf_dock"})
    assert bridge._node_blocks.owners_of(node) == []


def test_shelf_dock_result_closes_the_original_navigate_leg(monkeypatch):
    """[Critical 3] 도킹 결과가 오면 그제서야 원래 NAVIGATE 다리(fleet task_id)가 닫힌다."""
    _mock_submit_task(monkeypatch, task_id="fleet-t6")
    calls = _mock_send_command(monkeypatch)
    closed = []
    monkeypatch.setattr(
        bridge, "_orc",
        lambda: type("O", (), {"on_result": lambda s, i, ok, m="": closed.append((i, ok, m))})(),
    )

    cmd_id = real_dispatch("t6", "pinky1", _nav_leg("문학서가"))
    bridge.on_task_state({"task_id": cmd_id, "state": "COMPLETED"})
    assert closed == []                       # 도착만 했다 — 도킹 중이라 아직 안 닫혔다

    dock_cmd_id = calls[0][3]
    bridge.on_cmd_result({"id": dock_cmd_id, "ok": True, "msg": "docked"})
    assert closed == [(cmd_id, True, "docked")]


# ── fix round 4 (수정됨): 서가 도킹 동안 로봇도 붙잡는다 (RobotHold) ──────────
#
# node_block 은 **다른 로봇**이 그 정점을 지나가는 것만 막는다. fleet_node 가 이
# 로봇 자신을 유휴로 보고 순회를 새로 거는 것은 못 막는다 — 팔 다리와 똑같은 이유로
# hold 를 건다(`RobotHold.msg` 의 존재 이유 그대로). hold 는 여전히 **배차 시점**에
# 걸리고(잠금과 같은 자리), 도킹 결과가 온 뒤에 풀린다(round 3 재배선 이후에도 동일).

def test_shelf_leg_holds_the_robot(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.fleet_link, "set_robot_hold",
                         lambda robot, hold, ttl_sec=120.0: calls.append((robot, hold, ttl_sec)))
    _mock_submit_task(monkeypatch, task_id="fleet-t7")
    real_dispatch("t7", "pinky1", _nav_leg("문학서가"))
    assert ("pinky1", True, bridge.SHELF_DOCK_TTL_SEC) in calls


def test_non_shelf_leg_does_not_hold(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.fleet_link, "set_robot_hold",
                         lambda robot, hold, ttl_sec=120.0: calls.append((robot, hold, ttl_sec)))
    _mock_submit_task(monkeypatch, task_id="fleet-t8")
    real_dispatch("t8", "pinky1", _nav_leg("1번테이블"))
    assert calls == []


def test_shelf_dock_result_releases_the_hold(monkeypatch):
    released = []
    monkeypatch.setattr(bridge.fleet_link, "set_robot_hold",
                         lambda robot, hold, ttl_sec=120.0: released.append((robot, hold)))
    _mock_submit_task(monkeypatch, task_id="fleet-t9")
    calls = _mock_send_command(monkeypatch)

    cmd_id = real_dispatch("t9", "pinky1", _nav_leg("문학서가"))
    assert ("pinky1", True) in released           # 배차 때 걸렸고

    bridge.on_task_state({"task_id": cmd_id, "state": "COMPLETED"})
    dock_cmd_id = calls[0][3]
    assert ("pinky1", False) not in released      # 도착만 했다 — 아직 안 풀린다

    bridge.on_cmd_result({"id": dock_cmd_id, "ok": True, "msg": "docked"})
    assert ("pinky1", False) in released           # 도킹 결과가 오면 풀린다


def test_hold_ttl_matches_the_node_lock_ttl(monkeypatch):
    """두 TTL 이 어긋나면 한쪽만 먼저 풀려 그 사이 재배차/재진입이 들어온다."""
    hold_ttls = []
    lock_ttls = []
    monkeypatch.setattr(bridge.fleet_link, "set_robot_hold",
                         lambda robot, hold, ttl_sec=120.0: hold_ttls.append(ttl_sec))
    monkeypatch.setattr(bridge, "on_shelf_dock_lock",
                         lambda robot, node, ttl_sec=None, now=None: lock_ttls.append(ttl_sec))
    _mock_submit_task(monkeypatch, task_id="fleet-t10")
    real_dispatch("t10", "pinky1", _nav_leg("문학서가"))
    assert hold_ttls == [bridge.SHELF_DOCK_TTL_SEC]
    assert lock_ttls == [bridge.SHELF_DOCK_TTL_SEC]


# ── fix round 5 — 최종 리뷰 Critical 1: navigate 홉에 node/is_destination ─────
#
# 로봇 쪽 `providers.py` 는 args 에서 `node`/`is_destination` 을 읽고,
# `PersonBlockGuard` 는 `node is None` 이면 **조용히 아무 것도 안 한다** — 사람이
# 아무리 오래 막아도 차단 보고가 한 번도 안 나간다.

def test_navigate_args_carry_the_committed_node(monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                         lambda robot, action, args=None: sent.append((robot, action, args)) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()
    node = bridge.resolve_vertex("1번테이블")
    x, y = bridge.vertex_xy(node)

    bridge.on_path_request("pinky9", [(x, y, 0.0)])
    assert sent
    assert sent[0][2]["node"] == node


def test_destination_leg_is_flagged(monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                         lambda robot, action, args=None: sent.append((robot, action, args)) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    _reset_nav()
    node = bridge.resolve_vertex("1번테이블")
    # `real_dispatch` 가 다리를 낼 때 적는 것을 흉내 — (그 다리의 cmd_id, 목적지 정점).
    # cmd_id 를 같이 들고 있어야 `on_task_state` 가 다리를 닫을 때 지울 수 있다.
    bridge._nav_goal["pinky9"] = ("task-1", node)
    x, y = bridge.vertex_xy(node)

    bridge.on_path_request("pinky9", [(x, y, 0.0)])
    assert sent[0][2]["is_destination"] is True

    # 다른 정점이면 목적지가 아니다.
    _reset_nav()
    sent.clear()
    other = bridge.resolve_vertex("2번테이블")
    ox, oy = bridge.vertex_xy(other)
    bridge.on_path_request("pinky9", [(ox, oy, 0.0)])
    assert sent[0][2]["is_destination"] is False


# ── [2026-08-03] 사람 차단 때 FMS 는 **후퇴를 쏘지 않는다** ──────────────────
#
# 예전에는 여기서 로봇에 `backup` 을 직접 보냈다(round 5 Critical 2). 그 이동이
# **예약 체계 밖**이라, 이미 놓아 준 직전 정점에 다른 로봇이 들어와 정면으로 만날 수
# 있었다(codex 3차 P0 셋이 전부 그 하나에서 나왔다).
#
# 되돌리는 일은 이제 `fleet_node` 가 한다 — `/fms/node_block` 콜백에서 직전 정점을
# `request_move(A, A)` 로 **점유 claim 한 뒤에만** 막힌 정점을 놓고 `moving=false` 로
# 내린다. 그 뒤는 평소 GRANT 경로를 타므로 후퇴도 교통관제 안에서 일어난다.
#
# 이 시험들은 "다시 쏘기 시작하면 빨개진다" 를 붙들고 있다.

def test_person_block_does_not_send_backup(monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append((robot, action, args)) or "c1")
    monkeypatch.setattr(bridge.fleet_link, "set_node_block", lambda *a, **k: None)
    _reset_nav()
    n2 = bridge.resolve_vertex("2번테이블")

    bridge._on_node_block_report("pinky5", {"node": n2, "ttl_sec": 60.0, "reason": "person"})

    assert [c for c in sent if c[1] == "backup"] == [], (
        "사람 차단에 FMS 가 후퇴를 직접 쏘면 안 된다 — 예약 밖 이동이라 충돌 창이 열린다")


def test_person_block_still_reports_to_fleet_node(monkeypatch):
    """후퇴를 뺐다고 차단 보고까지 사라지면 안 된다 — 그게 재계획의 방아쇠다."""
    calls = []
    monkeypatch.setattr(bridge.fleet_link, "set_node_block",
                        lambda *a, **k: calls.append((a, k)))
    _reset_nav()
    n2 = bridge.resolve_vertex("2번테이블")
    bridge._on_node_block_report("pinky5", {"node": n2, "ttl_sec": 60.0, "reason": "person"})
    assert calls, "fleet_node 로 차단이 안 나갔다"

def test_arm_completion_does_not_trigger_a_backup(monkeypatch):
    """[2026-08-05] 팔 완료가 복귀를 부르지 않는다 — 도킹 자세 탈출은 로봇 BT 몫이다.

    이 시험은 예전에 정반대(`triggers_a_backup`)를 주장했고 **이미 빨간불이었다** —
    바로 위 머리말이 "되돌리는 일은 이제 `fleet_node` 가 한다, FMS 가 직접 쏘면
    예약 밖 이동이라 충돌 창이 열린다" 고 못 박아둔 것과 모순이었다. 사실에 맞게 뒤집는다.

    ⚠️ 복귀 자체가 없어진 게 아니다 — `decompose_delivery` 의 **backup 다리**가 한다
    (orchestrator 가 pick 완료를 받은 뒤 순서대로 배정). 여기서 막는 건 브릿지가
    팔 결과를 보고 **다리 순서 밖으로** 직접 쏘는 경로다.
    """
    monkeypatch.setattr(bridge, "ARM_VIA_BT", True)
    calls = _mock_send_command(monkeypatch)

    cmd_id = real_dispatch("t11", "pinky1", _arm_leg_at("문학서가"))
    bridge.on_cmd_result({"id": cmd_id, "ok": True, "msg": "picked"})

    assert [c for c in calls if c[1] == "backup"] == [], (
        "FMS 가 팔 완료 뒤 복귀를 직접 쏘고 있다 — 예약 밖 이동이라 충돌 창이 열린다")


# ── fix round 5 — 최종 리뷰 Important 4: 잠금·해제 owner 정규화 ──────────────

def test_lock_and_release_agree_on_the_owner_name():
    """잠글 때 DB 표기("Pinky-1")를 써도, 로봇의 해제 보고(브릿지 키 "pinky1")가
    같은 owner 를 정확히 지운다 — 정규화가 없으면 no-op 이 되어 TTL 까지 안 풀린다."""
    on_shelf_dock_lock("Pinky-1", 220, 180.0)
    assert bridge._node_blocks.owners_of(220) == ["dock:pinky1"]

    bridge._on_node_block_report("pinky1", {"node": 220, "ttl_sec": 0.0, "reason": "shelf_dock"})
    assert bridge._node_blocks.owners_of(220) == []


# ── fix round 5 — 최종 리뷰 Important 5: shelf_dock 진행 사건 배선 ────────────

def test_shelf_dock_publishes_started_and_done(monkeypatch):
    _mock_submit_task(monkeypatch, task_id="fleet-ph1")
    calls = _mock_send_command(monkeypatch)

    def _dock_kinds():
        return [e["kind"] for e in fleet_events.since(0) if e["kind"].startswith("shelf_dock_")]

    cmd_id = real_dispatch("t12", "pinky1", _nav_leg("문학서가"))
    assert _dock_kinds() == []                  # 아직 도착 전 — 도킹 사건은 없다(잠금 사건은 별개)

    bridge.on_task_state({"task_id": cmd_id, "state": "COMPLETED"})
    assert _dock_kinds() == ["shelf_dock_started"]

    dock_cmd_id = calls[0][3]
    bridge.on_cmd_result({"id": dock_cmd_id, "ok": True, "msg": "docked"})
    assert _dock_kinds() == ["shelf_dock_started", "shelf_dock_done"]


def test_finished_leg_forgets_its_destination(monkeypatch):
    """다리가 닫히면 `_nav_goal` 을 지운다.

    안 지우면 그 뒤 순회를 돌 때 낡은 목적지와 같은 정점에서만 `is_destination` 이
    True 가 되어, **순회 중 그 노드 하나만 조용히 차단 보고가 안 나간다.**
    되돌림 확인: `on_task_state` 의 정리 루프를 지우면 이 시험이 빨개진다.
    """
    sent = []
    monkeypatch.setattr(bridge.fleet_telemetry, "send_command_async",
                        lambda robot, action, args=None: sent.append((robot, action, args)) or "c1")
    monkeypatch.setattr(bridge, "NAV_VIA_BT", True)
    monkeypatch.setattr(bridge, "_orc", lambda: _StubOrc())
    _reset_nav()
    node = bridge.resolve_vertex("1번테이블")
    bridge._nav_goal["pinky9"] = ("task-1", node)

    bridge.on_task_state({"task_id": "task-1", "state": "COMPLETED"})
    assert "pinky9" not in bridge._nav_goal, "닫힌 다리의 목적지가 남았다"

    # 이제 같은 정점을 순회로 지나가도 목적지가 아니다 → 차단 보고가 살아 있다.
    x, y = bridge.vertex_xy(node)
    bridge.on_path_request("pinky9", [(x, y, 0.0)])
    assert sent[-1][2]["is_destination"] is False


class _StubOrc:
    def on_result(self, *a, **k):
        return None


# ── 2단계: 로봇 이름 표기 차이 흡수 (2026-08-03 실기) ────────────────────────
#
# `on_path_request` 는 fleet_node 이름(`pinky-3`)으로 쓰고, 차단 보고 훅은 토픽
# 접두사(`pinky3`)로 읽는다. 그래서 `_prev_granted.get("pinky3")` 이 영원히 None 이라
# **후퇴 명령이 한 번도 안 나갔다**:
#   `[backup] pinky3 직전 정점을 몰라 후진 명령을 못 낸다`  (11:35:48 · 11:50:07)
# 후퇴가 죽으면 로봇이 사람 앞에 그대로 서 있고, 그 자리에서는 nav2 가 경로를 못 만든다.
