"""libi_modes FSM 링크 — 상태/BT 스냅샷 구독 캐시 + 전이 요청 채널.

구조는 `app/fleet_telemetry.py` 와 동일하다: 전용 rclpy Context 를 도메인에 고정해
백그라운드 스레드에서 돌리고, FastAPI 스레드는 락으로 보호된 캐시만 읽는다.

전이 요청이 ROS2 서비스가 아니라 상관관계 ID 토픽인 이유는
`aba_fms_service/docs/fsm-transport-decision.md` 참고 — domain_bridge 의 YAML 은 토픽만
중계하고, 서비스 브릿지는 C++ 컴파일 타임 템플릿 API 로만 존재한다 (실측 확인).

**이 모듈의 최상위는 rclpy 의존성이 없어야 한다** (ROS 미설치 환경에서도 import 가능).
fleet_telemetry.py 가 지키는 규칙과 같으며, 덕분에 아래 순수 함수들이 ROS2 없이 테스트된다.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any

from app.fsm_model import STATES

# 미션 PC 는 자기 도메인을 쓰고, domain_bridge 가 FMS 쪽 도메인으로 중계한다.
# 이 값은 'FMS 가 구독하는 쪽' 도메인이다 — fleet_telemetry 와 같은 86.
# 미션 PC 자체의 도메인 번호는 아직 미정이며, 그건 브릿지 설정에만 나타난다.
FSM_DOMAIN_ID = int(os.environ.get("LIBI_FSM_DOMAIN_ID", "86"))

STATE_TOPIC = os.environ.get("LIBI_FSM_STATE_TOPIC", "/libi/fsm_state")
TREE_TOPIC = os.environ.get("LIBI_FSM_TREE_TOPIC", "/libi/bt_snapshot")
CMD_TOPIC = os.environ.get("LIBI_FSM_CMD_TOPIC", "/libi/fsm_transition_request")
RESULT_TOPIC = os.environ.get("LIBI_FSM_RESULT_TOPIC", "/libi/fsm_transition_result")

FRESH_SEC = 10.0          # 이 시간 안에 수신이 없으면 stale 로 표시
CMD_TIMEOUT_SEC = 3.0

_lock = threading.Lock()
_started = False
_cache: dict[str, dict[str, Any]] = {}

_cmd_pub: Any = None
_StringMsg: Any = None
_pub_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()

# 브라우저 push 구독자 — (event_loop, asyncio.Queue) 쌍. WebSocket 라우터가 등록/해제한다.
_listeners: set = set()
_listeners_lock = threading.Lock()


def _empty_entry() -> dict[str, Any]:
    return {
        "current_state": None,
        "previous_state": None,
        "active_branch": None,
        "error_code": "",
        "battery_percent": None,
        "is_docked": None,
        "tree": None,
        "transitioned_at": 0.0,
        "_last_ros_at": 0.0,
    }


def _apply_state_msg(cache: dict[str, dict[str, Any]], payload: dict) -> str | None:
    """상태 메시지를 캐시에 접는다. 반영한 robot_id 를 반환(무시했으면 None).

    순수 함수 — rclpy 콜백은 이걸 감싸기만 한다.

    상태가 실제로 바뀔 때만 previous_state 를 옮긴다. 같은 상태가 주기적으로 재발행되는
    것은 전이가 아니므로, 그때도 옮기면 '직전 전이 간선 강조'가 매 tick 사라진다.
    """
    robot_id = payload.get("robot_id")
    state = payload.get("current_state")
    if not robot_id or state not in STATES:
        return None

    entry = cache.setdefault(robot_id, _empty_entry())
    if entry["current_state"] is not None and entry["current_state"] != state:
        entry["previous_state"] = entry["current_state"]
        entry["transitioned_at"] = time.time()
    entry["current_state"] = state
    entry["active_branch"] = payload.get("active_branch") or state
    entry["error_code"] = payload.get("error_code", "") or ""
    if payload.get("battery_percent") is not None:
        entry["battery_percent"] = payload["battery_percent"]
    if payload.get("is_docked") is not None:
        entry["is_docked"] = payload["is_docked"]
    entry["_last_ros_at"] = time.time()
    return robot_id


def _apply_tree_msg(cache: dict[str, dict[str, Any]], payload: dict) -> str | None:
    """py_trees 스냅샷을 캐시에 접는다."""
    robot_id = payload.get("robot_id")
    tree = payload.get("tree")
    if not robot_id or tree is None:
        return None
    entry = cache.setdefault(robot_id, _empty_entry())
    entry["tree"] = tree
    entry["_last_ros_at"] = time.time()
    return robot_id


def is_stale(entry: dict[str, Any]) -> bool:
    """수신이 끊긴 캐시인지. UI 는 이걸로 '수신 끊김'을 표시한다."""
    return (time.time() - entry.get("_last_ros_at", 0.0)) > FRESH_SEC


def snapshot(robot_id: str) -> dict[str, Any] | None:
    with _lock:
        entry = _cache.get(robot_id)
        if entry is None:
            return None
        out = dict(entry)
    out["stale"] = is_stale(out)
    return out


def known_ids() -> list[str]:
    """캐시에 들어와 있는 로봇 식별자 목록 — 로봇이 스스로 발행한 `robot_id` 그대로다.

    all_snapshots() 는 엔트리를 통째로 복사한다. 키만 필요한 조회(fsm 라우터의
    resolve_robot_id)가 요청마다 그 비용을 낼 이유가 없어 따로 둔다.
    """
    with _lock:
        return list(_cache)


def all_snapshots() -> dict[str, dict[str, Any]]:
    with _lock:
        items = {k: dict(v) for k, v in _cache.items()}
    for entry in items.values():
        entry["stale"] = is_stale(entry)
    return items


def add_listener(loop, queue) -> None:
    """WebSocket 핸들러의 이벤트 루프와 큐를 함께 등록한다.

    _notify 가 ROS 스레드에서 호출되므로 루프를 알아야 call_soon_threadsafe 로 넘길 수 있다.
    루프 없이 put_nowait 를 직접 부르면 asyncio 큐를 다른 스레드에서 건드리게 되어
    깨어나지 않는 대기자가 생긴다.
    """
    with _listeners_lock:
        _listeners.add((loop, queue))


def remove_listener(loop, queue) -> None:
    with _listeners_lock:
        _listeners.discard((loop, queue))


def _notify(robot_id: str) -> None:
    """캐시 변경을 WebSocket 구독자에게 push 한다 (폴링 API 없음)."""
    payload = snapshot(robot_id)
    if payload is None:
        return
    message = {"robot_id": robot_id, "snapshot": payload}
    with _listeners_lock:
        listeners = list(_listeners)
    for loop, queue in listeners:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        except Exception:
            pass  # 닫힌 루프/가득 찬 큐 — 그 구독자만 건너뛴다


def request_transition(
    robot_id: str,
    target_state: str,
    force: bool = False,
    timeout: float = CMD_TIMEOUT_SEC,
) -> dict[str, Any] | None:
    """전이 요청 발행 후 결과 대기. 링크 불가/타임아웃이면 None (호출측이 폴백 결정).

    반환 스키마는 libi_interfaces/srv/RequestTransition 응답과 동일:
    {"accepted": bool, "current_state": str, "reason": str}
    """
    if _cmd_pub is None or _StringMsg is None:
        return None
    try:
        if _cmd_pub.get_subscription_count() == 0:
            return None  # 브릿지 미기동/미션 PC 오프라인 — 기다리지 않고 즉시 폴백
    except Exception:
        return None

    cmd_id = uuid.uuid4().hex
    entry: dict[str, Any] = {"event": threading.Event(), "result": None}
    with _pending_lock:
        _pending[cmd_id] = entry
    payload = json.dumps({
        "id": cmd_id,
        "ts": time.time(),
        "robot_id": robot_id,
        "target_state": target_state,
        "force": bool(force),
    })
    try:
        with _pub_lock:
            _cmd_pub.publish(_StringMsg(data=payload))
    except Exception:
        with _pending_lock:
            _pending.pop(cmd_id, None)
        return None

    ok = entry["event"].wait(timeout)
    with _pending_lock:
        _pending.pop(cmd_id, None)  # 타임아웃이어도 pop — 누수 방지 (늦은 결과는 콜백이 무시)
    return entry["result"] if ok else None


def _fsm_thread() -> None:
    global _cmd_pub, _StringMsg
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_msgs.msg import String

        ctx = rclpy.Context()
        rclpy.init(context=ctx, domain_id=FSM_DOMAIN_ID)
        node = Node("fms_fsm_link", context=ctx)
        _StringMsg = String

        def on_state(msg) -> None:
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            with _lock:
                robot_id = _apply_state_msg(_cache, payload)
            if robot_id:
                _notify(robot_id)

        def on_tree(msg) -> None:
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            with _lock:
                robot_id = _apply_tree_msg(_cache, payload)
            if robot_id:
                _notify(robot_id)

        def on_result(msg) -> None:
            try:
                payload = json.loads(msg.data)
            except Exception:
                return
            cmd_id = payload.get("id")
            if not cmd_id:
                return
            with _pending_lock:
                entry = _pending.get(cmd_id)
            if entry is None:
                return  # 늦게 도착한 결과 — 이미 타임아웃 처리됨
            entry["result"] = {
                "accepted": bool(payload.get("accepted")),
                "current_state": payload.get("current_state", ""),
                "reason": payload.get("reason", ""),
            }
            entry["event"].set()

        node.create_subscription(String, STATE_TOPIC, on_state, 10)
        node.create_subscription(String, TREE_TOPIC, on_tree, 10)
        node.create_subscription(String, RESULT_TOPIC, on_result, 10)
        _cmd_pub = node.create_publisher(String, CMD_TOPIC, 10)

        print(f"[fsm_link] ROS 구독 시작 (domain {FSM_DOMAIN_ID}, state {STATE_TOPIC})", flush=True)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        executor.spin()
    except Exception as e:
        print(f"[fsm_link] 비활성 — ROS2 링크 없이 진행합니다: {e}", flush=True)


def start() -> None:
    """백그라운드 ROS 스레드를 한 번만 띄운다 (fleet_telemetry.start() 와 동일 패턴)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_fsm_thread, name="fsm_link", daemon=True).start()
