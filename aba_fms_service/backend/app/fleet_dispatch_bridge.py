"""orchestrator(주문 상태기) ↔ fleet_node(배차·교통) 배선.

핸드오프 문서 6절 ① 이 여기다. 지금까지 orchestrator 는 `_stub_dispatch`(로그만) 로 돌아
로봇에 아무 명령도 가지 않았다. 이 모듈이 그 자리를 채운다.

## ⚠️ 알고리즘은 건드리지 않는다
배차(`Auction`)·교통(기본 `CbsTraffic`)은 pluginlib 플러그인이고 앞으로 바뀔 것이므로
**여기서는 연결만** 한다. 어느 로봇을 고를지는 `submit_task(robot="")` 로 넘겨 fleet_node 의
dispatcher 플러그인이 정하게 두고, 우리는 그 결과를 받아 다리 진행만 이어붙인다.

## 다리별 처리
- `NAVIGATE` → `fleet_link.submit_task(dropoff=waypoint, robot=robot)`.
  fleet 이 발급한 `task_id` 를 **orchestrator 의 cmd_id 로 그대로 쓴다** — 완료 신호가
  같은 id 로 돌아오므로 매핑 표가 따로 필요 없다.
- `PERFORM_ACTION` → **로봇 BT 로 보낸다.** `fleet_telemetry.send_command_async` 가
  `/fleet_cmd` 로 내려보내면 libi_modes 의 WorkingBranch 안 `ArmExec` 가 실행하고
  `/fleet_cmd_result` 로 회신한다. 팔이 아직 스텁이라도 경로는 진짜여서, 실제 팔이
  붙으면 이 코드는 그대로 둔 채 드라이버만 바뀌면 된다.
  링크가 없으면(브릿지 미기동·로봇 오프라인) 스텁으로 폴백한다 — 그때는 기본이
  **관제에서 사람이 넘기기**(`LIBI_ARM_AUTO=1` 이면 자동).

## 완료 신호 (두 갈래)
- 주행: `fleet_link` 의 task_state 훅. `COMPLETED`/`FAILED` 만 전달(진행 중은 무시).
- 팔:   `fleet_telemetry` 의 cmd_result 훅. 보낼 때 받은 id 를 그대로 cmd_id 로 써서
        매핑 표가 없다.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import math
import os
import threading
import time

from app import fleet_events, fleet_link, fleet_telemetry, fsm_link
from app.fleet_orchestrator import LegType
from app.node_block import NodeBlockRegistry

log = logging.getLogger("fleet_dispatch_bridge")

#: 팔이 스텁인 동안 팔 다리를 즉시 성공 처리할지. 기본 ON(그래야 시퀀스가 끝까지 돈다).
ARM_STUB = os.environ.get("LIBI_ARM_STUB", "1") != "0"
#: 팔 스텁이 "동작한 척" 하는 시간(초). 0 이면 dispatch 안에서 재진입할 수 있어 반드시 > 0.
ARM_STUB_DELAY_SEC = float(os.environ.get("LIBI_ARM_STUB_DELAY", "1.0"))

#: 팔 다리를 로봇 BT 로 보낼지. **기본 켜짐.**
#
# 로봇 BT(libi_modes)의 WorkingBranch 안에 `ArmExec` 가 이미 있고, `/fleet_cmd` 로 내려온
# 명령을 blackboard 에서 집어 실행한 뒤 `/fleet_cmd_result` 로 회신한다 — 이 경로가 원래
# 설계다(fleet_orchestrator_service 의 docstring 에도 그렇게 적혀 있다).
# 예전엔 그걸 우회해 파이썬 타이머로 "성공했다 치기" 를 했다. 그러면 로봇 FSM 이 WORKING
# 으로 전이되지 않아 관제의 상태·배차 판정이 실제와 어긋났고, BT 의 CommandTimeout·
# FaultDetected 같은 방어도 전혀 걸리지 않았다.
# 링크가 없으면(브릿지 미기동·로봇 오프라인) 아래 스텁 경로로 자동 폴백한다.
ARM_VIA_BT = os.environ.get("LIBI_ARM_VIA_BT", "1") == "1"

#: 팔 다리를 자동으로 완료 처리할지 (BT 경로가 없을 때의 스텁 동작).
#
# 예전엔 팔 다리가 1초 뒤 저절로 완료돼서, 화면에서 집기/놓기 단계가 순식간에 지나갔다.
# 팔이 실제로 붙기 전까지는 사람이 "지금 집었다"를 눈으로 확인하고 넘기는 편이 맞다.
# 켜려면 LIBI_ARM_AUTO=1 (로봇 없이 시퀀스만 빠르게 훑고 싶을 때).
#
# 끈 상태에서는 팔 다리가 대기로 남고, 관제 배차 화면의 **「현재 다리 완료 처리」**
# 버튼(POST /api/fleet/order/{id}/advance)이 다음으로 넘긴다.
ARM_AUTO = os.environ.get("LIBI_ARM_AUTO", "0") == "1"

_arm_seq = itertools.count(1)
#: 진행 중인 팔 스텁 타이머 — 프로세스 종료 시 남지 않게 참조를 들고 있는다.
_timers: set[threading.Timer] = set()

#: fleet_node 에 넘기는 task 이름 접두사. 이걸로 "우리가 낸 일"과 fleet_node 자체 순회(`P-…`)를
#  구분한다 — 화해(reconcile) 때 남의 일을 지우지 않기 위해 꼭 필요하다.
TASK_PREFIX = "orchestrator:"

#: 고아 task 화해 주기(초). 0 이면 끔.
RECONCILE_SEC = float(os.environ.get("LIBI_RECONCILE_SEC", "10"))
_reconcile_stop = threading.Event()
#: 직전 주기에 고아로 보였던 (로봇, task) — 연속 2회 관측돼야 실제로 정리한다.
_orphan_seen: set[tuple[str, str]] = set()

#: fleet_node 가 읽는 navgraph. 여기서도 같은 파일을 읽어 이름→인덱스를 만든다.
NAVGRAPH_PATH = os.environ.get(
    "LIBI_NAVGRAPH_FILE",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "fleet_ws/maps/library/arte2.navgraph.yaml",
    ),
)

_vertex_index: dict[str, int] | None = None
#: 정점 인덱스 → (x, y). `_load_vertex_index` 가 같은 파싱에서 함께 채운다.
_vertex_coords: list[tuple[float, float]] | None = None


def _load_vertex_index() -> dict[str, int]:
    """navgraph 의 `정점 이름 → 인덱스` 표(와 좌표 표 `_vertex_coords`).

    ⚠️ **왜 필요한가**: `fleet_node.cpp` 의 `on_submit` 은 `std::stoi(req->dropoff)` 로
    목적지를 **숫자 인덱스**로만 받는다(이름 조회 함수가 없다). 주문은 `문학-1` 같은
    waypoint 이름을 쓰므로, 여기서 인덱스로 바꿔 넘긴다.
    fleet_node(C++) 를 고치지 않기 위한 변환이며, navgraph 파일이 같으므로 인덱스가 일치한다.
    """
    global _vertex_index, _vertex_coords
    if _vertex_index is not None:
        return _vertex_index

    import yaml

    with open(NAVGRAPH_PATH) as f:
        data = yaml.safe_load(f)
    verts = data["levels"]["L1"]["vertices"]
    table: dict[str, int] = {}
    coords: list[tuple[float, float]] = []
    for i, v in enumerate(verts):
        # 정점 형식: [x, y, {name: '...'}]
        meta = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
        name = meta.get("name")
        if name:
            table[str(name)] = i
        coords.append((float(v[0]), float(v[1])))
    _vertex_index = table
    _vertex_coords = coords
    log.info("[dispatch] navgraph 정점 %d개 로드: %s", len(table), NAVGRAPH_PATH)
    return table


def vertex_at(x: float, y: float, tol: float = 0.05) -> int | None:
    """world 좌표에 해당하는 navgraph 정점 인덱스. `tol`(m) 안에 없으면 `None`.

    [Critical 1] `on_path_request` 가 fleet_node 로부터 받는 홉은 좌표뿐이라(정점
    번호가 없다) 여기서 되돌린다. navgraph 가 작아(수십 개 정점) 선형 스캔으로 충분하다.
    """
    _load_vertex_index()   # _vertex_coords 를 채운다(이름 표를 안 써도 같은 캐시를 탄다)
    if not _vertex_coords:
        return None
    best_i, best_d = None, tol
    for i, (vx, vy) in enumerate(_vertex_coords):
        d = ((vx - x) ** 2 + (vy - y) ** 2) ** 0.5
        if d <= best_d:
            best_i, best_d = i, d
    return best_i
def vertex_xy(idx: int) -> tuple[float, float] | None:
    """정점 인덱스 → (x, y). [Critical 2] 사람 차단 뒤 후진 목표 좌표를 구할 때 쓴다."""
    _load_vertex_index()
    if _vertex_coords is None or not (0 <= idx < len(_vertex_coords)):
        return None
    return _vertex_coords[idx]


#: 정점 인덱스 → [(이웃, 거리 m), ...]. lane 은 **방향 간선**이라 yaml 이 `[a,b]` 와
#: `[b,a]` 를 따로 적는다 — 여기서도 적힌 방향만 넣는다(무향으로 뒤집으면 일방통행이 사라진다).
_lane_adj: dict[int, list[tuple[int, float]]] | None = None


def _load_lanes() -> dict[int, list[tuple[int, float]]]:
    """navgraph 의 방향 간선 표. 비용은 두 정점 사이 직선거리(m)."""
    global _lane_adj
    if _lane_adj is not None:
        return _lane_adj

    import yaml

    _load_vertex_index()                       # _vertex_coords 를 채운다
    coords = _vertex_coords or []
    with open(NAVGRAPH_PATH) as f:
        data = yaml.safe_load(f)
    adj: dict[int, list[tuple[int, float]]] = {}
    for lane in data["levels"]["L1"].get("lanes") or []:
        a, b = int(lane[0]), int(lane[1])
        if not (0 <= a < len(coords) and 0 <= b < len(coords)):
            continue
        (ax, ay), (bx, by) = coords[a], coords[b]
        adj.setdefault(a, []).append((b, math.hypot(bx - ax, by - ay)))
    _lane_adj = adj
    log.info("[dispatch] navgraph 간선 %d개 로드", sum(len(v) for v in adj.values()))
    return adj


def path_cost(start: int, goal: int) -> float | None:
    """`start` → `goal` 최단 주행거리(m). 도달 불가면 None.

    **배차 입찰가가 이 값이다.** fleet_node 의 `Navgraph::dijkstra` + `path_cost` 와 같은
    것을 파이썬에서 재현한다 — 정점이 22개뿐이라 힙 하나로 충분하다.

    ⚠️ 홉 수가 아니라 **미터**다. arte2 는 레인 길이 편차가 커서(최소 0.062m) 홉 수로
       세면 짧은 레인을 여러 번 지나는 로봇이 부당하게 불리해진다.
    """
    if start == goal:
        return 0.0
    adj = _load_lanes()
    dist: dict[int, float] = {start: 0.0}
    heap: list[tuple[float, int]] = [(0.0, start)]
    while heap:
        d, v = heapq.heappop(heap)
        if v == goal:
            return d
        if d > dist.get(v, float("inf")):
            continue                            # 낡은 항목
        for w, cost in adj.get(v, ()):
            nd = d + cost
            if nd < dist.get(w, float("inf")):
                dist[w] = nd
                heapq.heappush(heap, (nd, w))
    return None


def resolve_vertex(name: str) -> int:
    """waypoint 이름 → 정점 인덱스. 숫자를 그대로 준 경우도 허용한다."""
    name = str(name).strip()
    if name.lstrip("-").isdigit():
        return int(name)
    table = _load_vertex_index()
    if name not in table:
        raise RuntimeError(
            f"navgraph 에 없는 정점: {name!r} "
            f"(navgraph={os.path.basename(NAVGRAPH_PATH)}, 정점 {len(table)}개)"
        )
    return table[name]


def vertex_name(value) -> str:
    """팔 leg 의 `at` 을 **정점 이름**으로 만든다. 숫자 인덱스면 navgraph 에서 이름을 찾는다.

    왜 필요한가: 로봇 쪽 중계가 `at` **이름**에서 장소 종류를 유도한다(`*서가`→서가,
    `*테이블`→테이블). 숫자를 그대로 보내면 유도가 실패하고 팔 goal 이 아예 안 나간다.
    상위가 이름을 주는 것이 정상 경로지만(`ops.py` 는 zone 이름을 준다), API 는 숫자도
    받으므로 여기서 한 번 되돌린다.

    못 찾으면 **원래 값을 그대로 돌려준다** — 여기서 이름을 지어내면 정본이 둘이 된다.
    """
    s = str(value or "").strip()
    if not s.lstrip("-").isdigit():
        return s
    try:
        for name, idx in _load_vertex_index().items():
            if idx == int(s):
                return name
    except Exception:  # noqa: BLE001 — navgraph 를 못 읽어도 배차는 계속돼야 한다
        log.warning("[dispatch] navgraph 를 못 읽어 정점 %s 의 이름을 못 찾았다", s)
    return s


def _orc():
    # 순환 import 를 피하려고 호출 시점에 가져온다.
    from app import fleet_orchestrator_service as svc

    return svc.orchestrator()


def _complete_arm_later(cmd_id: str) -> None:
    def fire() -> None:
        try:
            _orc().on_result(cmd_id, True, "arm-stub")
        except Exception:  # noqa: BLE001
            log.exception("[arm-stub] on_result 실패 cmd=%s", cmd_id)
        finally:
            _timers.discard(t)

    t = threading.Timer(ARM_STUB_DELAY_SEC, fire)
    t.daemon = True
    _timers.add(t)
    t.start()


#: 팔 다리 하나가 도는 최대 시간. 이 안에 안 끝나면 fleet_node 가 붙잡기를 스스로 푼다
#  (경고와 함께). 로봇이 영영 순회를 못 하는 것보다 낫다.
HOLD_TTL_SEC = float(os.environ.get("LIBI_ARM_HOLD_TTL_SEC", "180"))

#: 붙잡은 cmd_id → 로봇. 결과가 올 때 누구를 풀지 알아야 한다(결과에는 로봇이 없다).
_held_by_cmd: dict[str, str] = {}


def _robot_of_cmd(cmd_id: str) -> str:
    return _held_by_cmd.pop(cmd_id, "")


def _release_hold(robot: str) -> None:
    """로봇의 붙잡기를 풀고 역인덱스도 정리한다."""
    if not robot:
        return
    fleet_link.set_robot_hold(robot, False)
    for k, v in list(_held_by_cmd.items()):
        if v == robot:
            _held_by_cmd.pop(k, None)


#: 정밀 도킹 대상 서가. 셋 다 navgraph 에 yaw 가 없다(arte2.navgraph.yaml) — 도착
#: 자세는 shelf_dock.py 자체의 보정 회전(SHELF_YAW 기준)이 맡는다. 예술서가는
#: 2026-08-05까지 이 목록에서 빠져 있어 실주문으로 도킹이 전혀 안 나갔다(실측:
#: shelf_dock_status 가 시도 전 구간 내내 "waiting" — 로봇은 도착만 하고 끝남).
#: SHELF_YAW["예술서가"] 는 아직 실측 전 임시값이다(shelf_dock.py 참고) — 실기로
#: 확인 전까지는 첫 회전이 부정확할 수 있다.
SHELF_NODES = ("문학서가", "과학-인문학서가", "예술서가")

#: 서가 도킹(+팔 작업)이 끝나기까지 정점 잠금과 로봇 hold 에 함께 쓰는 TTL. **반드시
#  같은 값**이어야 한다 — 어긋나면 한쪽이 먼저 풀려 그 사이 재배차/재진입이 들어온다.
SHELF_DOCK_TTL_SEC = float(os.environ.get("LIBI_SHELF_DOCK_TTL_SEC", "180"))

#: [Critical 3] fleet_node 의 fleet task_id → (robot, shelf, node). 서가 정점으로
#  **정상 주행**(submit_task)을 태운 뒤, `on_task_state` 의 COMPLETED(도착)를 기다렸다가
#  그때서야 `shelf_dock` 을 낸다 — 배차 즉시 로봇 BT 로 직행하면(라운드 3의 실수)
#  로봇이 도착도 안 한 채 도킹 절차를 시작한다.
_shelf_pending: dict[str, tuple[str, str, int]] = {}

#: shelf_dock 의 cmd_id → (원래 NAVIGATE 다리의 fleet task_id, shelf 이름). 도킹 결과가
#  오면 이걸로 **원래 다리**를 닫는다(`on_shelf_dock_phase` 의 "done" 도 같이 낸다).
_shelf_dock_wait: dict[str, tuple[str, str]] = {}



def real_dispatch(task_id: str, robot: str, leg) -> str:
    """orchestrator 가 다리 하나를 내보낼 때 부른다. 반환값이 cmd_id."""
    # ── 주행이 아닌 다리(팔) 동안은 로봇을 **붙잡는다** ────────────────────────
    #
    # fleet_node 는 주행 다리만 안다. 팔 다리는 여기서 로봇에 직접 쏘므로 fleet_node 는
    # 그 로봇이 유휴라고 보고 **순회를 새로 걸어 버린다** — 실측으로 서가에 막 도착한
    # 로봇이 150 ms 뒤 떠났다. 팔이 그 서가에서 집어야 하는데.
    #
    # 그동안 이걸 막은 것은 로봇 상태기계가 WORKING 이라 순회 조건이 안 맞았던 것뿐이라,
    # 그 링크가 끊기면 그대로 사고가 난다. 붙잡기를 주문 계약으로 끌어올린다.
    if robot and leg.type != LegType.NAVIGATE:
        fleet_link.set_robot_hold(robot, True, HOLD_TTL_SEC)
    if leg.type == LegType.NAVIGATE:
        waypoint = str(leg.params.get("waypoint", ""))
        if not waypoint:
            raise RuntimeError(f"{task_id}: NAVIGATE 다리에 waypoint 가 없다")

        # fleet_node 는 숫자 인덱스만 받는다(위 resolve_vertex 주석 참고).
        goal_idx = resolve_vertex(waypoint)

        # [Critical 3] 서가 정점도 **정상적으로 주행을 태운다.** 라운드 3 은 여기서
        # fleet_node(submit_task)를 우회해 로봇에 `shelf_dock` 을 바로 쐈는데, 그러면
        # 앞선 주행이 없으니 **로봇이 그 자리에 선 채로 도킹을 시작한다**(실측 지적).
        # 잠금·hold 는 배차 시점 그대로 걸어 둔다(다른 로봇이 오면 안 되니까) — 실제
        # `shelf_dock` 발행은 `on_task_state` 가 도착(COMPLETED)을 본 뒤로 미룬다.
        is_shelf = waypoint in SHELF_NODES
        if is_shelf:
            # ⚠️ `LegType.NAVIGATE` 라 위 팔 hold 가드(`leg.type != NAVIGATE`)를 안
            #    탄다 — 그런데 도착 뒤 도킹이 끝날 때까지는 fleet_node 가 이 태스크를
            #    COMPLETED 로 보고(=유휴로 보임) `RobotHold.msg` 가 막으려는 바로 그
            #    상황이 생긴다. 팔 다리와 똑같이 hold 를 건다.
            if robot:
                fleet_link.set_robot_hold(robot, True, SHELF_DOCK_TTL_SEC)
            on_shelf_dock_lock(robot, goal_idx, SHELF_DOCK_TTL_SEC)

        res = fleet_link.submit_task(
            dropoff=str(goal_idx),
            robot=robot or "",
            task_type="delivery",
            requester=f"{TASK_PREFIX}{task_id}",
        )
        if not res.get("accepted"):
            # 배차 실패는 예외로 올린다 — orchestrator 가 재시도/FAILED 를 판단한다.
            raise RuntimeError(
                f"fleet_node 가 거절: {res.get('reason') or 'unknown'}"
            )
        cmd_id = res.get("task_id") or ""
        if not cmd_id:
            raise RuntimeError("fleet_node 가 task_id 를 주지 않았다")

        # [Critical 1] is_destination 판정용 — **어느 다리의 목적지인지 같이 기억한다.**
        # 예전엔 정점 번호만 넣고 아무 데서도 안 지웠다. 배달이 끝난 뒤 순회를 돌면
        # 그 낡은 목적지와 같은 정점에서만 `is_destination=True` 가 되어, 순회 중
        # **그 노드 하나만 조용히 차단 보고가 안 나갔다**(2026-08-03). cmd_id 를 같이
        # 들고 있다가 `on_task_state` 에서 그 다리가 닫힐 때 지운다.
        _nav_goal[_rkey(robot)] = (str(cmd_id), goal_idx)

        if is_shelf:
            # 도착(TaskState COMPLETED)은 `on_task_state` 가 안다 — 거기서 마저 진행한다.
            _shelf_pending[str(cmd_id)] = (robot, waypoint, goal_idx)

        log.info(
            "[dispatch] %s NAVIGATE→%s(v%d) robot=%s fleet_task=%s%s",
            task_id, waypoint, goal_idx, robot or "(경매)", cmd_id,
            " (서가 도킹 대기)" if is_shelf else "",
        )
        return cmd_id

    if leg.type == LegType.PERFORM_ACTION:
        action = leg.params.get("action", "?")
        if action == "backup":
            # 이 명령은 팔 BT가 아니라 FMS가 배정한 robot_agent 복귀 다리다.
            cmd_id = fleet_telemetry.send_command_async(robot, action="backup", args={})
            if not cmd_id:
                raise RuntimeError("FMS backup 명령 전송 실패")
            _held_by_cmd[str(cmd_id)] = robot
            log.info("[backup] %s FMS 승인 복귀 다리 배정 robot=%s cmd=%s", task_id, robot, cmd_id)
            return cmd_id
        # 숫자 인덱스로 온 정점도 이름으로 되돌린다 — 중계가 이름에서 장소를 유도한다.
        where = vertex_name(leg.params.get("at", "")) or "?"
        if not ARM_STUB:
            raise RuntimeError("팔 배선 없음 (LIBI_ARM_STUB=0)")
        # ── 1순위: 로봇 BT 로 보낸다 ──────────────────────────────────────
        if ARM_VIA_BT:
            # ⚠️ [2026-07-30] **키를 하나하나 명시적으로 옮긴다.** 예전에는
            #    `action`·`book`·`at` 셋만 복사했고, orchestrator 가 leg 에 새 키를 넣어도
            #    **여기서 조용히 사라졌다.** 팔 계약(`object`/`from_place`/`to_place`/
            #    `tier`/`row`/`slot`)을 추가할 때 이 줄을 같이 고쳐야 하는 이유다.
            #
            #    `to_place` 는 `place` 다리에서 비어 있을 수 있다 — 목적지가 테이블인지
            #    안내데스크인지는 정점의 정체를 알아야 정해지고, 그 지식은 orchestrator 에
            #    없다. 로봇 쪽 중계(`libi_modes/arm_task_map.py`)가 `at` 에서 유도한다.
            #    빈 값을 보내는 것이 맞다 — 여기서 추측해 채우면 정본이 둘이 된다.
            arm_args = {"action": action, "at": where,
                        "book": leg.params.get("book", ""),
                        "object": leg.params.get("object", ""),
                        "from_place": leg.params.get("from_place", ""),
                        "to_place": leg.params.get("to_place", ""),
                        "tier": int(leg.params.get("tier", 0) or 0),
                        "row": int(leg.params.get("row", 0) or 0),
                        "slot": int(leg.params.get("slot", 0) or 0)}
            cmd_id = fleet_telemetry.send_command_async(
                robot, action="perform_action", args=arm_args,
            )
            if cmd_id:
                _held_by_cmd[str(cmd_id)] = robot
                log.info(
                    "[arm] %s NAVIGATE→BT %s(%s) at %s robot=%s cmd=%s",
                    task_id, action, leg.params.get("book", ""), where, robot, cmd_id,
                )
                return cmd_id
            log.warning(
                "[arm] %s 로봇 명령 링크 없음(브릿지 미기동/오프라인) → 스텁으로 폴백", task_id,
            )

        # ── 폴백: 스텁 ────────────────────────────────────────────────────
        cmd_id = f"arm-{next(_arm_seq)}"
        if ARM_AUTO:
            log.info(
                "[arm-stub] %s(%s) at %s — 자동 완료 처리 cmd=%s (LIBI_ARM_AUTO=1)",
                action, leg.params.get("book", ""), where, cmd_id,
            )
            _complete_arm_later(cmd_id)
        else:
            log.info(
                "[arm-stub] %s(%s) at %s — **관제에서 넘길 때까지 대기** cmd=%s "
                "(배차 화면의 「현재 다리 완료 처리」)",
                action, leg.params.get("book", ""), where, cmd_id,
            )
        return cmd_id

    raise RuntimeError(f"알 수 없는 다리 종류: {leg.type}")


def on_task_state(payload: dict) -> None:
    """fleet_link 훅 — fleet task 가 끝나면 orchestrator 에 알린다.

    [Critical 3] 서가 정점으로 가는 다리(`_shelf_pending`)는 여기서 COMPLETED 를 봐도
    **다리를 바로 안 닫는다** — 도착했을 뿐 정밀 도킹은 이제 시작이다. `shelf_dock`
    을 내고, 그 결과가 오면(`on_cmd_result`) 그때 이 cmd_id 를 닫는다.
    """
    state = (payload.get("state") or "").upper()
    if state not in ("COMPLETED", "FAILED", "REJECTED"):
        return  # ASSIGNED/EXECUTING 은 진행 중
    cmd_id = str(payload.get("task_id") or "")
    if not cmd_id:
        return
    ok = state == "COMPLETED"
    log.info("[dispatch] fleet task %s → %s", cmd_id, state)

    # 이 다리가 세운 목적지 기억을 지운다 — 안 지우면 그 뒤 순회 중에 낡은 목적지가
    # `is_destination` 을 True 로 만들어 그 정점의 차단 보고가 조용히 사라진다.
    for r, entry in list(_nav_goal.items()):
        if entry[0] == cmd_id:
            _nav_goal.pop(r, None)

    pending = _shelf_pending.pop(cmd_id, None)
    if pending is not None:
        robot, shelf, node = pending
        if not ok:
            # 주행 자체가 실패/거절됐다 — 도킹을 시작할 수 없다. 다리를 그대로 닫는다.
            try:
                _orc().on_result(cmd_id, False, state)
            except Exception:  # noqa: BLE001
                log.exception("[shelf] on_result 실패 cmd=%s", cmd_id)
            return
        # 도착했다 — 이제 정밀 도킹을 시작한다.
        on_shelf_dock_phase(robot, shelf, "started")   # [Important 5]
        dock_cmd_id = fleet_telemetry.send_command_async(
            robot, action="shelf_dock", args={"shelf": shelf, "node": node},
        )
        if not dock_cmd_id:
            log.warning(
                "[shelf] %s 도킹 명령 전송 실패(로봇 명령 링크 없음) — 다리를 실패 처리한다",
                robot,
            )
            try:
                _orc().on_result(cmd_id, False, "shelf_dock_send_failed")
            except Exception:  # noqa: BLE001
                log.exception("[shelf] on_result 실패 cmd=%s", cmd_id)
            return
        # 팔 다리와 같은 방식으로 등록 — `on_cmd_result` 가 이 cmd_id 의 결과를 받으면
        # `_robot_of_cmd` 로 이 로봇을 찾아 hold 를 푼다(새 메커니즘을 안 만든다).
        _held_by_cmd[str(dock_cmd_id)] = robot
        _shelf_dock_wait[str(dock_cmd_id)] = (cmd_id, shelf)
        log.info("[shelf] %s 도착 → shelf_dock 발행 cmd=%s (원 다리 cmd=%s 는 대기)",
                 robot, dock_cmd_id, cmd_id)
        return

    try:
        _orc().on_result(cmd_id, ok, state)
    except Exception:  # noqa: BLE001
        log.exception("[dispatch] on_result 실패 cmd=%s", cmd_id)


#: 주행을 로봇 BT 로 보낼지. **기본 켜짐.**
#
# 끄면(0) fleet_node 의 PathRequest 를 로봇 쪽 path_request_driver 가 직접 받아 nav2 로
# 넣는 예전 경로가 된다. 그 경로는 **로봇 BT 를 우회**해서 FSM 이 WORKING 으로 가지 않고,
# 관제가 배달 중인 로봇을 "배차 가능"으로 표시한다.
NAV_VIA_BT = os.environ.get("LIBI_NAV_VIA_BT", "1") == "1"

#: 같은 목적지를 다시 내려보내기 전에 기다리는 시간(초).
#
# fleet_node 는 이동 중 같은 경로를 ~1초마다 재발행한다(놓친 명령 자가 복구). 그대로
# 흘려보내면 nav2 목표가 매초 선점돼 주행이 끊긴다. 그래서 걸러낸다.
#
# 그런데 **영원히** 걸러내면 반대쪽 고장이 생긴다. nav2 주행은 로봇이 도착하지 않은 채
# 끝날 수 있다 — ABORTED 이거나, 선점 순간 직전 목표의 완료가 새 목표의 완료로
# 보고되거나(실측: 로봇이 v1 에 선 채 v9 목표가 0.43초 만에 "Reached the goal!").
# BT 의 `NavigationExec` 은 명령이 **접수**되면 끝나므로 그 사실을 알지 못하고,
# fleet_node 의 재발행은 여기서 막혀 있으니, 로봇은 아무도 다시 몰지 않아 그대로 선다.
# 실측으로 6분 40초를 서 있었다.
#
# 그래서 재발행을 **느린 심박**으로 쓴다: 같은 목적지라도 이 시간이 지나면 한 번 더
# 보낸다. 정상 홉은 2~17초라 보통은 재전송 없이 끝나고, 멈춘 로봇만 다시 출발한다.
#
# ⚠️ [2026-08-06 실측] pinky-3 순회 로그로 보면 정상 홉이 실제로는 20~21초 걸렸다 —
#   20초였던 이전 값 바로 위 경계라 거의 매 홉마다 걸렸다(멀쩡히 가던 중에 nav2 goal
#   이 다시 나가 "중간중간 끊김"으로 관찰됨). 여유를 두고 45초로 올린다.
#
# ⚠️ `libi_modes` 의 `NavigationExec` 에도 재전송(`arrive_resend_sec`)이 있다.
#    **중복이 아니라 서로 다른 고장을 막는다** — 지우기 전에 읽을 것:
#      여기(FMS)  : 명령이 **로봇에 닿지 않은** 경우 (DDS 유실, 로봇 늦게 뜸,
#                   로봇이 아직 WORKING 이 아니라 NavigationExec 이 안 도는 동안)
#      BT 쪽      : 명령은 닿았는데 **주행이 도착 없이 끝난** 경우 (nav2 ABORTED 등)
#    BT 는 자기가 못 받은 명령을 다시 보낼 수 없고, FMS 는 로봇이 도착했는지 모른다.
#    같은 목적지가 다시 와도 BT 는 목적지가 안 바뀌었으면 goal 을 새로 내지 않으므로
#    둘이 겹쳐도 주행을 끊지 않는다.
NAV_RESEND_SEC = float(os.environ.get("LIBI_NAV_RESEND_SEC", "45"))

#: 로봇별 (마지막 목적지, 보낸 시각).
_last_nav: dict[str, tuple] = {}
_nav_lock = threading.Lock()

#: [Critical 1] 로봇별 **현재 NAVIGATE 다리의 최종 목적지** 정점. `real_dispatch` 가
#  다리를 낼 때 적어 두고, `on_path_request` 가 이번 홉과 비교해 `is_destination` 을 판정한다.
#: robot → (그 목적지를 세운 다리의 cmd_id, 목적지 정점). 다리가 닫히면 지운다 —
#: 안 지우면 순회 중에 낡은 목적지가 `is_destination` 을 True 로 만들어 그 정점의
#: 차단 보고가 조용히 사라진다.
_nav_goal: dict[str, tuple[str, int]] = {}

# ⚠️ [2026-08-03] `_prev_granted`/`_last_granted` 를 **없앴다.**
#
#   fleet_node(C++) 의 커밋 경로를 파이썬 쪽에서 다시 구성해 두고, 사람 차단 때
#   "떠나온 직전 정점" 으로 후퇴를 쐈던 표다. 그 후퇴가 **예약 체계 밖**이라
#   충돌 창을 만들었고(codex 3차 P0), 되돌리는 일은 이제 `fleet_node` 가
#   `/fms/node_block` 콜백에서 예약을 원자적으로 갈아타며 직접 한다.
#   같은 사실을 두 곳에서 들고 있으면 반드시 어긋난다 — 그래서 지운다.


def _rkey(robot: str) -> str:
    """로봇 이름을 **표기 차이를 지운 하나의 키**로 바꾼다.

    ⚠️ [2026-08-03] 이 세 표를 **서로 다른 이름으로 읽고 쓰고 있었다.**

      · `on_path_request` 는 fleet_node 가 준 이름(`pinky-3`)으로 **쓴다**
      · `_on_node_block_report` 는 토픽 접두사(`pinky3`)로 **읽는다**

    그래서 사람 차단이 와도 `_prev_granted.get("pinky3")` 이 영원히 `None` 이었고,
    후퇴 명령이 **한 번도 안 나갔다**(실기 로그:
    `[backup] pinky3 직전 정점을 몰라 후진 명령을 못 낸다`, 11:35:48 · 11:50:07).
    후퇴가 죽어 있으니 로봇은 사람 앞에 그대로 서 있고, 그 자리에서 재계획을 해 봐야
    nav2 가 경로를 못 만든다(`Failed to create plan with tolerance of: 0.100000`).

    `fsm_link.match_key` 가 이미 같은 일을 한다 — 캐시 키 표기 흡수용으로 있던 것을
    여기서도 쓴다. 새 규칙을 만들면 그 규칙이 또 어긋난다.
    """
    return fsm_link.match_key(robot or "")
def _current_dest_node(robot: str, action: str) -> int | None:
    """이번 홉이 향하는 다리/안내의 **최종 목적지** 정점. `is_destination` 판정용.

    `guide` 는 orchestrator 다리가 아니라 `fleet_link.guide_target` 에 등록된 목표
    좌표를 쓴다(길잡이는 `_nav_goal` 을 채우는 `real_dispatch` 를 거치지 않는다).
    """
    if action == "guide":
        target = fleet_link.guide_target(robot) or {}
        if "x" in target and "y" in target:
            return vertex_at(float(target["x"]), float(target["y"]))
        return None
    entry = _nav_goal.get(_rkey(robot))
    return entry[1] if entry else None


def should_send_nav(robot: str, key: tuple, now: float) -> bool:
    """이 목적지를 지금 보낼까. 보내기로 하면 기억을 갱신한다.

    순수한 판단이 아니라 상태를 바꾸는 게 맞다 — 판단과 기록이 갈라지면 두 요청이
    동시에 통과한다.
    """
    with _nav_lock:
        last = _last_nav.get(robot)
        if last is not None and last[0] == key and now - last[1] < NAV_RESEND_SEC:
            return False                # 같은 목적지 재발행 — 주행을 끊지 않는다
        _last_nav[robot] = (key, now)
        return True


def _has_admin_follow(robot: str) -> bool:
    """이 로봇에 관리자 추종 승인이 살아 있나.

    import 를 함수 안에서 한다 — 모듈 최상단에서 라우터를 끌어오면 순환 import 가 된다
    (라우터가 fleet_telemetry 를, 이 모듈도 fleet_telemetry 를 쓴다).
    """
    try:
        from app.routers import admin_follow
        with admin_follow._grants_lock:
            return robot in admin_follow._grants
    except Exception:       # noqa: BLE001 — 조회 실패로 배차를 막지 않는다
        return False


def _has_guide(robot: str) -> bool:
    """길잡이가 nav2를 직접 소유하는 동안 fleet 경로를 BT에 내리지 않는다."""
    try:
        return fleet_link.has_guide_target(robot)
    except Exception:       # noqa: BLE001 -- 상태 조회 실패는 기존 배차 동작을 보존한다
        return False


#: 길잡이 홉 id → 로봇. 소실 보고(`requester_lost`)가 어느 로봇 것인지 알려면 필요하다.
#  `_held_by_cmd` 를 못 쓰는 이유: 그건 붙잡기를 건 팔 다리만 담는다(홉은 안 담긴다).
#  ⚠️ 무한히 자라면 안 된다 — 결과가 유실된 홉이 남으므로 상한을 두고 오래된 것부터 버린다.
_guide_hop_robot: dict[str, str] = {}
_GUIDE_HOP_MAX = 256
#: 요청자를 놓친 동안 순회 재배차를 막아 두는 시간(초).
#  회복 BT 한 라운드(≈32.8초) + 대기(20초)보다 넉넉해야 회복 도중에 풀리지 않는다.
GUIDE_RECOVER_HOLD_TTL = float(os.environ.get("LIBI_GUIDE_RECOVER_HOLD_TTL", "300"))
#: 회복 중 fleet_node 에 다시 세울 모드. **로봇 FSM 과 같은 값**이어야 한다 —
#  PATROL 을 밀면 fleet 쪽이 순회를 시작할 수 있다(`_pause_guide_reservation` 주석).
GUIDE_PAUSE_MODE = "WORKING"


def _remember_guide_hop(cmd_id: str, robot: str) -> None:
    if not cmd_id or not robot:
        return
    _guide_hop_robot[cmd_id] = robot
    while len(_guide_hop_robot) > _GUIDE_HOP_MAX:
        _guide_hop_robot.pop(next(iter(_guide_hop_robot)), None)


#: `GuideExec._report_lost` 가 싣는 문구. 양쪽이 같아야 한다 — 다르면 조용히 안 걸린다.
GUIDE_LOST_MSG = "requester_lost"


def _guide_target_name(robot: str) -> str:
    """승인 때 등록해 둔 안내 목적지 이름. 없으면 빈 문자열."""
    try:
        return str((fleet_link.guide_target(robot) or {}).get("name", ""))
    except Exception:       # noqa: BLE001 — 표시값이라 조회 실패로 주행을 막지 않는다
        return ""


def on_path_request(robot: str, points: list) -> None:
    """fleet_node 가 허가한 다음 노드를 로봇 BT 로 내려보낸다.

    `points` 는 출발점을 뺀 목적지 열이다(fleet_link 가 이미 잘랐다). 노드 단위 예약이라
    보통 1개다.

    ⚠️ **다리 완료로 쓰지 않는다.** 주행 다리 하나는 여러 노드를 지나므로, `/fleet_cmd`
    결과(홉 단위)로 다리를 끝내면 첫 홉에서 완료돼 버린다. 다리 완료는 계속
    fleet_node 의 TaskState 가 정본이다.
    """
    if not NAV_VIA_BT or not robot or not points:
        return

    # 관리자 추종 중인 로봇에는 **주행을 배차하지 않는다.**
    #
    # 추종은 FSM 을 거치지 않고 ai_service↔로봇 직결로 돌아서, fleet_node 는 이 로봇이
    # 사람을 따라가는 중이라는 것을 모른다. 그래서 순회 경로 요청이 계속 들어오고,
    # 여기서 `navigate` 를 내보내면 로봇 쪽에서 이렇게 무너진다:
    #
    #   providers 가 active_command 를 "navigate" 로 덮음
    #     → dispatch Selector 에서 FollowExec 이 밀려남
    #     → terminate(INVALID) → driver.stop() → `stop-follow_admin-N`
    #     → follow_node 가 그 id 로 세션을 닫는다
    #
    # 실측 2026-07-28: 추종 시작 20초 뒤 재배차가 들어와 세션이 끊겼다. 화면은
    # "추종 중"인데 로봇은 사람을 안 따라온다 — 왜 끊겼는지 어디에도 안 남는다.
    #
    # 승인 기록(grant)이 이 상태를 아는 유일한 곳이다(admin_follow 머리말 참고).
    if _has_admin_follow(robot):
        log.info("[nav] %s 관리자 추종 중 — 주행 배차를 보류한다", robot)
        return
    # ⚠️ [2026-08-02] **길잡이도 홉을 받는다 — 액션 이름만 다르다.**
    #
    #   예전엔 여기서 `return` 해 길잡이를 교통관제 밖에 뒀다. 그러면 `GuideExec` 이
    #   최종 목적지까지 혼자 몰고 가서, 예약도 간선 잠금도 재할당도 걸 자리가 없었고
    #   관제 지도에는 선이 **하나도** 안 그려졌다(계획 대상이 아니므로).
    #
    #   지금은 같은 홉을 `guide` 로 내려보낸다. 로봇 쪽은 이미 준비돼 있다 —
    #   `providers._on_cmd` 가 `guide` 의 args 를 `NAV_TARGET` 에 넣고
    #   `active_command` 는 `"guide"` 로 **유지**하므로(GuideExec.handles 와 같아야 한다),
    #   `GuideExec` 이 NavigationExec 의 재조준 경로를 그대로 타면서 사람 감시 게이트를
    #   유지한다.
    #
    #   ⚠️ **`navigate` 로 보내면 안 된다.** 그러면 `active_command` 가 "navigate" 가 돼
    #      dispatch Selector 앞의 `NavigationExec` 이 가로채고, `GuideExec` 은 영영 안
    #      돈다 — 요청자를 놓쳐도 아무도 멈추지 않는다.
    action = "guide" if _has_guide(robot) else "navigate"

    x, y, yaw = points[-1]
    if not should_send_nav(robot, (round(x, 3), round(y, 3)), time.monotonic()):
        return

    # [Critical 1] `node`/`is_destination` 없이 보내면 로봇의 `PersonBlockGuard` 가
    # `node is None` 이라 **조용히 아무것도 안 한다** — 사람이 아무리 오래 막아도
    # 차단 보고가 한 번도 안 나간다. 좌표→정점은 이 홉이 이미 navgraph 정점이라는
    # 전제(fleet_node 가 정점 단위로 예약한다)로 역으로 찾는다.
    node = vertex_at(x, y)
    if node is None:
        log.warning(
            "[nav] %s 좌표 (%.3f, %.3f) 에 대응하는 navgraph 정점을 못 찾았다 — "
            "node/is_destination 없이 보낸다(로봇의 사람-차단 보고가 죽는다)",
            robot, x, y,
        )

    args = {"x": x, "y": y, "yaw": yaw}
    if node is not None:
        args["node"] = node
        args["is_destination"] = node == _current_dest_node(robot, action)
    if action == "guide":
        # 목적지 이름은 승인 때 등록해 둔 것을 그대로 싣는다 — 화면이 "어디로 안내 중"
        # 을 그 값으로 띄운다. 없으면 빈 문자열이라 표시만 비고 주행은 정상이다.
        args["name"] = _guide_target_name(robot)

    cmd_id = fleet_telemetry.send_command_async(robot, action=action, args=args)
    if cmd_id:
        if action == "guide":
            _remember_guide_hop(cmd_id, robot)
        log.info("[nav] %s → BT %s (%.3f, %.3f) cmd=%s", robot, action, x, y, cmd_id)
    else:
        log.warning("[nav] %s 주행 명령 전송 실패 (명령 링크 없음)", robot)


#: task 수명주기 → 로봇 미션 FSM 전이 신호 (libi_modes registry.py 의 트리거 이름).
#   task_assigned : IDLE/PATROL/INTERACTING → WORKING
#   task_done     : WORKING → PATROL
#   task_failed   : WORKING → PATROL
LIFECYCLE_ACTIONS = {"start": "task_assigned", "done": "task_done", "failed": "task_failed"}


def real_lifecycle(robot: str, phase: str) -> None:
    """주문 시작/끝을 로봇에 알려 미션 FSM 을 WORKING 으로 넣고 뺀다.

    ⚠️ **왜 필요한가**: libi_modes 의 `WorkingBranch` 는 맨 앞이 `IsMode("WORKING")` 이라,
    로봇이 WORKING 이 아니면 팔 명령(ArmExec)이 아예 실행되지 않는다. 그런데 주행은
    fleet_node 가 직접 nav2 를 몰기 때문에 로봇은 계속 IDLE/PATROL 로 남아 있었다
    (실측: 관제에 state=IDLE 인 채로 주문이 진행됐다).

    **task 단위**로만 보낸다 — 다리마다 보내면 주행 중에 WORKING 을 들락거린다.
    링크가 없으면 조용히 넘어간다(주행 자체는 fleet_node 가 하므로 진행은 막지 않는다).
    """
    action = LIFECYCLE_ACTIONS.get(phase)
    if not action:
        return
    if phase in ("done", "failed"):
        # 목적지 중복 제거 캐시를 비운다. 안 그러면 다음 주문이 **같은 노드로 시작할 때**
        # "이미 보낸 목적지"로 걸러져 로봇이 출발하지 않는다.
        with _nav_lock:
            _last_nav.pop(robot, None)
        # 주문이 끝났으니 붙잡기도 확실히 푼다 — 마지막 다리가 팔이면 결과 훅이
        # 먼저 풀지만, 결과가 유실됐을 때의 이중 안전망이다(중복 해제는 무해하다).
        _release_hold(robot)
    cmd_id = fleet_telemetry.send_command_async(robot, action=action, args={})
    if cmd_id:
        log.info("[lifecycle] %s → %s (cmd=%s)", robot, action, cmd_id)
    else:
        log.warning("[lifecycle] %s → %s 전송 실패 (명령 링크 없음)", robot, action)


#: 소실로 붙잡아 둔 로봇 → 그때의 안내 목적지 이름. 재획득하면 이걸로 태스크를 다시 낸다.
_guide_paused: dict[str, str] = {}


def on_requester_visible(robot: str, visible: bool) -> None:
    """`/pinkyN/requester_visible` 훅 — **요청자를 다시 찾았다.**

    소실은 로봇이 그 홉을 `requester_lost` 로 닫으며 알려 주지만, 재획득은 **닫을 홉이
    없다**(이미 닫았다). 그래서 가시성 Bool 을 그대로 올려 받는다. 새 통로를 만드는
    대신 이미 있는 신호를 브릿지에 얹었다 — `domain_bridge.template.yaml` 참고.

    ⚠️ **상승 에지에서만** 움직인다. 이 토픽은 계속 오므로(감시가 도는 동안 매 프레임)
       매번 태스크를 내면 fleet_node 에 같은 태스크가 쏟아진다.

    ⚠️ 붙잡기는 태스크를 **다시 낸 뒤에** 푼다. 먼저 풀면 그 사이 `on_timer` 가 유휴로
       보고 순회를 부여한다(fleet_node.cpp:1089 는 hold 만 본다).
    """
    if not visible or not robot:
        return
    waypoint = _guide_paused.pop(robot, "")
    if not waypoint:
        return                      # 소실로 멈춘 안내가 아니다 — 평소 가시성 신호다
    # ⚠️ **그 안내가 아직 살아 있는지 확인한다.** 회복 중에 이용자가 취소했거나
    #    회복이 소진돼(`guide_search_failed`) 안내가 끝났을 수 있다. 그때 남은 항목으로
    #    재배차하면 **아무도 요청하지 않은 주행**이 시작된다 — 지나가던 사람이 뒷캠에
    #    잡히기만 해도 로봇이 옛 목적지로 떠난다.
    #    끝났으면 붙잡기도 같이 푼다. 안 그러면 TTL(120초) 동안 순회를 못 한다.
    if not fleet_link.has_guide_target(robot):
        log.info("[guide] %s 재획득했지만 안내가 이미 끝났다 — 붙잡기만 푼다", robot)
        fleet_link.set_robot_hold(robot, False)
        return
    log.info("[guide] %s 요청자 재획득 — 경로를 다시 낸다 (%s)", robot, waypoint)
    try:
        from app.routers.guide import _submit_guide_task
        task_id = _submit_guide_task(robot, waypoint)
    except Exception:   # noqa: BLE001
        log.exception("[guide] %s 재배차 실패", robot)
        task_id = ""
    if not task_id:
        # 재배차가 안 되면 붙잡기를 **갱신**한다 — 그냥 두면 TTL 이 만료돼 회복이 아직
        # 도는 중에 순회가 배차된다(codex 검토 P1). 갱신은 만료 시각을 미루는 것이라
        # 영구 정지가 아니다 — 다음 가시성 에지에서 다시 시도한다.
        _guide_paused[robot] = waypoint
        fleet_link.set_robot_hold(robot, True, GUIDE_RECOVER_HOLD_TTL)
        return
    fleet_link.set_robot_hold(robot, False)


def _pause_guide_reservation(robot: str) -> None:
    """회복 중인 로봇의 fleet 예약을 놓되 **순찰로 떠나지 않게** 한다.

    ⚠️ **`real_release()` 를 쓰지 않는다.** 그 함수는 두 가지를 같이 하는데 여기서는
       둘 다 해롭다:
         · `_release_hold()` — 방금 건 붙잡기를 **곧바로 푼다**(:192)
         · `set_robot_mode(PATROL)` — fleet_node 의 `robot_mode_` 를 PATROL 로 바꿔
           (fleet_node.cpp:1769) 다음 timer 에서 **순회를 시작할 수 있다**. 로봇 FSM 은
           아직 WORKING 인데 fleet 쪽만 PATROL 인 창이 생긴다.
       (2026-08-02 codex 검토 P0/P1 지적)

    대신 **현재 상태(WORKING)로** 모드를 다시 세운다. fleet_node 는 `RETURNING` 이
    아닌 모드 설정에서 현재 task 를 취소하고 점유 노드를 놓으므로(fleet_node.cpp:1523)
    예약 해제라는 목적은 그대로 달성되고, `mode_of(robot)` 가 WORKING 이라
    `start_patrol` 분기에 애초에 안 걸린다(fleet_node.cpp:1090).

    붙잡기는 그래도 건다 — 로봇 FSM 이 어떤 이유로 WORKING 을 벗어나도 순회 재배차를
    막는 이중 안전망이다.
    """
    fleet_link.set_robot_hold(robot, True, GUIDE_RECOVER_HOLD_TTL)
    try:
        res = fleet_link.set_robot_mode(robot, GUIDE_PAUSE_MODE)
        if not res.get("ok"):
            log.warning("[guide] %s 예약 해제 실패: %s", robot, res.get("reason"))
    except Exception:   # noqa: BLE001 — 예약 해제 실패로 회복을 막지 않는다
        log.exception("[guide] %s 예약 해제 실패", robot)


def clear_guide_bookkeeping(robot: str) -> None:
    """안내가 끝났다(취소·완료·실패) — 이 모듈이 들고 있던 안내 상태를 지운다.

    ⚠️ 안 지우면 `_guide_paused` 가 남아, 나중에 지나가던 사람이 뒷캠에 잡히는 순간
       **아무도 요청하지 않은 주행**이 시작된다(codex 검토 P1). `on_requester_visible`
       의 `has_guide_target` 가드가 한 겹 더 막지만, 근원에서 지우는 쪽이 맞다.
    """
    if not robot:
        return
    _guide_paused.pop(robot, None)
    for k, v in list(_guide_hop_robot.items()):
        if v == robot:
            _guide_hop_robot.pop(k, None)


def on_cmd_result(res: dict) -> None:
    """`/fleet_cmd_result` 훅 — 로봇 BT 가 끝낸 팔 다리를 orchestrator 에 알린다.

    `send_command_async` 가 돌려준 id 를 그대로 다리의 cmd_id 로 쓰므로 매핑 표가 없다.
    우리 것이 아닌 id 는 orchestrator 가 알아서 무시한다(on_result 가 조용히 넘어간다).
    """
    cmd_id = str(res.get("id") or "")
    if not cmd_id:
        return
    ok = bool(res.get("ok"))
    msg = str(res.get("msg") or "")

    # ⚠️ [2026-08-02] **길잡이 홉이 "요청자를 놓쳤다" 로 닫혔다 — 예약을 푼다.**
    #
    #   회복 BT 가 도는 동안 그 로봇은 제자리에 선다. 그런데 fleet_node 는 아직
    #   "가는 중" 으로 알고 **그 로봇 몫으로 잡아 둔 노드·간선을 안 놓는다** — 다른
    #   로봇이 그 길을 못 쓴다.
    #
    #   순서가 중요하다: **붙잡기(hold)를 먼저** 걸고 나서 태스크를 취소한다.
    #   반대로 하면 취소와 hold 사이에 `fleet_node.on_timer` 가 그 로봇을 유휴로 보고
    #   **순회를 새로 부여한다**(fleet_node.cpp:1089 가 hold 만 본다) — 사람을 찾는
    #   중인 로봇이 순찰을 떠나 버린다.
    #
    #   ⚠️ `RobotHold` 만으로는 부족하다. 그건 **순회 재배차만** 막고 진행 중 태스크의
    #      경로 요청은 계속 나간다. 점유를 실제로 놓는 건 태스크 취소(`real_release`)다.
    if msg == GUIDE_LOST_MSG:
        robot = _guide_hop_robot.pop(cmd_id, "")
        if not robot:
            log.warning("[guide] 소실 보고인데 어느 로봇인지 모른다 cmd=%s", cmd_id)
            return
        log.info("[guide] %s 요청자 소실 — 붙잡고 예약을 푼다 cmd=%s", robot, cmd_id)
        # 재획득했을 때 어디로 다시 보낼지 — 목적지를 기억해 둔다.
        _guide_paused[robot] = _guide_target_name(robot)
        _pause_guide_reservation(robot)
        return

    # [Critical 3] shelf_dock 결과 — 원래 NAVIGATE 다리(fleet task_id)를 이제야 닫는다.
    dock_wait = _shelf_dock_wait.pop(cmd_id, None)
    if dock_wait is not None:
        nav_cmd_id, shelf = dock_wait
        robot = _robot_of_cmd(cmd_id)
        if robot:
            fleet_link.set_robot_hold(robot, False)
        if ok:
            on_shelf_dock_phase(robot, shelf, "done")   # [Important 5]
        log.info("[shelf] %s 도킹 결과 cmd=%s ok=%s → 원 다리 cmd=%s 닫음",
                 robot, cmd_id, ok, nav_cmd_id)
        try:
            _orc().on_result(nav_cmd_id, ok, msg)
        except Exception:  # noqa: BLE001
            log.exception("[shelf] on_result 실패 cmd=%s", nav_cmd_id)
        return

    log.info("[arm] BT 결과 cmd=%s ok=%s status=%s", cmd_id, ok, res.get("status"))
    # ⚠️ **결과가 오면 반드시 푼다.** 안 풀면 그 로봇은 TTL 이 만료될 때까지 순회를 못 한다.
    #    다음 다리가 주행이면 fleet_node 가 곧바로 다시 몰고 가므로 여기서 풀어도 안전하다.
    robot = _robot_of_cmd(cmd_id)
    if robot:
        fleet_link.set_robot_hold(robot, False)
    try:
        _orc().on_result(cmd_id, ok, str(res.get("msg") or ""))
    except Exception:  # noqa: BLE001
        log.exception("[arm] on_result 실패 cmd=%s", cmd_id)


#: 주문을 놓아줄 때 fleet_node 에 넣는 모드.
#  PATROL 이면 fleet_node 의 on_timer 가 곧바로 순회를 다시 부여한다 — 주문이 사라졌는데
#  로봇만 목적지로 계속 가는 상태를 막는다.
RELEASE_MODE = os.environ.get("LIBI_RELEASE_MODE", "PATROL")


def real_release(robot: str) -> None:   # noqa: D401
    """주문이 취소·실패했을 때 로봇을 fleet_node 에서 놓아준다.

    ⚠️ **왜 set_robot_mode 인가**: fleet_node 에는 전용 취소 서비스가 없다. `cancel_task()`
    는 `/fms/set_mode` 핸들러 안에서만 불린다(mode != RETURNING 이면 현재 task 취소 +
    점유 노드 해제). 그래서 지금은 이게 로봇을 멈추는 유일한 경로다.
    C++ 에 전용 cancel 서비스를 넣는 게 더 깔끔하지만, 배차·교통 로직을 건드리지 않으려고
    기존 계약을 그대로 쓴다.

    RELEASE_MODE=PATROL 을 주면 취소 즉시 순회로 돌아간다. 그 뒤에는 로봇 자신의
    FsmState 가 다시 정본이 된다(on_fsm_state 가 robot_mode_ 를 덮어쓴다).

    ## 로봇에게도 정지를 보낸다 (2026-07-28)

    `set_robot_mode` 는 **fleet_node** 의 task 를 취소하고 점유 노드를 놓을 뿐,
    로봇에게는 `/fleet_cmd` 를 아무것도 안 보낸다. 이미 내려간 `goal` 은 살아 있어
    **로봇은 현재 목표까지 간 뒤에야 멈췄다** — 주문은 사라졌는데 로봇은 계속 갔다.

    `mission_stop` 은 로봇에서 `mission.stop_mission()` 으로 가고, 그 안에서
    `ros_bridge.cancel_nav()` 가 nav2 목표를 실제로 끊는다.

    **비동기로 보낸다.** 이 함수는 `Orchestrator._release()` 를 통해 **코어 락을 쥔 채**
    불린다(`cancel()`/실패 처리 안쪽). 응답을 기다리는 `send_command_for_robot` 을 쓰면
    ROS 왕복 시간만큼 주문 큐 전체가 멈춘다.

    보낼 게 없어도(로봇이 이미 서 있어도) 무해하다 —
    **멈추는 것은 중복돼도 안전, 조종하는 것은 중복되면 위험.**
    """
    if not robot:
        return
    with _nav_lock:
        _last_nav.pop(robot, None)      # 놓아준 로봇의 목적지 기억도 지운다
    # 주문이 끝났으니 붙잡기도 푼다. 안 풀면 취소된 주문 때문에 로봇이 TTL 동안 순회를 못 한다.
    _release_hold(robot)

    try:
        cmd_id = fleet_telemetry.send_command_async(robot, action="mission_stop", args={})
        log.info("[release] %s 주행 정지 요청 (cmd=%s)", robot, cmd_id)
    except Exception as e:                      # noqa: BLE001
        # 정지를 못 보내도 아래 해제는 계속한다 — 둘 다 실패하는 것보다 낫다.
        log.warning("[release] %s 정지 명령 실패: %s", robot, e)

    res = fleet_link.set_robot_mode(robot, RELEASE_MODE)
    if res.get("ok"):
        log.info("[release] %s → %s (task 취소·점유 해제)", robot, RELEASE_MODE)
    else:
        # 실패해도 주문 상태 전이는 이미 끝났다 — 여기선 남기기만 한다.
        log.warning("[release] %s 해제 실패: %s", robot, res.get("reason"))


def find_orphans(robots, live_ids) -> list[tuple[str, str]]:
    """fleet_node 는 붙잡고 있는데 orchestrator 는 모르는 task 를 고른다.

    반환: [(로봇, fleet task id), …]

    ⚠️ **왜 생기나**: orchestrator 의 주문은 메모리에만 있다. 백엔드를 재기동하면 주문은
    전부 사라지는데 fleet_node 는 그대로 살아 있어 `orchestrator:t10` 같은 일을 계속
    붙잡는다. 그러면 그 로봇은 `busy` 로 고정돼 **영원히 배차를 못 받는다**
    (패널에는 "IDLE 1대 / 배차 가능 0대" 라는 말이 안 되는 화면이 뜬다).

    fleet_node 자체 순회(`P-<robot>`)나 다른 주체가 낸 일은 **건드리지 않는다** —
    접두사가 우리 것인 task 만 고아 판정 대상이다.

    순수 함수라 ROS 없이 테스트된다.
    """
    out: list[tuple[str, str]] = []
    for r in robots:
        tid = str(r.get("task_id") or "")
        if not r.get("busy") or not tid.startswith(TASK_PREFIX):
            continue
        if tid[len(TASK_PREFIX):] not in live_ids:
            out.append((str(r.get("name") or ""), tid))
    return out


def reconcile_once() -> int:
    """고아 task 를 찾아 로봇을 놓아준다. 정리한 개수를 반환."""
    # [P2-1] 정점 차단 TTL 만료 sweep. `on_person_blocked`/`on_shelf_dock_lock` 안에서만
    # 불리면 아무도 그 정점을 다시 안 건드리는 한 `node_unblocked` 사건이 영영 안 나간다
    # (레지스트리 내부는 맞게 풀려도 관제 화면은 언제 풀렸는지 모른다, PRD Story 44).
    # 이미 도는 이 주기 루프에 얹는다 — 새 스레드/타이머를 안 만든다.
    _publish_ttl_expirations(time.monotonic())
    try:
        orc = _orc()
        live = {
            t["id"] for t in orc.snapshot()
            if t["status"] not in ("COMPLETED", "FAILED", "CANCELLED")
        }
        robots = fleet_link.snapshot().get("robots", [])
    except Exception:  # noqa: BLE001 — 화해 실패가 서비스를 막으면 안 된다
        log.exception("[reconcile] 스냅샷 읽기 실패")
        return 0

    orphans = find_orphans(robots, live)
    seen = {(r, t) for r, t in orphans}

    # ⚠️ **연속 2회 관측했을 때만 정리한다.**
    # 한 번 보고 바로 끊으면, 주문이 막 접수돼 fleet_node 에는 등록됐지만 orchestrator
    # 스냅샷에는 아직 안 잡힌 찰나에 **살아 있는 주문을 죽인다.** 실제로 그 일이 났다.
    # 고아는 백엔드 재기동 같은 영구적 상황이라 한 주기 늦게 정리해도 아무 문제가 없다.
    confirmed = seen & _orphan_seen
    _orphan_seen.clear()
    _orphan_seen.update(seen - confirmed)   # 이번에 처음 본 것은 다음 주기에 판정

    for robot, tid in confirmed:
        log.warning(
            "[reconcile] 고아 task 정리: %s 가 %s 를 붙잡고 있는데 orchestrator 는 모른다"
            " (백엔드 재기동 추정) → 로봇 해제",
            robot, tid,
        )
        real_release(robot)
    return len(confirmed)


def _reconcile_loop() -> None:
    while not _reconcile_stop.wait(RECONCILE_SEC):
        reconcile_once()


#: 다리 종류별로 "무슨 일이 일어났는가"를 사람 말로 옮긴다.
# 화면마다 이 문장을 다시 만들면(관제 하나, 회원 앱 하나) 곧 서로 다른 말을 하게 된다.
_ARRIVAL_TEXT = {
    "navigate": "{where} 도착",
    "perform_action": "{what} 완료",
}

#: 팔 액션 → 사람이 읽을 문구. `snapshot_leg_label()`/`_completed_leg_summary()` 가 공유한다 — 따로 두면
#: 새 액션을 추가할 때 한쪽만 고치고 잊어버리기 쉽다.
_ARM_ACTION_TEXT = {
    "pick": "책 집기", "place": "책 놓기",
    "unload_to_floor": "바구니 내려놓기",
    "load_from_box": "바구니 싣기",
    "refill_box": "바구니 채우기",
}


def snapshot_leg_label(leg: dict) -> str:
    """`Task.snapshot()` 의 raw leg dict(`{"type","params"}`) 하나를 관제 패널에 보일
    한 줄로 바꾼다.

    task_type 마다 다리 뜻이 다르다 — 수거의 4다리(주행+팔×3)는 배달의 4다리
    (주행→집기→주행→놓기)와 전혀 다른 일을 한다. 프런트가 `leg_idx` 를 4로 나눈
    나머지로 라벨을 추측하면(예전 `LEG_STEPS`) 반드시 틀린 라벨이 뜬다 — 실제
    leg 값을 보고 여기서 만든다.
    """
    params = leg.get("params") or {}
    if leg.get("type") == "navigate":
        where = params.get("waypoint", "")
        return f"주행 → {where}" if where else "주행"
    action = str(params.get("action") or "")
    text = _ARM_ACTION_TEXT.get(action, "작업")
    where = params.get("at", "")
    return f"{text} ({where})" if where else text


def _completed_leg_summary(leg) -> tuple[str, str]:
    """(다리 종류, 사람이 읽을 한 줄). leg 가 없으면 빈 값.

    `snapshot_leg_label()` 과 목적이 다르다 — 이건 다리 하나가 **막 끝났을 때** 사건 문구를
    만든다(과거형, "도착"/"완료"). `snapshot_leg_label()` 은 아직 안 끝난 다리도 포함해 전체
    목록을 미리 보여줄 때 쓴다(중립형, "주행"/"집기").
    """
    if leg is None:
        return "", ""
    kind = getattr(leg.type, "value", str(leg.type))
    params = leg.params or {}
    where = str(params.get("waypoint") or params.get("at") or "")
    what = _ARM_ACTION_TEXT.get(str(params.get("action") or ""), "작업")
    text = _ARRIVAL_TEXT.get(kind, "{what}").format(where=where or "목적지", what=what)
    return kind, text


#: task_type 별 완료 문구. 종류가 늘 "배달"은 아니다 — 수거·주행도 이 이벤트를 낸다.
_TASK_DONE_TEXT = {
    "delivery": "배달 완료",
    "navigate": "이동 완료",
    "collect": "수거 완료",
}


def on_orchestrator_event(kind: str, task, leg) -> None:
    """orchestrator 의 사건을 화면이 읽을 모양으로 바꿔 발행한다.

    **여기서 예외를 내면 안 된다** — 이 함수는 오케스트레이터 락 안에서 불린다.
    (`fleet_events.publish` 자체도 예외를 삼키지만, 그 앞 변환에서 터질 수 있다.)
    """
    try:
        leg_kind, text = _completed_leg_summary(leg)
        if kind == "task_done":
            text = _TASK_DONE_TEXT.get(task.task_type, "작업 완료")
        elif kind == "task_failed":
            text = f"실패: {task.reason}" if task.reason else "실패"
        elif kind == "task_started":
            text = "작업 시작"
        fleet_events.publish(
            kind,
            task_id=task.id,
            robot=task.robot or "",
            requester=task.requester or "",
            status=getattr(task.status, "value", str(task.status)),
            leg_idx=task.leg_idx,
            leg_count=len(task.legs),
            leg_kind=leg_kind,
            text=text,
        )
    except Exception:  # noqa: BLE001
        log.exception("[events] 사건 변환 실패 kind=%s", kind)


#: 자동배차 주기(초). 0 이면 끔 — 사람이 관제 화면에서 배차(수동/템플릿)해야 한다.
#
# ⚠️ **진짜 경매가 아니다.** fleet_node 의 `Auction` 플러그인(Dijkstra 최단거리)은
# `SubmitTask` 를 `robot=""` 로 불러야 도는데, 그 응답에는 **어느 로봇이 뽑혔는지가 없다**
# (`SubmitTask.srv` Response: accepted/task_id/reason 뿐 — robot 필드 없음). 그 값은
# `/fms/task_states` 의 ASSIGNED 메시지로 뒤늦게, 서비스 응답과 순서 보장 없이 온다.
# 그 경합에 기대는 대신, 관제 프런트가 이미 쓰는 것과 **같은 휴리스틱**(PATROL 우선 →
# 배터리 최대, `dispatch-shared.ts:pickRobot`)을 여기서도 써서 사람이 아는 로봇을 바로
# `assign()` 한다 — 두 곳에 각자 만들면 반드시 어긋나므로 근거 문서는 하나(그 파일)만 보면 된다.
# 나중에 fleet_node 쪽 `SubmitTask.srv` 에 robot 필드를 추가하면 이 함수는 지우고
# `real_dispatch` 가 `robot=""` 로 제출하도록 바꾼다.
AUTO_ASSIGN_SEC = float(os.environ.get("LIBI_AUTO_ASSIGN_SEC", "3"))

#: 배터리 완주 관문 — **fleet_node 의 `battery_drain_per_m` · `battery_reserve_pct`
#: 기본값과 같은 값이다.** 두 곳에 있는 이유는 입찰 단계에서 먼저 걸러야 낙찰된 뒤
#: 거절당하는 일이 없기 때문이다(`pick_robot` 독스트링). fleet_node 쪽 파라미터를
#: 바꾸면 여기 환경변수도 같이 준다 — 어긋나면 관문이 두 번 다르게 걸린다.
DRAIN_PER_M = float(os.environ.get("LIBI_BATTERY_DRAIN_PER_M", "1.0"))
BATTERY_RESERVE_PCT = float(os.environ.get("LIBI_BATTERY_RESERVE_PCT", "15.0"))

#: 로봇 좌표에서 정점을 되찾을 때의 반경(m). 노드 점유 정보가 없을 때만 쓴다.
#: `vertex_at` 의 기본값(0.05)은 홉 판정용이라 너무 빡빡해 — 서 있는 로봇은 그보다
#: 벌어져 있는 것이 정상이다.
ROBOT_VERTEX_TOL = float(os.environ.get("LIBI_ROBOT_VERTEX_TOL", "0.4"))
_auto_assign_stop = threading.Event()

#: fleet_node 의 `can_accept()`/`is_dispatchable()` 과 같은 규칙(IDLE·PATROL 만).
_ACCEPTING_STATES = {"IDLE", "PATROL"}


def _reject_reason(r: dict) -> str | None:
    """이 로봇을 후보에서 뺀 이유. 넣어도 되면 None.

    ## 왜 사유를 따로 내는가
    예전에는 `ready()` 가 bool 만 냈다. 그래서 후보가 0이면 배차 루프가 **조용히** 0건을
    반환하고 주문은 PENDING 에 그대로 남았다 — 화면에도 로그에도 아무것도 안 나온다.
    실측: 로봇 3대가 멀쩡히 순회 중인데 주문이 영영 안 나갔다. 원인은 상태기계 링크가
    없어 `state` 가 None 이었던 것뿐인데, 그 사실이 어디에도 안 드러났다.
    """
    if r.get("stale"):
        return "위치 신호 끊김"
    state = r.get("state")
    on_fms_patrol = str(r.get("task_id") or "").startswith("P-")
    if state is None:
        # ⚠️ **좁게만 허용한다.** FMS 가 **자기가 배정한** 순회를 돌리고 있는 로봇이면,
        #    적어도 움직일 수 있다는 것은 FMS 자신이 안다(그 순회를 자기가 걸었으므로).
        #    그 근거가 없으면 모르는 상태에 일을 주는 것이라 거절한다 — ERROR·충전 중인
        #    로봇에 배차하는 것보다 멈춰 서는 편이 낫다.
        return None if on_fms_patrol else "상태 미상 (상태기계 링크 확인)"
    if state not in _ACCEPTING_STATES:
        return f"상태 {state}"
    if r.get("busy") and not on_fms_patrol:
        return "다른 작업 중"   # 순회(P-*)는 선점 가능, 그 외 작업 중은 후보 제외
    return None


def _robot_vertex(r: dict) -> int | None:
    """로봇이 지금 서 있는 정점.

    쥐고 있는 노드가 있으면 그것을 쓴다 — 교통관제가 "이 로봇은 여기 있다"고 판정한
    값이라 좌표 역산보다 정확하다. 없으면 좌표에서 가장 가까운 정점을 찾는다.
    """
    held = r.get("held_nodes") or []
    if held:
        return int(held[0])
    x, y = r.get("x"), r.get("y")
    if x is None or y is None:
        return None
    return vertex_at(float(x), float(y), tol=ROBOT_VERTEX_TOL)


def pick_robot(robots: list[dict], goal_idx: int | None = None) -> str | None:
    """대기 중인 주문 하나에 배정할 로봇을 고른다 — **경매(Auction)**.

    후보 로봇이 목표까지의 **실제 주행거리(m)** 로 입찰하고 **최저가가 낙찰**된다.

    ## 배터리는 입찰가가 아니라 완주 관문이다

    입찰가에 섞으면 "배터리 많은 로봇이 굳이 먼 거리를 가는" 비효율이 생긴다.
    통과한 로봇끼리는 거리로만 겨루고, 방전 좌초는 관문이 막는다.

    ⚠️ 관문을 **여기서 먼저** 거는 이유: fleet_node 도 같은 관문을 갖고 있지만, 거기서
       거절당하면 이미 낙찰된 주문이 dispatch 단계에서 실패로 닫힌다. 입찰 단계에서
       빼면 그 로봇이 애초에 낙찰되지 않는다.

    ## 거리를 못 재면 예전 규칙으로 떨어진다

    목표 정점을 모르거나(주행 다리가 없는 주문) navgraph 를 못 읽는 경우다. 그때는
    순회 우선 → 배터리 높은 순 (`dispatch-shared.ts:pickRobot` 과 같은 규칙).
    **거리를 잴 수 있었는데 관문을 다 떨어진 경우는 폴백하지 않는다** — 폴백하면
    배터리 부족한 로봇이 낙찰되어 fleet_node 가 거절한다.
    """
    candidates = [r for r in robots if _reject_reason(r) is None]
    if not candidates:
        return None

    if goal_idx is not None:
        bids: list[tuple[float, str]] = []
        priced = 0                      # 거리를 잰 로봇 수 (관문 탈락 포함)
        for r in candidates:
            if _reachable(r, goal_idx) is None:
                continue                # 위치 미상·경로 없음 — 거리를 못 잰다
            priced += 1
            cost = bid_of(r, goal_idx)
            if cost is not None:
                bids.append((cost, str(r["name"])))
        if priced:
            if not bids:
                return None             # 전원 관문 탈락 — 폴백하지 않는다(위 독스트링)
            bids.sort()
            return bids[0][1]

    candidates.sort(key=lambda r: (0 if r.get("state") == "PATROL" else 1,
                                   -(r.get("battery") if r.get("battery") is not None else -1)))
    return str(candidates[0]["name"])


def _reachable(robot: dict, goal_idx: int | None) -> float | None:
    """이 로봇이 목표까지 가는 주행거리(m). 위치를 모르거나 경로가 없으면 None.

    **완주 관문은 여기서 안 본다** — "거리를 잴 수 있었나"와 "갈 배터리가 있나"를
    가르기 위해서다. 둘을 합치면 배터리 부족을 '거리 못 잼'으로 오인해 폴백이 열린다.
    """
    if goal_idx is None:
        return None
    v = _robot_vertex(robot)
    if v is None:
        return None
    return path_cost(v, int(goal_idx))


def bid_of(robot: dict, goal_idx: int | None) -> float | None:
    """이 로봇이 이 목표에 낼 **입찰가(m)**. 입찰할 수 없으면 None.

    입찰가 = 목표까지의 실제 주행거리. 배터리는 값에 섞지 않고 관문으로만 쓴다.
    """
    cost = _reachable(robot, goal_idx)
    if cost is None:
        return None
    batt = robot.get("battery")
    if batt is not None and float(batt) < cost * DRAIN_PER_M + BATTERY_RESERVE_PCT:
        return None                     # 완주 관문 탈락
    return cost


def first_goal_vertex(task: dict) -> int | None:
    """주문의 **첫 주행 다리** 목적지 정점. 없으면 None(= 거리 입찰 불가).

    입찰가는 "지금 위치 → 첫 목적지"다. 배달이면 서가까지의 거리이고, 그 뒤 다리는
    어느 로봇이 맡아도 같으므로 비교에 넣지 않는다.
    """
    for leg in task.get("legs") or []:
        if leg.get("type") != LegType.NAVIGATE.value:
            continue
        wp = (leg.get("params") or {}).get("waypoint")
        if wp is None:
            return None
        try:
            return resolve_vertex(wp)
        except Exception:               # noqa: BLE001 — navgraph 에 없는 이름
            return None
    return None


def _best_pair(tasks: list[dict], robots: list[dict],
               goals: dict[str, int | None]) -> tuple[dict, dict] | None:
    """이번 라운드의 **최저 입찰 (주문, 로봇) 한 쌍**. 아무도 입찰 못 하면 None.

    SSI 의 한 라운드다 — 남은 주문 전체 × 남은 로봇 전체를 보고 가장 싼 짝을 고른다.
    주문을 고정해 두고 로봇만 고르면(= 예전 방식) 그 주문이 로봇을 먼저 집어가서
    **더 싸게 처리될 수 있었던 다음 주문이 먼 로봇을 떠안는다.**
    """
    best: tuple[float, dict, dict] | None = None
    for t in tasks:
        goal = goals.get(t["id"])
        for r in robots:
            bid = bid_of(r, goal)
            if bid is None:
                continue
            if best is None or bid < best[0]:
                best = (bid, t, r)
    return (best[1], best[2]) if best else None


def _auto_assign_once() -> int:
    """대기 주문을 **순차 단일품목 경매(SSI)** 로 배차한다.

    한 라운드마다 (남은 주문 × 남은 로봇)의 입찰가를 전부 보고 **가장 싼 짝**을 낙찰한다.
    낙찰된 주문과 로봇을 빼고 다음 라운드를 돈다 — 그래서 앞 낙찰이 뒤 주문의 선택지를
    바꾸는 것을 계산에 넣는다(그 재평가가 없으면 단순 즉시배정 IA 다).

    `priority` 는 입찰가로 흡수하지 않고 **계층**으로 둔다 — 급한 주문이 "조금 더 가까운"
    일반 주문에 밀리면 우선순위라는 말이 성립하지 않는다. 같은 계층 안에서만 경매한다.
    """
    orc = _orc()
    pending = list(orc.pending())
    if not pending:
        return 0
    robots = [r for r in fleet_link.snapshot().get("robots", [])
              if _reject_reason(r) is None]
    all_robots = fleet_link.snapshot().get("robots", [])
    goals = {t["id"]: first_goal_vertex(t) for t in pending}
    assigned = 0

    while pending and robots:
        # 우선순위 계층 — priority 는 task 지정 우선도(SubmitTask.srv 주석과 같은 뜻).
        tier = max(int(t.get("priority", 0) or 0) for t in pending)
        tier_tasks = [t for t in pending if int(t.get("priority", 0) or 0) == tier]

        pair = _best_pair(tier_tasks, robots, goals)
        if pair is None:
            # 거리를 못 재는 계층(주행 다리가 없거나 위치 미상) — 예전 규칙으로 한 건 민다.
            task = tier_tasks[0]
            name = pick_robot(robots, goals.get(task["id"]))
            if name is None:
                # ⚠️ **조용히 나가지 않는다.** 대기 주문이 있는데 후보가 0이면 그건 정상이
                #    아니라 진단해야 할 상태다. 로봇별 거절 사유를 로그와 주문 사유에 남겨,
                #    "왜 배차가 안 되지" 를 화면만 보고 알 수 있게 한다.
                why = ", ".join(
                    f"{r.get('name')}: {_reject_reason(r)}" for r in all_robots
                ) or "로봇 없음"
                log.warning("[auto-assign] 대기 %d건인데 배차 가능한 로봇이 없습니다 — %s",
                            len(pending), why)
                _mark_stalled(pending, why)
                break   # 배차 가능한 로봇 소진 — 나머지는 다음 주기로
            robot = next(r for r in robots if str(r.get("name")) == name)
        else:
            task, robot = pair

        pending = [t for t in pending if t["id"] != task["id"]]
        try:
            orc.assign(task["id"], str(robot["name"]))
        except (KeyError, ValueError) as exc:
            log.warning("[auto-assign] %s → %s 배정 실패: %s",
                        task["id"], robot.get("name"), exc)
            continue    # 로봇은 남겨 둔다 — 다른 주문이 쓸 수 있다
        robots = [r for r in robots if r.get("name") != robot.get("name")]
        assigned += 1
    return assigned


def _mark_stalled(pending: list[dict], why: str) -> None:
    """대기 주문에 "왜 안 나가는지" 를 적는다. 화면이 그 사유를 그대로 보여 준다."""
    orc = _orc()
    for t in pending:
        try:
            orc.set_reason(t["id"], f"배차 대기 — {why}")
        except (KeyError, AttributeError):
            pass


def _auto_assign_loop() -> None:
    while not _auto_assign_stop.wait(AUTO_ASSIGN_SEC):
        try:
            n = _auto_assign_once()
            if n:
                log.info("[auto-assign] %d건 배차", n)
        except Exception:  # noqa: BLE001 — 루프가 죽으면 자동배차 자체가 영구히 멈춘다
            log.exception("[auto-assign] 루프 실패")


def install() -> None:
    """실배선을 켠다. `LIBI_REAL_DISPATCH=1` 일 때만 main.py 가 부른다."""
    from app import fleet_orchestrator_service as svc

    svc.set_dispatch(real_dispatch)
    svc.set_release(real_release)
    svc.set_lifecycle(real_lifecycle)
    svc.set_on_event(on_orchestrator_event)
    fleet_link.add_task_state_hook(on_task_state)
    fleet_link.add_path_request_hook(on_path_request)
    fleet_telemetry.add_cmd_result_hook(on_cmd_result)
    # 길잡이 재획득 → 경로 재할당 (`on_requester_visible` 주석 참고)
    fleet_link.add_requester_visible_hook(on_requester_visible)
    # 로봇발 "사람이 정점을 막았다" 보고 → on_person_blocked (아래 `_on_node_block_report`)
    fleet_link.add_node_block_hook(_on_node_block_report)

    # 고아 task 화해 — 백엔드만 재기동한 경우를 자동으로 되돌린다.
    if RECONCILE_SEC > 0:
        _reconcile_stop.clear()
        threading.Thread(target=_reconcile_loop, daemon=True,
                         name="fleet-reconcile").start()

    # 자동배차 — 이게 없으면 주문이 PENDING 에서 사람이 누를 때까지 영원히 멈춘다
    # (2026-07-30 배선 감사에서 확인된 증상). 실배선(LIBI_REAL_DISPATCH=1)일 때만 돈다 —
    # stub 모드에서 자동으로 assign 해봐야 로그만 남고 아무것도 안 움직인다.
    if AUTO_ASSIGN_SEC > 0:
        _auto_assign_stop.clear()
        threading.Thread(target=_auto_assign_loop, daemon=True,
                         name="fleet-auto-assign").start()

    log.info(
        "[dispatch] 실배선 활성화 (arm_stub=%s, delay=%.1fs, auto_assign=%.1fs)",
        ARM_STUB, ARM_STUB_DELAY_SEC, AUTO_ASSIGN_SEC,
    )


# ── 정점 차단·서가 도킹 → 사건 (Task 14) ─────────────────────────────────────
#
# 로봇이 "사람이 이 정점을 막았다" 고 보고하면 fleet_node 로 중계하고, 관제 화면이
# 볼 사건을 남긴다. 서가 도킹 진행도 같은 사건 버스로 알린다.

#: 정점 차단 대장. fleet_node(C++) 도 자기 것을 들고 있지만, 이쪽은 사건 발행과
#: 화면 조회를 위해 같은 사실을 파이썬 쪽에서도 안다.
_node_blocks = NodeBlockRegistry()

_DOCK_EVENTS = {"started": "shelf_dock_started", "done": "shelf_dock_done"}


def _publish_node_block(*, robot: str, node: int, ttl_sec: float, reason: str, owner: str) -> None:
    """`/fms/node_block` 로 `NodeBlock` 을 낸다 — `fleet_node.cpp` 가 그 토픽을 구독한다
    (`aba_fms_service/fleet_ws/src/libi_fleet/src/fleet_node.cpp:344-346`).

    이 파일이 이미 쓰는 통로(`fleet_link` — `set_robot_hold` 가 같은 계약의 `RobotHold` 를
    낸다, `app/fleet_link.py:613-631`)에 맞춘다. 그 모듈에 아직 node_block 용 발행 함수가
    없으면(다른 작업 몫) 경고만 남기고 돌아간다 — **사건 발행은 이 함수 호출 전에 이미
    끝났으므로 알림이 조용히 사라지지 않는다.**
    """
    publish = getattr(fleet_link, "set_node_block", None)
    if publish is None:
        log.warning(
            "[node_block] fleet_link 에 node_block 발행 통로가 없다 — "
            "robot=%s node=%s ttl=%.1f owner=%s (사건 발행은 계속한다)",
            robot, node, float(ttl_sec), owner,
        )
        return
    try:
        publish(node, float(ttl_sec), reason=reason, robot=robot, owner=owner)
    except Exception:  # noqa: BLE001 — ROS 발행 실패로 사건 발행까지 막으면 안 된다
        log.exception("[node_block] 발행 실패 node=%s", node)


def _publish_ttl_expirations(now: float) -> None:
    """`_node_blocks` 가 저절로 만료시킨 정점마다 `node_unblocked` 를 낸다 (R8-3).

    명시적 `ttl_sec<=0` 해제는 `on_person_blocked` 가 스스로 발행하므로 여기서 다시
    안 낸다 — `expired_since` 는 **자연 만료**만, 그것도 한 번만 돌려준다.
    """
    for node in _node_blocks.expired_since(now):
        fleet_events.publish("node_unblocked", node=node, reason="ttl")


def _on_node_block_report(robot: str, payload: dict) -> None:
    """`fleet_link.add_node_block_hook` 콜백 — 로봇이 올린 정점 차단/해제 보고를
    `on_person_blocked` 로 흘려보낸다.

    `payload` 는 `fleet_link.node_block_from_json` 이 이미 검증한 값이라 `node` 가
    항상 있다(구독 콜백 쪽에서 이미 걸러졌다 — 여기서 다시 검증하지 않는다).

    ⚠️ `reason` 을 보고 owner 를 가른다. 로봇이 도킹 정밀 이동을 시작하며 스스로 푸는
    보고(`reason="shelf_dock"`)를 `person:` owner 로 처리하면 **엉뚱한 차단(사람 차단)
    을 지운다** — `on_shelf_dock_lock` 이 잡은 `dock:{robot}` 이 아니라.

    [Important 4] `fleet_telemetry.bridge_key` 로 정규화한다 — `on_shelf_dock_lock`
    도 같은 함수를 거친다(아래). 여기 `robot` 은 이미 브릿지 키라 사실상 항등이지만,
    **양쪽이 같은 정규화 함수를 통과**해야 어느 쪽이 DB 이름을 받아도 어긋나지 않는다.
    """
    reason = str(payload.get("reason") or "person")
    owner = (f"dock:{fleet_telemetry.bridge_key(robot)}" if reason == "shelf_dock"
             else f"person:{robot}")
    ttl_sec = float(payload.get("ttl_sec", 0.0))
    on_person_blocked(robot, int(payload["node"]), ttl_sec, owner=owner, reason=reason)
    # ⚠️ [2026-08-03] **여기서 로봇에 `backup` 을 쏘지 않는다.**
    #
    #   예전에는 사람 차단 직후 FMS 가 로봇에게 직접 후퇴를 명령했다. 그 이동은
    #   **예약 체계 밖**이라, 이미 놓아 준 직전 정점에 다른 로봇이 들어와 정면으로
    #   만날 수 있었다(codex 3차 P0 셋이 전부 여기서 나왔다).
    #
    #   되돌리는 일은 이제 `fleet_node` 가 한다 — `/fms/node_block` 콜백에서
    #   직전 정점을 `request_move(A, A)` 로 **점유 claim 한 뒤에만** 막힌 정점을 놓고
    #   `moving=false` 로 내린다. 그 뒤는 평소 경로대로 GRANT 가 나가고
    #   `send_path` 가 로봇의 **실제 좌표**에서 경로를 내므로 nav2 가 알아서 몬다.
    #   즉 후퇴도 교통관제 안에서 일어난다.
    #
    #   `backup` 액션 자체는 남아 있다 — **서가 도킹 복귀**가 쓴다(BackupExec).
def on_person_blocked(robot: str, node: int, ttl_sec: float, now: float | None = None,
                       *, owner: str | None = None, reason: str = "person") -> None:
    """로봇이 "사람이 이 정점을 막았다" 고 알려 왔다.

    fleet_node 로 중계하고 관제 화면이 볼 사건을 남긴다. `ttl_sec <= 0` 은 해제다.
    소유자는 기본 `f"person:{robot}"` — 서가 도킹 잠금(`f"dock:{robot}"`)과 겹쳐도 서로
    남의 차단을 지우지 않는다(R4). `owner`/`reason` 은 `_on_node_block_report` 가 로봇의
    `shelf_dock` 해제 보고를 `dock:` owner 로 돌리는 데 쓴다 — 밖에서 부를 땐 안 줘도 된다.

    `now` 는 시험이 시계를 직접 밀 수 있도록 둔 선택 인자다. 기본은 `time.monotonic()`.
    """
    if now is None:
        now = time.monotonic()
    _publish_ttl_expirations(now)   # 이번 호출 전에 저절로 풀린 정점부터 알린다
    owner = owner or f"person:{robot}"
    _node_blocks.set(node, ttl_sec, now, owner=owner, reason=reason)
    _publish_node_block(robot=robot, node=node, ttl_sec=ttl_sec, reason=reason, owner=owner)
    if float(ttl_sec) <= 0.0:
        fleet_events.publish("node_unblocked", robot=robot, node=node)
    else:
        fleet_events.publish("person_blocked", robot=robot, node=node,
                              ttl_sec=float(ttl_sec))


def on_shelf_dock_lock(robot: str, node: int, ttl_sec: float = SHELF_DOCK_TTL_SEC,
                        now: float | None = None) -> None:
    """서가 다리 배차 시 그 정점을 잠근다. 로봇이 정밀 이동을 시작하면 스스로 푼다
    (`reason="shelf_dock"` 보고 → `_on_node_block_report` → 여기와 같은 `dock:` owner 해제).

    ⚠️ owner 가 `person:` 과 달라야 한다 — 같은 정점을 사람 차단과 도킹 잠금이 함께
       잡을 수 있고, 한쪽 해제가 남의 차단까지 지우면 안 된다(NodeBlockRegistry 계약).

    TTL 기본은 `SHELF_DOCK_TTL_SEC`(로봇 hold 와 같은 값) — 도킹+팔 작업이 그 안에
    끝난다는 전제이고, 안 끝나면 스스로 풀려 교착을 막는다(`RobotHold` 와 같은 이유).
    """
    if now is None:
        now = time.monotonic()
    _publish_ttl_expirations(now)
    # [Important 4] `fleet_telemetry.bridge_key` 로 정규화한다. 여기 `robot` 은
    # `real_dispatch` 가 준 DB 표기(`Pinky-1`)일 수 있는데, 해제는 로봇 자신의 보고가
    # **브릿지 키**(`pinky1`, `fleet_link.py` 의 `/{key}/node_block` 구독)로 온다 —
    # 정규화 없이 그대로 쓰면 owner 문자열이 갈려 **해제가 no-op** 이 되고 TTL 까지
    # 잠겨 있는다(실측 지적).
    owner = f"dock:{fleet_telemetry.bridge_key(robot)}"
    _node_blocks.set(node, ttl_sec, now, owner=owner, reason="shelf_dock")
    _publish_node_block(robot=robot, node=node, ttl_sec=ttl_sec, reason="shelf_dock", owner=owner)
    fleet_events.publish("shelf_node_locked", robot=robot, node=node, ttl_sec=float(ttl_sec))


def on_shelf_dock_phase(robot: str, shelf: str, phase: str) -> None:
    """서가 도킹 진행을 관제에 알린다."""
    kind = _DOCK_EVENTS.get(phase)
    if kind is None:
        log.warning("[shelf] %s 알 수 없는 도킹 단계: %s", robot, phase)
        return
    fleet_events.publish(kind, robot=robot, shelf=shelf)
