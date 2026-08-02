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
from app.ros_domains import domain_for
from app.fsm_model import STATES

# fleet_node 가 도는 도메인. 기본은 서버 도메인(app/ros_domains.py, 기본 111)이고,
# 같은 호스트에서 함께 띄우는 것을 전제한다.
# ⚠️ 이걸 바꾸면 fleet_node 를 띄우는 쪽도 같이 바꿔야 한다
#    (scripts/laptop/fms_service.sh 의 `export ROS_DOMAIN_ID`).
FLEET_DOMAIN_ID = domain_for("LIBI_FLEET_DOMAIN_ID")

SRV_SUBMIT = "/fms/submit_task"
SRV_PLUGINS = "/fms/set_plugins"
SRV_MODE = "/fms/set_robot_mode"
SRV_BATTERY = "/fms/set_battery"
SRV_RELOAD = "/fms/reload_navgraph"

TOPIC_TASK_STATES = "/fms/task_states"
TOPIC_OCCUPANCY = "/fms/occupancy"
TOPIC_ROUTES = "/fms/routes"
#: CBS 시간표. /fms/routes 는 좌표뿐이라 "언제 어디" 를 모른다 — 예약 시각을 화면에
#  띄우려면 도착틱이 필요하다. transient_local 이라 늦게 붙어도 마지막 값을 받는다.
#  반응형 교통에서는 아무것도 안 온다(= 예약 표시가 없는 것이 정상).
TOPIC_PLAN = "/fms/plan"
#: 주문 다리 사이에 로봇을 붙잡아 순회로 떠나지 않게 한다.
#  fleet_node 는 주행 다리만 알아서, 팔 다리가 도는 동안 로봇이 유휴로 보인다.
#  TTL 이 있어 푸는 쪽이 죽어도 로봇이 영영 묶이지 않는다.
TOPIC_HOLD = "/fms/robot_hold"
TOPIC_GOALS = "/fms/goals"
TOPIC_ROBOT_STATE = "/robot_state"
#: fleet_node 가 발행하는 주행 경로. 예전엔 로봇 쪽 path_request_driver 가 직접 받아
#  nav2 로 넣었는데, 그러면 로봇 BT 를 우회해 FSM 이 WORKING 으로 가지 않았다.
#  이제 여기서 받아 /fleet_cmd{navigate} 로 BT 에 넘긴다.
TOPIC_PATH_REQUESTS = "/robot_path_requests"

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
#: 마지막 시간표. robots 가 비어 있으면 **계획을 버린 상태**다(reason 에 이유).
#  화면은 그때 경로 강조를 지워야 한다 — 옛 예약을 계속 띄우면 화면만 거짓이 된다.
_plan: dict[str, Any] = {}
_goals: dict[str, int] = {}
#: 길잡이의 목적지는 fleet_node 경로가 아니다. nav2가 직접 계획하므로, 관제에는
#: "정확한 경로가 아닌 목표"만 따로 싣는다.
_guide_targets: dict[str, dict[str, Any]] = {}
#: 로봇별 **nav2 실주행 경로**. `robot_state_adapter` 가 `/pinkyN/plan` 을 이름 붙여
#: `/robot_nav_path` 로 넘겨 준다. `_routes`(fleet_node 의 CBS 순회 경로)와 **다르다** —
#: 길잡이는 nav2 가 직접 몰아서 fleet_node 가 그 목적지를 모른다.
_nav_paths: dict[str, list[list[float]]] = {}
#: 그 경로를 마지막으로 받은 시각(로봇별). 신선도 판정에 쓴다.
_nav_paths_at: dict[str, float] = {}
_plugins: dict[str, str] = {"dispatcher": "", "traffic": ""}
_last_ros_at: float = 0.0

_hold_pub: Any = None
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
        "yaw": None,      # 로봇이 바라보는 방향(rad). 화면이 화살표로 그린다
        "task_id": "",
        "task_state": "",
        "progress": 0.0,
        "busy": False,
        "goal_vertex": None,
        "_last_ros_at": 0.0,
        # ⚠️ **위치 보고 전용** 신선도. `_last_ros_at` 과 나눠야 하는 이유는
        #    `stale` 계산이 쓰는 값이 바로 이것이기 때문이다 — 아래 build_rows 주석.
        "_last_pose_at": 0.0,
    }


