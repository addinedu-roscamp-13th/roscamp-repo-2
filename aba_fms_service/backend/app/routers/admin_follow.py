"""관리자 추종 요청 — libi_gui 관리자 버튼이 호출하고 응답을 기다린다.

로봇이 로컬에서 임의로 추종을 시작하지 않고 FMS 승인을 거치게 한다. 그래야 FMS 가 이
로봇이 작업 중임을 계속 알고 있고, 중단 시 task_cancelled 보고가 성립한다.

로봇 상태는 fsm_link 캐시에서 읽는다 — 자체 캐시를 두면 로봇의 실제 상태와 조용히 어긋난다.

## 승인 기록(grant)을 따로 두는 이유

추종 제어는 ai_service ↔ 로봇 직결(TCP)로 돌고 libi_modes 의 FSM 을 거치지 않는다. 그래서
추종 중인 로봇도 FSM 상으로는 계속 IDLE/PATROL 로 보인다 — fsm_link 만 봐서는 "얘가 지금
추종 중"인지 알 방법이 없다. 그 공백을 메우는 게 아래 `_grants` 다.

이건 위의 "자체 캐시 금지"와 어긋나지 않는다. grant 는 로봇 상태의 사본이 아니라 **FMS
자신의 사실**("내가 이 로봇에게 언제 승인해줬다")이다. 로봇이 실제로 어떤 상태인지는
여전히 fsm_link 에서만 읽고, /status 는 그 stale 여부를 grant 에 같이 실어 보낸다.

프로세스가 죽어 /release 가 안 오면 grant 가 남는다. 만료를 걸지 않은 건 갱신 신호(heartbeat)가
없어 아무 값이나 정하면 멀쩡한 추종을 끊게 되기 때문이다. 대신 granted_at 과 state_stale 을
그대로 노출해 관제가 판단하도록 한다.
"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import APIRouter
from pydantic import BaseModel

from app import fleet_telemetry, fsm_link

router = APIRouter(prefix="/api/robot/admin-follow", tags=["admin-follow"])

FOLLOW_COMMAND = "follow_admin"

# 추종을 시작할 수 있는 상태 — 유휴하거나 순찰 중일 때만 받는다. WORKING 은 이미 다른
# 태스크를 수행 중이므로 가로채지 않는다.
# ⚠️ INTERACTING 이 빠져 있으면 **패널에서는 추종을 시작할 수 없다.** 관리자 화면에 가려면
#    화면을 눌러야 하고, 그 터치가 곧바로 PATROL → INTERACTING 을 만든다(registry.py:44).
#    그래서 요청이 도착할 때 로봇은 거의 항상 INTERACTING 이다.
#    전이표에도 INTERACTING → WORKING(task_assigned) 간선이 있다(registry.py:47).
ACCEPTING_STATES = frozenset({"IDLE", "PATROL", "INTERACTING"})

# 승인되면 로봇을 이 상태로 옮긴다. IDLE/PATROL -> WORKING 은 전이표의 정식 간선
# (task_assigned)이라 force 가 필요 없다 — ACCEPTING_STATES 가 딱 그 둘인 이유이기도 하다.
FOLLOW_STATE = "WORKING"
# 해제하면 되돌릴 상태. WORKING -> PATROL 은 task_done 간선이라 force 가 필요 없다.
#
# ⚠️ [2026-08-02] **"IDLE" 이었다 — 추종이 끝나면 로봇이 순찰로 갔다가 곧바로 대기로
#    떨어지는 버그의 원인이다.**
#
#    로봇 쪽은 이미 "추종 끝 → 순찰" 로 짜여 있다: `FollowExec._release` 가
#    `NEXT_MODE="PATROL"` 을 예약하고 `RequestTransition` 이 그 tick 에 적용한다
#    (libi_modes/common/working_actions.py). 그런데 거의 같은 시각에 여기서 보낸
#    IDLE 이 로봇의 `state_io.apply_pending()` 에 도착하고, 그 함수는 검사 없이
#    `CURRENT_MODE` 를 덮어쓴다 — 로봇이 방금 정한 PATROL 이 뭉개진다.
#    화면에는 "순찰로 잠깐 갔다가 바로 대기" 로 보인다(실측 2026-08-02).
#
#    게다가 `apply_pending` 은 `HOLD_UNTIL = now + manual_hold_sec` 를 찍는데
#    params.yaml 이 300초라, 그 뒤 **5분 동안 로봇이 스스로 못 빠져나온다.**
#
#    PATROL 로 맞추면 로봇이 스스로 간 곳과 FMS 가 미는 곳이 같아져 경합 자체가
#    사라진다. 배터리가 낮으면 `PatrolBranch` 의 `BatteryCheck` 가 알아서 복귀시킨다.
RELEASE_STATE = "PATROL"

# 로봇이 스스로 들어간 상태 — 추종 해제가 여기서 끌어내면 안 된다.
#   ERROR      : error_code 확인 없이 에러를 지우는 셈이 된다.
#   RETURNING  : 배터리가 떨어져 로봇이 알아서 복귀 중인데 방해하게 된다.
#   CHARGING   : 위와 같다.
# 이 목록에 없으면 복귀 전이를 시도한다. "지금 WORKING 이면" 으로 판단하지 않는 이유는
# fsm_link 캐시가 로봇->브릿지 지연만큼 뒤처지기 때문이다 — 승인 직후 바로 해제하면
# 캐시엔 아직 IDLE 이 남아 있어, WORKING 검사로는 복귀를 건너뛰고 로봇이 WORKING 에 갇힌다.
SELF_ENTERED_STATES = frozenset({"ERROR", "RETURNING", "CHARGING"})

# robot_id -> {"granted_at": float}. ROS 스레드가 아니라 요청 핸들러들만 건드리지만,
# fsm_link 와 같은 방식으로 락을 건다 (uvicorn 워커 스레드가 동시에 들어올 수 있다).
_grants: dict[str, dict[str, float]] = {}
_grants_lock = threading.Lock()


class AdminFollowRequest(BaseModel):
    robot_id: str


class AdminFollowResponse(BaseModel):
    accepted: bool
    command: str | None = None
    reason: str | None = None


class AdminFollowReleaseResponse(BaseModel):
    released: bool
    reason: str | None = None


class FollowGrant(BaseModel):
    robot_id: str
    granted_at: float
    state_stale: bool          # 로봇 FSM 상태 수신이 끊겼는지 (grant 는 살아있어도)


class AdminFollowStatusResponse(BaseModel):
    following: list[FollowGrant]


def _reject(reason: str) -> AdminFollowResponse:
    return AdminFollowResponse(accepted=False, reason=reason)


async def _move_state(robot_id: str, target: str) -> tuple[bool, str]:
    """미션 PC 의 FSM 을 target 으로 옮긴다. (성공여부, 사유) 를 돌려준다.

    request_transition 은 응답을 기다리는 블로킹 호출이라 스레드로 뺀다 — fsm.py 의
    /transition 핸들러와 같은 방식이다. 링크가 없으면 None 이 온다.
    """
    result = await asyncio.to_thread(fsm_link.request_transition, robot_id, target)
    if result is None:
        return False, "미션 PC FSM 링크에 연결할 수 없습니다 (브릿지 미기동 또는 오프라인)."
    if not result.get("accepted"):
        return False, result.get("reason") or f"{target} 전이가 거부되었습니다."
    return True, ""


@router.post("/request", response_model=AdminFollowResponse)
async def request_admin_follow(req: AdminFollowRequest) -> AdminFollowResponse:
    snap = fsm_link.snapshot(req.robot_id)
    if snap is None:
        return _reject(f"알 수 없는 로봇입니다: {req.robot_id}")
    if snap.get("stale"):
        return _reject("로봇 상태 수신이 끊겼습니다. 연결을 먼저 확인하세요.")

    state = snap.get("current_state")
    if state is None:
        return _reject("로봇 상태를 아직 모릅니다.")
    if state == "ERROR":
        return _reject("로봇이 ERROR 상태입니다. 먼저 복구하세요.")
    if state not in ACCEPTING_STATES:
        return _reject(f"{state} 상태에서는 추종을 시작할 수 없습니다.")

    # 추종 중인 로봇도 FSM 은 IDLE/PATROL 이라 위 검사로는 안 걸러진다. grant 로만 알 수 있다.
    # 전이보다 먼저 자리를 잡아둔다 — 전이를 기다리는 동안 들어온 두 번째 요청이 통과하면
    # 같은 로봇에 추종이 두 번 걸린다.
    # 거부 문구는 GUI 가 그대로 띄운다. GUI 자체도 로컬 상태로 "이미 추종 중" 을 막는데,
    # 두 문구가 같으면 어느 쪽이 막은 건지 화면만 봐서는 알 수 없다 — 실제로 그것 때문에
    # 원인을 못 찾았다. 여기서는 관제 기록이라는 걸 드러낸다.
    with _grants_lock:
        if req.robot_id in _grants:
            return _reject("관제에 이 로봇의 추종 승인 기록이 남아 있습니다. 먼저 해제하세요.")
        _grants[req.robot_id] = {"granted_at": time.time()}

    # ① 진행 중이던 주행을 먼저 끊는다.
    #
    # 전이는 `active_command` 를 지우지 않는다. PATROL 에서 왔다면 fleet_node 가 허가한
    # `navigate` 가 blackboard 에 그대로 남아 있어, WORKING 으로 옮기는 순간
    # `WorkingBranch` 의 `NavigationExec` 이 **그 옛 목표를 이어서 실행한다.**
    # 그리고 추종은 `/cmd_vel` 을 직접 미므로, nav2 목표가 살아 있으면 두 제어 주체가
    # 같은 토픽을 다툰다(중재자 없음 — 마지막 메시지가 이긴다).
    #
    # 실측 2026-07-28: 승인은 났는데 로봇이 사람을 따라오지 않고 순회 정점으로 계속 갔다.
    # BT 스냅샷의 RUNNING leaf 가 `FollowExec` 이 아니라 `NavigationExec` 이었다.
    #
    # 실패해도 진행한다 — 정지를 못 보냈다고 추종 자체를 막으면, 링크가 잠깐 흔들릴 때마다
    # 추종을 못 쓰게 된다. 아래 `follow_admin` 이 도착하면 BT 가 선점하며 다시 멈춘다.
    await asyncio.to_thread(
        fleet_telemetry.send_command_async, req.robot_id, "mission_stop", {})

    # ② 로봇을 WORKING 으로 옮긴다. 실패하면 승인 자체를 무른다(fail-closed): 로봇이 IDLE 로
    # 남으면 관제가 유휴로 보고 다른 태스크를 배차할 수 있는데, 실제로는 사람을 따라다니는
    # 중이라 충돌한다. 승인 기록과 로봇 상태는 같이 움직이거나 둘 다 안 움직여야 한다.
    moved, reason = await _move_state(req.robot_id, FOLLOW_STATE)
    if not moved:
        with _grants_lock:
            _grants.pop(req.robot_id, None)
        return _reject(reason)

    # ③ **추종 명령을 실제로 내보낸다.**
    #
    # 여기가 오래 비어 있었다: `FOLLOW_COMMAND` 는 응답 필드로만 쓰였고 아무도 발행하지
    # 않았다(패널에도 `follow_admin` 문자열이 없다). 그래서 로봇은 WORKING 으로만 가고
    # 세션이 안 열려 카메라도 안 켜졌다. `guide.py` 는 처음부터 이 단계를 갖고 있었다.
    #
    # 전이가 된 **뒤에** 보낸다. 순서가 뒤집히면 BT 가 WORKING 이 아닌 채로 명령을 받아
    # `WorkingBranch` 의 `IsMode` 에서 걸러진다(명령만 조용히 버려진다).
    #
    # args 는 비운다 — 세션 역할이 FOLLOW 면 카메라는 앞이 기본값이다(session.py).
    cmd_id = await asyncio.to_thread(
        fleet_telemetry.send_command_async, req.robot_id, FOLLOW_COMMAND, {})
    if cmd_id is None:
        # 명령이 안 나갔으면 되돌린다 — WORKING 에 갇힌 로봇을 남기지 않는다(guide.py 와 같다).
        #
        # ⚠️ [2026-08-02] **`RELEASE_STATE` 를 쓰면 안 된다 — 원래 상태로 되돌려야 한다.**
        #   `RELEASE_STATE`(=PATROL) 는 "추종을 정상적으로 마쳤다 → 순찰 재개" 라는 뜻이다.
        #   여기는 **아무 일도 안 일어난** 경우라, PATROL 을 밀면 IDLE·INTERACTING 에
        #   서 있던 로봇이 **갑자기 순찰 주행을 시작한다**(fleet_node 가 task 없는 PATROL 에
        #   순회 경로를 부여한다). 관리자가 패널 앞에 서 있는데 로봇이 떠나 버린다.
        #   위에서 읽어 둔 `state`(ACCEPTING_STATES 중 하나)로 정확히 되돌린다.
        await _move_state(req.robot_id, state)
        with _grants_lock:
            _grants.pop(req.robot_id, None)
        return _reject("로봇 명령 링크가 없습니다 (브릿지 미기동?).")

    return AdminFollowResponse(accepted=True, command=FOLLOW_COMMAND)


@router.post("/release", response_model=AdminFollowReleaseResponse)
async def release_admin_follow(req: AdminFollowRequest) -> AdminFollowReleaseResponse:
    """추종 종료 보고. 종료는 언제나 받아준다 — GUI 도 FMS 응답을 안 기다리고 먼저 멈춘다.

    승인받은 적 없는 로봇이 보내도 에러가 아니다(released=False). 여기서 막아봐야 양쪽
    기록만 더 어긋난다.
    """
    with _grants_lock:
        existed = _grants.pop(req.robot_id, None) is not None
    if not existed:
        return AdminFollowReleaseResponse(released=False, reason="추종 승인 기록이 없습니다.")

    # 로봇이 스스로 빠져나간 상태(에러·복귀·충전)면 그대로 둔다. 그 외에는 복귀시킨다 —
    # 승인 때 WORKING 으로 옮긴 게 FMS 자신이므로, 캐시가 아직 못 따라잡았더라도 되돌릴
    # 책임은 여기에 있다.
    snap = fsm_link.snapshot(req.robot_id) or {}
    if snap.get("current_state") in SELF_ENTERED_STATES:
        return AdminFollowReleaseResponse(released=True)

    # 전이가 실패해도 released 는 유지한다. 승인 기록을 남겨두면 다시 추종을 시작할 수
    # 없게 되는데, 그건 상태가 안 되돌아간 것보다 나쁘다.
    moved, reason = await _move_state(req.robot_id, RELEASE_STATE)
    if not moved:
        return AdminFollowReleaseResponse(released=True, reason=f"상태 복귀 실패: {reason}")
    return AdminFollowReleaseResponse(released=True)


@router.get("/status", response_model=AdminFollowStatusResponse)
async def admin_follow_status() -> AdminFollowStatusResponse:
    """지금 추종 승인이 살아있는 로봇 목록 — 관제 화면이 읽는다."""
    with _grants_lock:
        items = [(robot_id, g["granted_at"]) for robot_id, g in _grants.items()]

    out = []
    for robot_id, granted_at in items:
        snap = fsm_link.snapshot(robot_id)
        out.append(FollowGrant(
            robot_id=robot_id,
            granted_at=granted_at,
            # 로봇이 캐시에서 아예 사라졌으면 끊긴 것으로 본다 (snapshot None).
            state_stale=snap is None or bool(snap.get("stale")),
        ))
    return AdminFollowStatusResponse(following=out)
