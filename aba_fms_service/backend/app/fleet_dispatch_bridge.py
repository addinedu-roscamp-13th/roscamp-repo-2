"""orchestrator(주문 상태기) ↔ fleet_node(배차·교통) 배선.

핸드오프 문서 6절 ① 이 여기다. 지금까지 orchestrator 는 `_stub_dispatch`(로그만) 로 돌아
로봇에 아무 명령도 가지 않았다. 이 모듈이 그 자리를 채운다.

## ⚠️ 알고리즘은 건드리지 않는다
배차(`Auction`)·교통(`ReservationDeadlock`)은 pluginlib 플러그인이고 앞으로 바뀔 것이므로
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

import itertools
import logging
import os
import threading
import time

from app import fleet_events, fleet_link, fleet_telemetry
from app.fleet_orchestrator import LegType

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


def _load_vertex_index() -> dict[str, int]:
    """navgraph 의 `정점 이름 → 인덱스` 표.

    ⚠️ **왜 필요한가**: `fleet_node.cpp` 의 `on_submit` 은 `std::stoi(req->dropoff)` 로
    목적지를 **숫자 인덱스**로만 받는다(이름 조회 함수가 없다). 주문은 `문학-1` 같은
    waypoint 이름을 쓰므로, 여기서 인덱스로 바꿔 넘긴다.
    fleet_node(C++) 를 고치지 않기 위한 변환이며, navgraph 파일이 같으므로 인덱스가 일치한다.
    """
    global _vertex_index
    if _vertex_index is not None:
        return _vertex_index

    import yaml

    with open(NAVGRAPH_PATH) as f:
        data = yaml.safe_load(f)
    verts = data["levels"]["L1"]["vertices"]
    table: dict[str, int] = {}
    for i, v in enumerate(verts):
        # 정점 형식: [x, y, {name: '...'}]
        meta = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
        name = meta.get("name")
        if name:
            table[str(name)] = i
    _vertex_index = table
    log.info("[dispatch] navgraph 정점 %d개 로드: %s", len(table), NAVGRAPH_PATH)
    return table


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


def real_dispatch(task_id: str, robot: str, leg) -> str:
    """orchestrator 가 다리 하나를 내보낼 때 부른다. 반환값이 cmd_id."""
    if leg.type == LegType.NAVIGATE:
        waypoint = str(leg.params.get("waypoint", ""))
        if not waypoint:
            raise RuntimeError(f"{task_id}: NAVIGATE 다리에 waypoint 가 없다")

        # fleet_node 는 숫자 인덱스만 받는다(위 resolve_vertex 주석 참고).
        goal_idx = resolve_vertex(waypoint)
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
        log.info(
            "[dispatch] %s NAVIGATE→%s(v%d) robot=%s fleet_task=%s",
            task_id, waypoint, goal_idx, robot or "(경매)", cmd_id,
        )
        return cmd_id

    if leg.type == LegType.PERFORM_ACTION:
        action = leg.params.get("action", "?")
        where = leg.params.get("at", "?")
        if not ARM_STUB:
            raise RuntimeError("팔 배선 없음 (LIBI_ARM_STUB=0)")
        # ── 1순위: 로봇 BT 로 보낸다 ──────────────────────────────────────
        if ARM_VIA_BT:
            cmd_id = fleet_telemetry.send_command_async(
                robot, action="perform_action",
                args={"action": action, "book": leg.params.get("book", ""), "at": where},
            )
            if cmd_id:
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
    """fleet_link 훅 — fleet task 가 끝나면 orchestrator 에 알린다."""
    state = (payload.get("state") or "").upper()
    if state not in ("COMPLETED", "FAILED", "REJECTED"):
        return  # ASSIGNED/EXECUTING 은 진행 중
    cmd_id = payload.get("task_id") or ""
    if not cmd_id:
        return
    ok = state == "COMPLETED"
    log.info("[dispatch] fleet task %s → %s", cmd_id, state)
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
# ⚠️ `libi_modes` 의 `NavigationExec` 에도 재전송(`arrive_resend_sec`)이 있다.
#    **중복이 아니라 서로 다른 고장을 막는다** — 지우기 전에 읽을 것:
#      여기(FMS)  : 명령이 **로봇에 닿지 않은** 경우 (DDS 유실, 로봇 늦게 뜸,
#                   로봇이 아직 WORKING 이 아니라 NavigationExec 이 안 도는 동안)
#      BT 쪽      : 명령은 닿았는데 **주행이 도착 없이 끝난** 경우 (nav2 ABORTED 등)
#    BT 는 자기가 못 받은 명령을 다시 보낼 수 없고, FMS 는 로봇이 도착했는지 모른다.
#    같은 목적지가 다시 와도 BT 는 목적지가 안 바뀌었으면 goal 을 새로 내지 않으므로
#    둘이 겹쳐도 주행을 끊지 않는다.
NAV_RESEND_SEC = float(os.environ.get("LIBI_NAV_RESEND_SEC", "20"))

#: 로봇별 (마지막 목적지, 보낸 시각).
_last_nav: dict[str, tuple] = {}
_nav_lock = threading.Lock()


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
    x, y, yaw = points[-1]
    if not should_send_nav(robot, (round(x, 3), round(y, 3)), time.monotonic()):
        return

    cmd_id = fleet_telemetry.send_command_async(
        robot, action="navigate", args={"x": x, "y": y, "yaw": yaw},
    )
    if cmd_id:
        log.info("[nav] %s → BT navigate (%.3f, %.3f) cmd=%s", robot, x, y, cmd_id)
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
    cmd_id = fleet_telemetry.send_command_async(robot, action=action, args={})
    if cmd_id:
        log.info("[lifecycle] %s → %s (cmd=%s)", robot, action, cmd_id)
    else:
        log.warning("[lifecycle] %s → %s 전송 실패 (명령 링크 없음)", robot, action)


def on_cmd_result(res: dict) -> None:
    """`/fleet_cmd_result` 훅 — 로봇 BT 가 끝낸 팔 다리를 orchestrator 에 알린다.

    `send_command_async` 가 돌려준 id 를 그대로 다리의 cmd_id 로 쓰므로 매핑 표가 없다.
    우리 것이 아닌 id 는 orchestrator 가 알아서 무시한다(on_result 가 조용히 넘어간다).
    """
    cmd_id = str(res.get("id") or "")
    if not cmd_id:
        return
    ok = bool(res.get("ok"))
    log.info("[arm] BT 결과 cmd=%s ok=%s status=%s", cmd_id, ok, res.get("status"))
    try:
        _orc().on_result(cmd_id, ok, str(res.get("msg") or ""))
    except Exception:  # noqa: BLE001
        log.exception("[arm] on_result 실패 cmd=%s", cmd_id)


#: 주문을 놓아줄 때 fleet_node 에 넣는 모드.
#  PATROL 이면 fleet_node 의 on_timer 가 곧바로 순회를 다시 부여한다 — 주문이 사라졌는데
#  로봇만 목적지로 계속 가는 상태를 막는다.
RELEASE_MODE = os.environ.get("LIBI_RELEASE_MODE", "PATROL")


def real_release(robot: str) -> None:
    """주문이 취소·실패했을 때 로봇을 fleet_node 에서 놓아준다.

    ⚠️ **왜 set_robot_mode 인가**: fleet_node 에는 전용 취소 서비스가 없다. `cancel_task()`
    는 `/fms/set_mode` 핸들러 안에서만 불린다(mode != RETURNING 이면 현재 task 취소 +
    점유 노드 해제). 그래서 지금은 이게 로봇을 멈추는 유일한 경로다.
    C++ 에 전용 cancel 서비스를 넣는 게 더 깔끔하지만, 배차·교통 로직을 건드리지 않으려고
    기존 계약을 그대로 쓴다.

    RELEASE_MODE=PATROL 을 주면 취소 즉시 순회로 돌아간다. 그 뒤에는 로봇 자신의
    FsmState 가 다시 정본이 된다(on_fsm_state 가 robot_mode_ 를 덮어쓴다).
    """
    if not robot:
        return
    with _nav_lock:
        _last_nav.pop(robot, None)      # 놓아준 로봇의 목적지 기억도 지운다
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


def _leg_summary(leg) -> tuple[str, str]:
    """(다리 종류, 사람이 읽을 한 줄). leg 가 없으면 빈 값."""
    if leg is None:
        return "", ""
    kind = getattr(leg.type, "value", str(leg.type))
    params = leg.params or {}
    where = str(params.get("waypoint") or params.get("at") or "")
    what = {"pick": "책 집기", "place": "책 놓기"}.get(str(params.get("action") or ""), "작업")
    text = _ARRIVAL_TEXT.get(kind, "{what}").format(where=where or "목적지", what=what)
    return kind, text


def on_orchestrator_event(kind: str, task, leg) -> None:
    """orchestrator 의 사건을 화면이 읽을 모양으로 바꿔 발행한다.

    **여기서 예외를 내면 안 된다** — 이 함수는 오케스트레이터 락 안에서 불린다.
    (`fleet_events.publish` 자체도 예외를 삼키지만, 그 앞 변환에서 터질 수 있다.)
    """
    try:
        leg_kind, text = _leg_summary(leg)
        if kind == "task_done":
            text = "배달 완료"
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

    # 고아 task 화해 — 백엔드만 재기동한 경우를 자동으로 되돌린다.
    if RECONCILE_SEC > 0:
        _reconcile_stop.clear()
        threading.Thread(target=_reconcile_loop, daemon=True,
                         name="fleet-reconcile").start()
    log.info(
        "[dispatch] 실배선 활성화 (arm_stub=%s, delay=%.1fs)",
        ARM_STUB, ARM_STUB_DELAY_SEC,
    )