# TaskState 를 받았을 때 추가로 알려줄 곳들. orchestrator 배선(fleet_dispatch_bridge)이
# 여기에 자기 핸들러를 건다 — fleet_link 가 orchestrator 를 직접 알면 순환 의존이 된다.
# 훅에서 난 예외는 삼킨다: 구독 스레드가 죽으면 텔레메트리 전체가 멈추기 때문이다.
_task_state_hooks: list[Any] = []


def add_task_state_hook(fn) -> None:
    if fn not in _task_state_hooks:
        _task_state_hooks.append(fn)


# fleet_node 가 낸 주행 경로를 받아갈 곳. orchestrator 배선(fleet_dispatch_bridge)이
# 여기에 핸들러를 걸어 로봇의 BT 로 내려보낸다.
_path_request_hooks: list[Any] = []


def add_path_request_hook(fn) -> None:
    if fn not in _path_request_hooks:
        _path_request_hooks.append(fn)


def _fire_path_request_hooks(robot: str, points: list) -> None:
    for fn in list(_path_request_hooks):
        try:
            fn(robot, points)
        except Exception as exc:  # noqa: BLE001 — 훅 하나가 구독 스레드를 죽이면 안 된다
            print(f"[fleet_link] path_request 훅 실패: {exc}", flush=True)


#: 길잡이 요청자 가시성 훅. `on_requester_visible(robot, visible)` 로 부른다.
_requester_hooks: list[Any] = []
#: 로봇별 마지막 가시성. **상승 에지에서만** 훅을 부르려고 들고 있다 — 이 토픽은
#  감시가 도는 동안 계속 오므로 매번 부르면 재배차가 쏟아진다.
_requester_last: dict[str, bool] = {}


def add_requester_visible_hook(fn) -> None:
    if fn not in _requester_hooks:
        _requester_hooks.append(fn)


def _fire_requester_visible(robot: str, visible: bool) -> None:
    if _requester_last.get(robot) == visible:
        return                      # 값이 안 바뀌었다 — 에지가 아니다
    _requester_last[robot] = visible
    for fn in list(_requester_hooks):
        try:
            fn(robot, visible)
        except Exception as exc:  # noqa: BLE001 — 훅 하나가 구독 스레드를 죽이면 안 된다
            print(f"[fleet_link] requester_visible 훅 실패: {exc}", flush=True)


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
    # 방향까지 접는다. 점만 찍으면 로봇이 **어디를 보고 있는지** 알 수 없어, 서가를 등지고
    # 섰는지 도킹 방향이 맞는지를 화면에서 못 가른다.
    entry["yaw"] = loc.get("yaw")
    entry["_last_ros_at"] = time.time()
    # **여기서만** 찍는다. 위치를 실제로 보고받은 순간이다.
    entry["_last_pose_at"] = time.time()
    return name


