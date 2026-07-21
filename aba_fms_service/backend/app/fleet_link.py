"""libi_fleet 링크 — 배차·교통 코어(`fleet_ws`)의 서비스 호출 + 피드 구독 캐시.

구조는 `app/fsm_link.py` · `app/fleet_telemetry.py` 와 같다: 전용 rclpy Context 를 도메인에
고정해 백그라운드 스레드에서 돌리고, FastAPI 스레드는 락으로 보호된 캐시만 읽는다.

**fsm_link 와 다른 점 — 여기서는 진짜 ROS2 서비스를 쓴다.**
fsm_link 가 상관관계 ID 토픽을 쓰는 이유는 미션 PC 가 다른 도메인에 있고 domain_bridge 가
서비스를 중계하지 못하기 때문이다(`docs/fsm-transport-decision.md`). 반면 `fleet_node` 는
이 서비스와 같은 워크스페이스(`aba_fms_service/fleet_ws`)에 있고 같은 도메인에서 돌 것을
전제하므로, 브릿지를 건너지 않는다. 그래서 `/fms/*` 서비스를 그대로 호출한다.

**이 모듈의 최상위는 rclpy 의존성이 없어야 한다** (ROS 미설치 환경에서도 import 가능).
아래 순수 함수들이 ROS2 없이 테스트되는 이유다.

## fleet_node 가 발행하지 않는 것

`fleet_node` 는 로봇의 상태(`robot_mode_`)와 배터리를 **내부에만** 두고 어떤 토픽으로도
발행하지 않는다(publisher 5개: task_states/occupancy/routes/goals/robot_path_requests).
따라서 이 링크는 그 둘을 되읽을 수 없고, **이 패널을 통해 마지막으로 설정한 값**을 기억해
보여줄 뿐이다. 응답의 `mode_source`/`battery_source` 가 `"panel"` 이면 그 뜻이고,
`"unknown"` 이면 아직 아무도 설정하지 않아 fleet 내부값을 모른다는 뜻이다.
로봇이 실제로 무슨 상태인지는 libi_modes 가 소유하며 FSM 패널이 보여준다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

# 상태 어휘는 libi_modes 가 소유한다. 여기서 8종을 다시 적지 않는다.
from app import fsm_link
from app.fsm_model import STATES

# fleet_node 가 도는 도메인. 아직 확정되지 않았다 — 확정되면 배포 설정에서 넣는다.
# 기본값은 FMS 텔레메트리와 같은 86 으로, 같은 호스트에서 함께 띄우는 것을 전제한다.
FLEET_DOMAIN_ID = int(os.environ.get("LIBI_FLEET_DOMAIN_ID", "86"))

SRV_SUBMIT = "/fms/submit_task"
SRV_PLUGINS = "/fms/set_plugins"
SRV_MODE = "/fms/set_robot_mode"
SRV_BATTERY = "/fms/set_battery"
SRV_RELOAD = "/fms/reload_navgraph"

TOPIC_TASK_STATES = "/fms/task_states"
TOPIC_OCCUPANCY = "/fms/occupancy"
TOPIC_ROUTES = "/fms/routes"
TOPIC_GOALS = "/fms/goals"
TOPIC_ROBOT_STATE = "/robot_state"

FRESH_SEC = 10.0
CALL_TIMEOUT_SEC = 4.0
TASK_HISTORY_MAX = 50

_lock = threading.Lock()
_started = False

# 로봇별 관측값 — location 은 /robot_state, task/goal 은 fleet 피드에서 온다.
_robots: dict[str, dict[str, Any]] = {}
# fleet 내부값의 낙관적 반영 — 이 패널로 설정한 것만 안다 (위 docstring 참고).
_echo: dict[str, dict[str, Any]] = {}
_tasks: list[dict[str, Any]] = []
_occupancy: dict[str, str] = {}
_routes: dict[str, list] = {}
_goals: dict[str, int] = {}
_plugins: dict[str, str] = {"dispatcher": "", "traffic": ""}
_last_ros_at: float = 0.0

_clients: dict[str, Any] = {}
_node: Any = None

_listeners: set = set()
_listeners_lock = threading.Lock()


# ── 순수 함수 (rclpy 없이 테스트된다) ────────────────────────────────────────

def _empty_robot() -> dict[str, Any]:
    return {
        "name": "",
        "x": None,
        "y": None,
        "task_id": "",
        "task_state": "",
        "progress": 0.0,
        "busy": False,
        "goal_vertex": None,
        "_last_ros_at": 0.0,
    }


# TaskState 를 받았을 때 추가로 알려줄 곳들. orchestrator 배선(fleet_dispatch_bridge)이
# 여기에 자기 핸들러를 건다 — fleet_link 가 orchestrator 를 직접 알면 순환 의존이 된다.
# 훅에서 난 예외는 삼킨다: 구독 스레드가 죽으면 텔레메트리 전체가 멈추기 때문이다.
_task_state_hooks: list[Any] = []


def add_task_state_hook(fn) -> None:
    if fn not in _task_state_hooks:
        _task_state_hooks.append(fn)


def _fire_task_state_hooks(payload: dict) -> None:
    for fn in list(_task_state_hooks):
        try:
            fn(payload)
        except Exception as exc:  # noqa: BLE001 — 훅 하나가 구독 스레드를 죽이면 안 된다
            print(f"[fleet_link] task_state 훅 실패: {exc}", flush=True)


def apply_task_state(
    robots: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
    payload: dict,
) -> str | None:
    """TaskState 를 로봇 카드와 이력에 접는다. 반영한 robot_id 를 반환.

    busy 판정은 `state` 로 한다 — ASSIGNED/EXECUTING 이면 진행 중, 그 외(COMPLETED/FAILED/
    REJECTED)면 끝난 것이다. fleet 내부 `busy` 플래그를 되읽을 방법이 없으므로 이게 최선이며,
    태스크 없이 순회만 도는 로봇은 busy=False 로 보인다(순회는 배차 후보이므로 맞는 표시다).
    """
    robot_id = payload.get("robot_id")
    if not robot_id:
        return None

    state = payload.get("state", "") or ""
    entry = robots.setdefault(robot_id, _empty_robot())
    entry["name"] = robot_id
    entry["task_id"] = payload.get("task_id", "") or ""
    entry["task_state"] = state
    entry["progress"] = float(payload.get("progress", 0.0) or 0.0)
    entry["busy"] = state in ("ASSIGNED", "EXECUTING")
    entry["_last_ros_at"] = time.time()

    tasks.append({
        "task_id": entry["task_id"],
        "state": state,
        "robot_id": robot_id,
        "progress": entry["progress"],
        "at": time.time(),
    })
    del tasks[:-TASK_HISTORY_MAX]     # 최근 N건만 유지
    return robot_id


def apply_robot_state(robots: dict[str, dict[str, Any]], payload: dict) -> str | None:
    """rmf RobotState 에서 위치를 접는다.

    배터리는 일부러 무시한다 — fleet_node 도 sim 의 battery_percent 를 신뢰하지 않고
    자기 내부값을 쓰며(`/fms/set_battery` 로만 바뀐다), 여기서 sim 값을 보여주면
    배차 판정에 실제로 쓰이는 값과 다른 숫자가 화면에 뜬다.
    """
    name = payload.get("name")
    if not name:
        return None
    entry = robots.setdefault(name, _empty_robot())
    entry["name"] = name
    loc = payload.get("location") or {}
    entry["x"] = loc.get("x")
    entry["y"] = loc.get("y")
    entry["_last_ros_at"] = time.time()
    return name


def parse_json_map(raw: str) -> dict:
    """fleet_node 가 std_msgs/String 에 실어 보내는 JSON 맵. 깨진 프레임은 빈 맵."""
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def build_rows(
    robots: dict[str, dict[str, Any]],
    echo: dict[str, dict[str, Any]],
    goals: dict[str, int],
    occupancy: dict[str, str],
    fsm: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """패널이 그리는 로봇 표를 만든다.

    state/battery 는 fleet_node 의 RobotState 에 없다. 출처가 둘이라 우선순위를 둔다:

      1. `fsm`  — 로봇의 libi_modes 가 `/libi/fsm_state` 로 실제 발행한 값 (정본)
      2. `echo` — 이 패널에서 손으로 설정한 값 (sim·디버그, FSM 없는 장비용)

    FSM 을 우선하는 이유: fleet_node 도 `on_fsm_state` 에서 `robot_mode_` 를 무조건
    덮어쓴다. 패널 값을 우선하면 **화면과 fleet_node 의 배차 판단이 어긋난다**
    (화면엔 PATROL 인데 fleet_node 는 RETURNING 으로 알고 배차를 안 하는 식).
    수신이 끊긴(stale) FSM 은 쓰지 않고 패널 값으로 내려간다.

    예전엔 `echo` 뿐이라, 로봇이 멀쩡히 상태를 발행해도 패널에서 손으로 설정하지 않는 한
    표의 state 가 늘 비어 배차 가능 대수가 0 으로 보였다.

    출처를 `*_source` 로 함께 실어 보내, UI 가 어디서 온 값인지 구분하게 한다.
    """
    fsm = fsm or {}
    names = set(robots) | set(echo)
    rows = []
    for name in sorted(names):
        base = robots.get(name, _empty_robot())
        ec = echo.get(name, {})
        # 끊긴 FSM 캐시는 없는 셈 친다 — 마지막으로 본 상태를 현재처럼 보여주면 안 된다.
        fs = fsm.get(name) or {}
        if fs.get("stale"):
            fs = {}
        fsm_state = fs.get("current_state")
        fsm_batt = fs.get("battery_percent")
        held = sorted(int(n) for n, who in occupancy.items() if who == name)
        rows.append({
            "name": name,
            "x": base.get("x"),
            "y": base.get("y"),
            "task_id": base.get("task_id", ""),
            "task_state": base.get("task_state", ""),
            "progress": base.get("progress", 0.0),
            "busy": base.get("busy", False),
            "goal_vertex": goals.get(name),
            "held_nodes": held,
            "state": fsm_state or ec.get("state"),
            "state_source": (
                "fsm" if fsm_state else ("panel" if ec.get("state") else "unknown")
            ),
            "battery": fsm_batt if fsm_batt is not None else ec.get("battery"),
            "battery_source": (
                "fsm"
                if fsm_batt is not None
                else ("panel" if ec.get("battery") is not None else "unknown")
            ),
            "stale": (time.time() - base.get("_last_ros_at", 0.0)) > FRESH_SEC,
        })
    return rows


def is_valid_state(state: str) -> bool:
    """libi_modes 8종인지. fleet_node 도 같은 집합으로 거부하지만, 왕복 전에 걸러
    사용자에게 즉시 사유를 보여준다."""
    return state in STATES


# ── 캐시 접근 ────────────────────────────────────────────────────────────────

def snapshot() -> dict[str, Any]:
    # fsm 캐시는 **_lock 을 잡기 전에** 읽는다. 락 두 개를 겹쳐 잡을 이유가 없다.
    fsm_states = fsm_link.all_snapshots()
    with _lock:
        rows = build_rows(_robots, _echo, _goals, _occupancy, fsm_states)
        return {
            "robots": rows,
            "tasks": list(_tasks),
            "occupancy": dict(_occupancy),
            "routes": {k: list(v) for k, v in _routes.items()},
            "goals": dict(_goals),
            "plugins": dict(_plugins),
            "linked": _node is not None,
            "domain_id": FLEET_DOMAIN_ID,
            "stale": (time.time() - _last_ros_at) > FRESH_SEC if _last_ros_at else True,
        }


def add_listener(loop, queue) -> None:
    with _listeners_lock:
        _listeners.add((loop, queue))


def remove_listener(loop, queue) -> None:
    with _listeners_lock:
        _listeners.discard((loop, queue))


def _notify() -> None:
    payload = snapshot()
    with _listeners_lock:
        listeners = list(_listeners)
    for loop, queue in listeners:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except Exception:
            pass  # 닫힌 루프/가득 찬 큐 — 그 구독자만 건너뛴다


# ── 서비스 호출 ──────────────────────────────────────────────────────────────

def _call(name: str, request: Any, timeout: float = CALL_TIMEOUT_SEC) -> Any | None:
    """서비스 1회 호출. 링크 없음/미기동/타임아웃이면 None (호출측이 사유를 만든다).

    응답은 백그라운드 executor 스레드가 처리하므로, 여기서는 done 콜백이 세우는 Event 를
    기다린다. future.result() 를 곧장 블로킹하면 어느 스레드가 spin 하는지에 따라 굳는다.
    """
    client = _clients.get(name)
    if client is None:
        return None
    if not client.service_is_ready():
        return None

    done = threading.Event()
    box: dict[str, Any] = {}

    future = client.call_async(request)
    future.add_done_callback(lambda f: (box.update(result=f.result()), done.set()))
    if not done.wait(timeout):
        return None
    return box.get("result")


def submit_task(
    dropoff: str,
    robot: str = "",
    arm_actions: int = 0,
    task_type: str = "delivery",
    requester: str = "fms-panel",
) -> dict[str, Any]:
    """태스크 투입. `robot` 이 비면 dispatcher 가 경매로 고른다."""
    if _node is None:
        return {"accepted": False, "task_id": "", "reason": "fleet_link_down"}
    try:
        from libi_fleet_msgs.srv import SubmitTask
    except Exception:
        return {"accepted": False, "task_id": "", "reason": "libi_fleet_msgs_missing"}

    req = SubmitTask.Request()
    req.task_type = task_type
    req.pickup = ""
    req.dropoff = str(dropoff)
    req.requester = requester
    req.robot = robot or ""
    req.priority = 0
    req.arm_actions = int(arm_actions)

    res = _call(SRV_SUBMIT, req)
    if res is None:
        return {"accepted": False, "task_id": "", "reason": "fleet_node_unavailable"}
    return {"accepted": bool(res.accepted), "task_id": res.task_id, "reason": res.reason}


def set_robot_mode(robot: str, mode: str) -> dict[str, Any]:
    """로봇 상태 강제 설정 (sim·디버그). 성공하면 echo 에 기록해 표에 반영한다."""
    if not is_valid_state(mode):
        return {"ok": False, "reason": "bad_mode"}
    if _node is None:
        return {"ok": False, "reason": "fleet_link_down"}
    try:
        from libi_fleet_msgs.srv import SetRobotMode
    except Exception:
        return {"ok": False, "reason": "libi_fleet_msgs_missing"}

    req = SetRobotMode.Request()
    req.robot = robot
    req.mode = mode
    res = _call(SRV_MODE, req)
    if res is None:
        return {"ok": False, "reason": "fleet_node_unavailable"}
    if res.ok:
        with _lock:
            _echo.setdefault(robot, {})["state"] = mode
        _notify()
    return {"ok": bool(res.ok), "reason": res.reason}


def set_battery(robot: str, value: float) -> dict[str, Any]:
    if _node is None:
        return {"ok": False, "reason": "fleet_link_down"}
    try:
        from libi_fleet_msgs.srv import SetBattery
    except Exception:
        return {"ok": False, "reason": "libi_fleet_msgs_missing"}

    req = SetBattery.Request()
    req.robot = robot
    req.value = float(value)
    res = _call(SRV_BATTERY, req)
    if res is None:
        return {"ok": False, "reason": "fleet_node_unavailable"}
    if res.ok:
        with _lock:
            _echo.setdefault(robot, {})["battery"] = float(value)
        _notify()
    return {"ok": bool(res.ok), "reason": res.reason}


def set_plugins(dispatcher: str, traffic: str) -> dict[str, Any]:
    if _node is None:
        return {"ok": False, "reason": "fleet_link_down",
                "active_dispatcher": "", "active_traffic": ""}
    try:
        from libi_fleet_msgs.srv import SetPlugins
    except Exception:
        return {"ok": False, "reason": "libi_fleet_msgs_missing",
                "active_dispatcher": "", "active_traffic": ""}

    req = SetPlugins.Request()
    req.dispatcher = dispatcher or ""
    req.traffic = traffic or ""
    res = _call(SRV_PLUGINS, req)
    if res is None:
        return {"ok": False, "reason": "fleet_node_unavailable",
                "active_dispatcher": "", "active_traffic": ""}
    if res.ok:
        with _lock:
            _plugins["dispatcher"] = res.active_dispatcher
            _plugins["traffic"] = res.active_traffic
        _notify()
    return {
        "ok": bool(res.ok),
        "reason": res.reason,
        "active_dispatcher": res.active_dispatcher,
        "active_traffic": res.active_traffic,
    }


def reload_navgraph() -> dict[str, Any]:
    if _node is None:
        return {"ok": False, "reason": "fleet_link_down"}
    try:
        from std_srvs.srv import Trigger
    except Exception:
        return {"ok": False, "reason": "std_srvs_missing"}

    res = _call(SRV_RELOAD, Trigger.Request())
    if res is None:
        return {"ok": False, "reason": "fleet_node_unavailable"}
    return {"ok": bool(res.success), "reason": res.message}


# ── ROS 스레드 ───────────────────────────────────────────────────────────────

def _fleet_thread() -> None:
    global _node, _last_ros_at
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from std_msgs.msg import String
        from std_srvs.srv import Trigger

        from libi_fleet_msgs.msg import TaskState
        from libi_fleet_msgs.srv import SetBattery, SetPlugins, SetRobotMode, SubmitTask
        from rmf_fleet_msgs.msg import RobotState as RmfRobotState

        ctx = rclpy.Context()
        rclpy.init(context=ctx, domain_id=FLEET_DOMAIN_ID)
        node = Node("fms_fleet_link", context=ctx)

        def touch() -> None:
            global _last_ros_at
            _last_ros_at = time.time()

        def on_task_state(msg) -> None:
            payload = {
                "task_id": msg.task_id,
                "state": msg.state,
                "robot_id": msg.robot_id,
                "progress": msg.progress,
            }
            with _lock:
                apply_task_state(_robots, _tasks, payload)
                touch()
            _notify()
            _fire_task_state_hooks(payload)

        def on_robot_state(msg) -> None:
            payload = {
                "name": msg.name,
                "location": {"x": msg.location.x, "y": msg.location.y},
            }
            with _lock:
                apply_robot_state(_robots, payload)
                touch()
            _notify()

        def on_occupancy(msg) -> None:
            parsed = parse_json_map(msg.data)
            with _lock:
                _occupancy.clear()
                _occupancy.update({str(k): str(v) for k, v in parsed.items()})
                touch()
            _notify()

        def on_routes(msg) -> None:
            parsed = parse_json_map(msg.data)
            with _lock:
                _routes.clear()
                _routes.update(parsed)
                touch()
            _notify()

        def on_goals(msg) -> None:
            parsed = parse_json_map(msg.data)
            with _lock:
                _goals.clear()
                _goals.update({str(k): v for k, v in parsed.items()})
                touch()
            _notify()

        node.create_subscription(TaskState, TOPIC_TASK_STATES, on_task_state, 10)
        node.create_subscription(RmfRobotState, TOPIC_ROBOT_STATE, on_robot_state, 10)
        node.create_subscription(String, TOPIC_OCCUPANCY, on_occupancy, 10)
        node.create_subscription(String, TOPIC_ROUTES, on_routes, 10)
        node.create_subscription(String, TOPIC_GOALS, on_goals, 10)

        _clients[SRV_SUBMIT] = node.create_client(SubmitTask, SRV_SUBMIT)
        _clients[SRV_PLUGINS] = node.create_client(SetPlugins, SRV_PLUGINS)
        _clients[SRV_MODE] = node.create_client(SetRobotMode, SRV_MODE)
        _clients[SRV_BATTERY] = node.create_client(SetBattery, SRV_BATTERY)
        _clients[SRV_RELOAD] = node.create_client(Trigger, SRV_RELOAD)
        _node = node

        print(f"[fleet_link] ROS 링크 시작 (domain {FLEET_DOMAIN_ID})", flush=True)
        executor = SingleThreadedExecutor(context=ctx)
        executor.add_node(node)
        executor.spin()
    except Exception as e:
        print(f"[fleet_link] 비활성 — libi_fleet 링크 없이 진행합니다: {e}", flush=True)


def start() -> None:
    """백그라운드 ROS 스레드를 한 번만 띄운다 (fsm_link.start() 와 동일 패턴)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_fleet_thread, name="fleet_link", daemon=True).start()