def parse_json_map(raw: str) -> dict:
    """fleet_node 가 std_msgs/String 에 실어 보내는 JSON 맵. 깨진 프레임은 빈 맵."""
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _fsm_entry(fsm: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    """이 로봇의 FSM 캐시 항목. 표기가 달라도 찾는다.

    ⚠️ 캐시 키는 **로봇이 스스로 발행한 robot_id** 이고, 이 표의 이름은 DB
    (`rc_robots.name`)에서 온다. 둘이 늘 같지는 않다 — 로봇을 `pinky3` 로 띄우고
    DB 엔 `Pinky-3` 로 등록해 두면 정확히 일치하지 않아 **상태가 통째로 빈다**
    (실측: 관제에 "상태 미상", 배차 가능 0대. 정작 /api/fsm/state 로는 잘 보였다 —
    거기만 표기 차이를 흡수하고 있었기 때문이다).

    규칙은 fsm_link 가 소유한다(fsm 라우터도 같은 걸 쓴다).

    ⚠️ 이건 **표시를 살리는 안전망**일 뿐이다. 이름이 어긋나면 로봇 쪽 경로 드라이버가
    PathRequest 를 이름으로 걸러 **배차해도 움직이지 않는다.** 근본 해결은 로봇을
    DB 와 같은 이름으로 띄우는 것이다(`pi.sh --robot <DB이름>`).
    """
    entry = fsm.get(name)
    if entry is not None:
        return entry
    want = fsm_link.match_key(name)
    for key, value in fsm.items():
        if fsm_link.match_key(key) == want:
            return value
    return {}


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
        fs = _fsm_entry(fsm, name)
        if fs.get("stale"):
            fs = {}
        fsm_state = fs.get("current_state")
        fsm_batt = fs.get("battery_percent")
        held = sorted(int(n) for n, who in occupancy.items() if who == name)
        rows.append({
            "name": name,
            "x": base.get("x"),
            "y": base.get("y"),
            "yaw": base.get("yaw"),
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
            # ⚠️ [2026-08-02] **`_last_ros_at` 이 아니라 `_last_pose_at` 을 본다.**
            #
            #   `_last_ros_at` 은 `apply_task_state` 도 찍는다(같은 파일). 그런데
            #   TaskState 를 발행하는 것은 **fleet_node 자신**이다. 로봇이 죽어 위치
            #   보고가 끊겨도 fleet_node 가 그 로봇의 순회 task(`P-pinky-3`)를 계속
            #   내보내는 한 도장이 갱신되어 `stale` 이 **영원히 false** 였다.
            #
            #   실측 2026-08-02: fleet_node 는 `[WARN] 로봇 상태 끊김(10s 이상): pinky-3`
            #   을 계속 찍는데 백엔드 스냅샷은 `stale: false` 였다. 문턱은 양쪽 다 10초로
            #   같았다(FRESH_SEC · kRobotStaleSec) — 다른 것은 **무엇을 세느냐**였다.
            #   그 사이 프론트는 stale 을 믿고 꺼진 로봇을 계속 그렸다.
            #
            #   같은 부류를 오늘 `GuideExec` 에서도 고쳤다: 자기 계열의 발행이
            #   페일세이프의 기준 시각을 밀어 올려 무력화하는 것.
            #   **살아 있다는 증거는 로봇이 보낸 것이어야 한다.**
            "stale": (time.time() - base.get("_last_pose_at", 0.0)) > FRESH_SEC,
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
            "plan": dict(_plan),
            "goals": dict(_goals),
            # ⚠️ [2026-08-02] **살아서 일하는 로봇의 것만 내보낸다.**
            #
            #   `set_guide_target` 은 승인 때 넣고 `/release` 때만 뺀다. 그런데 안내가
            #   그 길로만 끝나지 않는다 — 회복 실패(FAILURE), 로봇 전원 차단, FMS 재기동,
            #   패널 종료. 그 경우 목표가 **영원히 남아** 지도에 유령 다이아몬드가 뜬다.
            #   오늘 하루 고친 것이 전부 "화면이 없는 것을 현재처럼 보여주는 것" 이었다.
            #
            #   해제 훅을 여기저기 다는 대신 **표시 조건을 사실에 매단다**: 그 로봇이
            #   (1) 살아 있고(위치 보고가 신선하고) (2) 지금 WORKING 이어야 한다.
            #   안내가 어떻게 끝나든 둘 중 하나는 반드시 깨지므로 저절로 사라진다.
            "guide_targets": _live_guide_targets(rows, fsm_states),
            # nav2 가 지금 실제로 따라가는 경로. **추정이 아니라 로봇이 낸 값**이다.
            # 오래됐거나 로봇이 꺼졌으면 안 보낸다 — `_live_nav_paths` 주석.
            "nav_paths": _live_nav_paths(rows),
            "plugins": dict(_plugins),
            "linked": _node is not None,
            "domain_id": FLEET_DOMAIN_ID,
            # 브라우저가 자기 시계와의 오차를 빼기 위해 쓴다. 계획의 epoch_wall 은
            # fleet_node 의 벽시계인데, 브라우저 시계가 몇 초 틀어져 있으면 "T-12.3초" 가
            # 그만큼 통째로 어긋난다. 서버와 fleet_node 는 같은 기계라 이 값으로 보정된다.
            "server_now": time.time(),
            "stale": (time.time() - _last_ros_at) > FRESH_SEC if _last_ros_at else True,
        }


#: nav2 경로를 신선하다고 볼 시간(초). nav2 는 주행 중 계속 재발행하므로, 이보다
#: 오래됐으면 **주행이 끝났거나 로봇이 죽은 것**이다. 화면에 옛 선을 남기지 않는다.
NAV_PATH_FRESH_SEC = 5.0


def _live_nav_paths(rows) -> dict[str, list[list[float]]]:
    """살아 있는 로봇의 **신선한** nav2 경로만 남긴다.

    ⚠️ 빈 경로도 그대로 내보낸다. nav2 가 목표를 취소하면 빈 Path 를 낸다 —
       그것이 "지금 가는 곳이 없다"는 사실이고, 화면이 옛 선을 계속 그리면 안 된다.
    """
    now = time.time()
    live = {r["name"] for r in rows if not r.get("stale")}
    out: dict[str, list[list[float]]] = {}
    for name, pts in _nav_paths.items():
        if name not in live:
            continue
        if now - _nav_paths_at.get(name, 0.0) > NAV_PATH_FRESH_SEC:
            continue
        out[name] = list(pts)
    return out


#: 길잡이 목표를 보여 줄 FSM 상태. 안내는 WORKING 에서만 돈다
#: (`guide.py` GUIDE_STATE). 여기를 벗어났다는 것은 안내가 끝났다는 뜻이다.
_GUIDE_ACTIVE_STATES = frozenset({"WORKING"})


def _live_guide_targets(rows, fsm) -> dict[str, dict[str, Any]]:
    """살아서 안내 중인 로봇의 목표만 남긴다 — `snapshot` 의 ⚠️ 주석 참고.

    `rows` 는 이미 만들어진 표라 `stale` 과 `state` 가 들어 있다. 여기서 다시 계산하지
    않는 이유는 두 곳이 갈리면 **지도의 마커와 목표가 서로 다른 말을 하기** 때문이다.
    """
    out: dict[str, dict[str, Any]] = {}
    by_name = {r["name"]: r for r in rows}
    for name, target in _guide_targets.items():
        row = by_name.get(name)
        if row is None or row.get("stale"):
            continue                      # 꺼졌거나 위치 보고가 끊긴 로봇
        if (row.get("state") or "") not in _GUIDE_ACTIVE_STATES:
            continue                      # 안내가 끝났다(성공·실패·취소 모두 여기로)
        out[name] = dict(target)
    return out


def set_guide_target(robot_id: str, target: dict[str, Any] | None) -> None:
    """길잡이 목표 표시를 갱신한다. nav2 경로를 추정해서 routes에 섞지 않는다."""
    with _lock:
        if target is None:
            _guide_targets.pop(robot_id, None)
        else:
            _guide_targets[robot_id] = {
                "x": float(target["x"]), "y": float(target["y"]),
                "name": str(target.get("name", "")),
            }
    _notify()


def guide_target(robot_id: str) -> dict[str, Any] | None:
    """등록된 안내 목표({x,y,name}) 사본. 없으면 None.

    홉 명령에 목적지 **이름**을 실어 보내려고 쓴다(`fleet_dispatch_bridge`).
    사본을 주는 이유: 호출자가 dict 를 고쳐도 원본이 안 바뀌게.
    """
    with _lock:
        t = _guide_targets.get(robot_id)
        return dict(t) if t else None


def has_guide_target(robot_id: str) -> bool:
    """로봇이 아직 GuideExec 소유인지 확인한다.

    fleet_node의 PathRequest는 순회/주문용이므로, 안내가 직접 nav2를 소유하는 동안
    그것을 BT ``navigate``로 중계하면 안 된다. 지도 표시는 WORKING 여부로 한 번 더
    걸러도, 이 함수는 명령 경합을 막기 위해 등록 자체를 정본으로 본다.
    """
    # 정상 종료 뒤 `/release`가 따로 오지 않아도 target 레코드는 남을 수 있다. 그 레코드만
    # 믿으면 안내를 끝낸 로봇의 PATROL PathRequest까지 영구 차단한다. FSM 정본이 이미
    # WORKING 밖이면 안내도 끝난 것이므로 중계를 다시 연다.
    #
    # ⚠️ [2026-08-02] **배제 목록이 아니라 `_GUIDE_ACTIVE_STATES` 하나를 본다.**
    #   예전에는 `state != "PATROL" and state != "IDLE" and ...` 로 여섯 개를 나열했다.
    #   상태가 하나 늘면(registry 의 8종은 늘 수 있다) 그 상태가 **조용히 "안내 중"으로
    #   분류돼** 순회 중계가 영구 차단된다. 게다가 화면 표시(`_live_guide_targets`)는
    #   포함 목록을 쓰고 있어서 **두 판정이 갈려 있었다** — 지도에는 목표가 없는데
    #   명령은 계속 막히는 상태가 만들어질 수 있다. 정본을 하나로 묶는다.
    #
    # ⚠️ 상태를 못 읽는 순간에는 **안전 쪽(차단)** 을 택한다 — 그 창의 순회 navigate 가
    #    GuideExec 을 죽이는 편이 더 위험하다(그게 오늘 잡은 P0 였다).
    with _lock:
        if robot_id not in _guide_targets:
            return False
    state = (fsm_link.snapshot(robot_id) or {}).get("current_state")
    if not state:
        return True
    return state in _GUIDE_ACTIVE_STATES


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
            # 피드는 사건 로그가 아니라 최신 상태다. ROS 콜백마다 전부 쌓으면, 느린
            # WebSocket 소비자가 같은 내용을 수십 프레임 뒤늦게 받고 화면도 다시 흔들린다.
            # 이 콜백은 asyncio 루프에서 실행되므로 queue 조작도 그 루프에만 한정된다.
            def put_latest(q=queue, value=payload) -> None:
                while True:
                    try:
                        q.get_nowait()
                    except Exception:  # QueueEmpty (테스트의 queue 호환 객체도 허용)
                        break
                try:
                    q.put_nowait(value)
                except Exception:
                    pass

            loop.call_soon_threadsafe(put_latest)
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


def set_robot_hold(robot: str, hold: bool, ttl_sec: float = 120.0) -> bool:
    """주문 다리 사이에 로봇을 붙잡거나 푼다.

    ⚠️ **거는 쪽과 푸는 쪽이 짝이 맞아야 한다.** 안 풀면 TTL 이 만료될 때까지 그 로봇은
    순회를 못 한다(fleet_node 가 만료를 경고와 함께 스스로 푼다 — 영구 점유는 없다).
    """
    if _hold_pub is None:
        return False
    # ⚠️ 메시지 타입은 **함수 안에서** import 한다. 이 모듈은 rclpy 없이도 import 돼야
    #    하는데(테스트·오프라인), 모듈 스코프에서 쓰면 NameError 가 나고 그게 dispatch
    #    실패로 둔갑해 주문이 통째로 FAILED 가 된다 — 실제로 그랬다.
    from libi_fleet_msgs.msg import RobotHold

    m = RobotHold()
    m.robot = robot
    m.hold = bool(hold)
    m.ttl_sec = float(ttl_sec)
    _hold_pub.publish(m)
    return True


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
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String
        from std_srvs.srv import Trigger

        from libi_fleet_msgs.msg import TaskState
        from libi_fleet_msgs.msg import (
            FleetGoals, FleetOccupancy, FleetPlan, FleetRoutes, RobotHold,
        )
        from libi_fleet_msgs.srv import SetBattery, SetPlugins, SetRobotMode, SubmitTask
        from rmf_fleet_msgs.msg import PathRequest
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
                "location": {
                    "x": msg.location.x,
                    "y": msg.location.y,
                    # rmf RobotState 는 yaw 를 늘 싣고 있었다 — 그동안 버리고 있었을 뿐이다.
                    "yaw": getattr(msg.location, "yaw", None),
                },
            }
            with _lock:
                apply_robot_state(_robots, payload)
                touch()
            _notify()

        def on_nav_path(msg) -> None:
            """`robot_state_adapter` 가 넘긴 nav2 실주행 경로.

            ⚠️ `touch()` 를 부르지 **않는다.** 이건 로봇 생존 판정에 쓰는 신호가 아니다
               — 생존은 `robot_state`(위치 보고)가 쥔다(`_last_pose_at`). 여기서 찍으면
               오늘 고친 "관제 자신의 발행을 로봇 생존으로 오인" 과 같은 부류가 된다.
            """
            try:
                payload = json.loads(msg.data)
            except (ValueError, TypeError):
                return
            name = payload.get("name")
            if not name:
                return
            pts = payload.get("points")
            if not isinstance(pts, list):
                return
            with _lock:
                _nav_paths[str(name)] = [
                    [float(p[0]), float(p[1])] for p in pts
                    if isinstance(p, (list, tuple)) and len(p) >= 2
                ]
                _nav_paths_at[str(name)] = time.time()
            _notify()

        def on_occupancy(msg) -> None:
            with _lock:
                _occupancy.clear()
                _occupancy.update(
                    {str(n): str(r) for n, r in zip(msg.nodes, msg.robots)}
                )
                touch()
            _notify()

        def on_plan(msg) -> None:
            # 타입 메시지 → 화면이 쓰던 것과 **같은 모양**의 dict. UI 계약은 안 바뀐다.
            parsed = {
                "seq": int(msg.seq),
                "epoch": float(msg.epoch),
                "epoch_wall": float(msg.epoch_wall),
                "tick_sec": float(msg.tick_sec),
                "drift_limit": int(msg.drift_limit),
                "reason": str(msg.reason),
                "robots": {
                    r.robot: {
                        "path": [int(v) for v in r.path],
                        "arrive": [int(a) for a in r.arrive_tick],
                        "xy": [[float(x), float(y)] for x, y in zip(r.xs, r.ys)],
                    }
                    for r in msg.robots
                },
            }
            with _lock:
                _plan.clear()
                _plan.update(parsed)
                # ⚠️ **여기서 touch() 를 부르면 안 된다.** 이 토픽은 transient_local 이라
                #    fleet_node 가 죽은 뒤에도 우리가 재구독하면 **마지막 값이 다시 배달된다**.
                #    그걸 "방금 수신" 으로 세면 stale 이 영원히 false 로 남고, 아무도 지키지
                #    않는 예약을 화면이 계속 카운트다운한다. 생존 판정은 주기 발행 토픽
                #    (robot_state·occupancy·routes)에 맡긴다 — 그건 재전송이 없다.
                pass
            _notify()

        def on_routes(msg) -> None:
            parsed = {
                r.robot: [[float(x), float(y)] for x, y in zip(r.xs, r.ys)]
                for r in msg.routes
            }
            with _lock:
                _routes.clear()
                _routes.update(parsed)
                touch()
            _notify()

        def on_goals(msg) -> None:
            with _lock:
                _goals.clear()
                _goals.update(
                    {str(r): int(g) for r, g in zip(msg.robots, msg.goals)}
                )
                touch()
            _notify()

        node.create_subscription(TaskState, TOPIC_TASK_STATES, on_task_state, 10)
        node.create_subscription(RmfRobotState, TOPIC_ROBOT_STATE, on_robot_state, 10)
        node.create_subscription(FleetOccupancy, TOPIC_OCCUPANCY, on_occupancy, 10)
        # nav2 실주행 경로 — 어댑터가 로봇별 `/pinkyN/plan` 을 이름 붙여 넘긴다.
        node.create_subscription(String, "/robot_nav_path", on_nav_path, 10)
        global _hold_pub
        _hold_pub = node.create_publisher(RobotHold, TOPIC_HOLD, 10)
        node.create_subscription(FleetRoutes, TOPIC_ROUTES, on_routes, 10)
        # ⚠️ transient_local 로 발행하므로 **구독도 같은 durability** 여야 붙는다.
        #    reliable/volatile 로 잡으면 QoS 불일치로 조용히 한 건도 안 온다.
        node.create_subscription(
            FleetPlan, TOPIC_PLAN, on_plan,
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                       reliability=ReliabilityPolicy.RELIABLE))
        def on_path_request(msg) -> None:
            """fleet_node 가 허가한 다음 노드. 로봇 BT 로 넘긴다.

            `msg.path[0]` 은 로봇의 현재 위치(출발점)이고 그 뒤가 목적지다 —
            0번을 목표로 주면 nav2 가 거리 0 경로를 못 만들어 즉시 실패한다.
            """
            pts = [(float(p.x), float(p.y), float(p.yaw)) for p in msg.path]
            if len(pts) < 2:
                return
            _fire_path_request_hooks(str(msg.robot_name or ""), pts[1:])

        # 길잡이 요청자 가시성 — **로봇별 토픽**이다(payload 에 robot_id 가 없다).
        # 브릿지가 `/{key}/requester_visible` 로 remap 한다(domain_bridge.template.yaml).
        try:
            from std_msgs.msg import Bool as _Bool

            from app.fleet_telemetry import FLEET_ROBOTS
            for _cfg in FLEET_ROBOTS.values():
                _key = _cfg["key"]

                def _mk(name):
                    def _cb(msg, _n=name):
                        _fire_requester_visible(_n, bool(msg.data))
                    return _cb
                node.create_subscription(_Bool, f"/{_key}/requester_visible", _mk(_key), 10)
        except Exception as exc:    # noqa: BLE001 — 구독 실패로 링크 전체를 막지 않는다
            print(f"[fleet_link] requester_visible 구독 실패: {exc}", flush=True)

        node.create_subscription(FleetGoals, TOPIC_GOALS, on_goals, 10)
        node.create_subscription(PathRequest, TOPIC_PATH_REQUESTS, on_path_request, 10)

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
