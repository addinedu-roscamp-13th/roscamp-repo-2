"""길잡이 요청 — libi_gui 터치패널이 호출한다.

## 왜 패널이 `/fleet_cmd` 를 직접 쏘지 않고 여기로 오나

패널이 로봇 토픽에 직접 명령을 넣으면 세 가지가 한꺼번에 깨진다.

  1. **계층 위반** — `goto`/`goal` 은 실행 층이라 `fleet_link` 가 곧바로 nav2 로 내린다.
     BT 를 건너뛰므로 `GuideExec` 이 안 돌고, 요청자를 놓쳐도 아무도 멈추지 않는다.
     (`test_nav_command.py` 의 계약 주석: "navigate 는 BT 소유라 실행기가 실행하면 안 된다")
  2. **봉투가 없다** — `fleet_link.on_cmd` 는 `id` 없는 명령을 **조용히 버린다**.
     봉투(id/ts)를 만드는 건 `fleet_telemetry` 쪽이다.
  3. **순서가 없다** — 전이(WORKING)와 주행을 서로 다른 채널로 보내면 어느 쪽이 먼저
     도착할지 보장되지 않는다. 여기서는 한 요청 안에서 전이 → 명령 순으로 처리한다.

그래서 패널은 **무엇을 원하는지만** 말하고(정점 이름), 좌표 해석·전이·명령 발행은 FMS 가
한 작업으로 소유한다. 관리자 추종(`admin_follow.py`)이 같은 이유로 같은 모양이다.

## 액션 계층

    패널 → 이 라우터 → /fleet_cmd {action:"guide", args:{x,y,yaw,name}}   ← BT 층
                        BT(GuideExec) → /fleet_cmd {action:"goal", ...}    ← 실행 층 → nav2
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

import logging

from app import fleet_link, fleet_telemetry, fsm_link

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/robot/guide", tags=["guide"])

GUIDE_COMMAND = "guide"
#: 요청자를 놓쳤거나 이용자가 취소했을 때 실제로 멈추는 수단 → ros_bridge.cancel_nav()
STOP_COMMAND = "mission_stop"

# 안내를 받을 수 있는 상태. WORKING 은 이미 다른 태스크 중이라 가로채지 않는다.
# INTERACTING 이 들어 있는 이유는 admin_follow 와 같다 — 패널을 눌러 목적지를 고르는
# 순간 로봇은 이미 INTERACTING 이다. 빼면 패널에서 안내를 시작할 방법이 없다.
ACCEPTING_STATES = frozenset({"IDLE", "PATROL", "INTERACTING"})
GUIDE_STATE = "WORKING"
# [2026-08-02] **"IDLE" → "PATROL". `admin_follow.py` 와 같은 버그였다.**
#
#   로봇 쪽은 안내가 끝나면 스스로 순찰로 간다 — `GuideExec._release` 가
#   `NEXT_MODE="PATROL"` 을 예약하고 `RequestTransition` 이 그 tick 에 적용한다
#   (libi_modes/common/working_actions.py). 그런데 여기서 보낸 IDLE 이 거의 같은
#   시각에 도착해 `state_io.apply_pending()` 에 들어가고, 그 함수는 검사 없이
#   `CURRENT_MODE` 를 덮어쓴다 — 로봇이 방금 정한 PATROL 이 뭉개진다.
#   화면에는 "순찰로 잠깐 갔다가 바로 대기" 로 보인다.
#
#   게다가 `apply_pending` 은 `HOLD_UNTIL = now + manual_hold_sec` 를 찍는데
#   params.yaml 이 300초라, IDLE 로 떨어지면 **5분 동안 스스로 못 빠져나온다.**
#
#   PATROL 로 맞추면 로봇이 스스로 간 곳과 FMS 가 미는 곳이 같아져 경합이 사라진다.
#   `WORKING → PATROL` 은 `task_done` 정식 간선이라 force 도 필요 없다.
#   배터리가 낮으면 `PatrolBranch` 의 `BatteryCheck` 가 알아서 복귀시킨다.
RELEASE_STATE = "PATROL"

# 정본 내비 그래프 — mission_control 이 편집하는 것과 **같은 파일**을 읽는다.
# 여기서 좌표를 따로 들고 있으면 정점을 옮겼을 때 조용히 어긋난다.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHARED_WAYPOINT = (
    _REPO_ROOT
    / "aba_controller/libi_drive_controller/ros_ws/src/pinky_pro"
    / "pinky_navigation/params/waypoint.yaml"
)


class GuideRequest(BaseModel):
    robot_id: str
    #: **정점 이름**이다(예: "과학-인문학서가"). 화면 표시명("과학 서가")이 아니다 —
    #: 둘은 다르고, 표시명을 그대로 보내면 "그런 정점 없음"으로 떨어진다.
    waypoint: str


class ReleaseRequest(BaseModel):
    robot_id: str


async def _move_state(robot_id: str, target: str) -> tuple[bool, str]:
    """FSM 전이. `admin_follow._move_state` 와 같은 계약 — 블로킹이라 스레드로 뺀다."""
    result = await asyncio.to_thread(fsm_link.request_transition, robot_id, target)
    if result is None:
        return False, "미션 PC FSM 링크에 연결할 수 없습니다 (브릿지 미기동 또는 오프라인)."
    if not result.get("accepted"):
        return False, result.get("reason") or f"{target} 전이가 거부되었습니다."
    return True, ""


def _vertex(name: str) -> dict | None:
    if not _SHARED_WAYPOINT.exists():
        return None
    data = yaml.safe_load(_SHARED_WAYPOINT.read_text()) or {}
    v = (data.get("vertices") or {}).get(name)
    if not v:
        return None
    return {"x": float(v["x"]), "y": float(v["y"]), "yaw": float(v.get("yaw", 0.0))}


def _submit_guide_task(robot_id: str, waypoint: str) -> str:
    """안내를 fleet 태스크로 등록한다. 성공하면 task_id, 실패하면 빈 문자열.

    `fleet_dispatch_bridge` 의 NAVIGATE 다리와 **같은 통로**다(`submit_task`) — 다른 건
    `task_type` 뿐이고, 그 값은 관제 화면이 배달과 안내를 구분해 보여주는 데 쓴다.

    실패를 예외로 안 올리는 이유: 호출부가 폴백(직접 `guide` 발행)으로 이어가야 하므로
    "왜 실패했나" 보다 "됐나 안 됐나" 만 알면 된다. 사유는 로그에 남는다.
    """
    # ⚠️ [2026-08-02] **import 도 try 안에 둔다.** 밖에 두면 순환 임포트나 모듈 적재
    #    실패가 이 함수 밖으로 튀어 나가고, `panel_bridge.handle()` 의 포괄 except 가
    #    그걸 받아 패널에만 사유를 돌려준다 — 서버 로그에는 아무 흔적도 안 남는다.
    #    "왜 예약이 안 붙나" 를 로그로 못 쫓게 만드는 자리가 정확히 여기였다.
    try:
        from app.fleet_dispatch_bridge import resolve_vertex
        goal_idx = resolve_vertex(waypoint)
    except Exception as e:      # noqa: BLE001 — 정점 이름이 그래프에 없다
        log.warning("[guide] %s 정점 인덱스를 못 찾았다: %s (%s)", robot_id, waypoint, e)
        return ""
    res = fleet_link.submit_task(
        dropoff=str(goal_idx), robot=robot_id, task_type="guide",
        requester=f"guide:{robot_id}",
    )
    if not res.get("accepted"):
        # ⚠️ **여기서 폴백으로 빠지면 안내가 교통관제 밖에서 돈다.** 노드 예약도 간선
        #    잠금도 없고, 관제 지도에 경로·예약이 **아무것도 안 그려진다** — 화면만 보면
        #    "예약이 왜 안 보이지" 인데 원인은 이 한 줄이다. 사유를 반드시 남긴다.
        log.warning(
            "[guide] %s fleet 태스크 거부: %s → **교통관제 없이** 안내한다"
            "(관제 화면에 경로·예약이 안 보인다)", robot_id, res.get("reason"))
        return ""
    task_id = str(res.get("task_id") or "")
    log.info("[guide] %s fleet 태스크 등록 %s → v%d(%s)", robot_id, task_id, goal_idx, waypoint)
    return task_id


@router.post("/request")
async def request_guide(req: GuideRequest):
    """승인되면 로봇을 WORKING 으로 옮기고 BT 층 `guide` 명령을 보낸다.

    거절 사유를 문장으로 돌려준다 — 패널은 그걸 그대로 띄운다. 실패를 조용히
    성공처럼 돌려주면 화면만 "안내 중"이고 로봇은 가만히 있는 상태가 된다.
    """
    # ⚠️ 진입을 반드시 남긴다. 아래 거절 분기들은 사유를 **패널에만** 돌려주고 로그를
    #    안 남겨서, 서버 로그만 보면 "요청이 온 적 없음" 과 구별되지 않았다.
    log.info("[guide] 요청 수신 robot=%s waypoint=%s", req.robot_id, req.waypoint)
    target = _vertex(req.waypoint)
    if target is None:
        log.warning("[guide] %s 정점을 내비 그래프에서 못 찾았다: %s", req.robot_id, req.waypoint)
        return {"granted": False, "reason": f"'{req.waypoint}' 정점을 내비 그래프에서 찾을 수 없습니다."}

    snap = fsm_link.snapshot(req.robot_id)
    if snap is None:
        log.warning("[guide] %s 알 수 없는 로봇", req.robot_id)
        return {"granted": False, "reason": f"알 수 없는 로봇입니다: {req.robot_id}"}
    if snap.get("stale"):
        log.warning("[guide] %s 상태가 묵었다 (브릿지 끊김?)", req.robot_id)
        return {"granted": False, "reason": "로봇 상태가 오래됐습니다 (브릿지 끊김?)."}
    state = snap.get("current_state")
    if state not in ACCEPTING_STATES:
        log.warning("[guide] %s 지금 상태로는 안내 불가: %s", req.robot_id, state)
        return {"granted": False, "reason": f"지금은 안내를 시작할 수 없습니다 (로봇 상태: {state})."}

    # 동시에 두 사람이 요청하면 둘 다 이 snapshot 검사를 통과할 수 있다. 락을 두지 않는
    # 이유는 **전이표가 이미 원자적으로 걸러 주기 때문**이다 — `libi_modes/registry.py` 에
    # `WORKING -> WORKING` 간선이 없다(IDLE/PATROL/INTERACTING -> WORKING 뿐). 먼저 온 쪽이
    # WORKING 으로 옮기고 나면 두 번째는 여기서 거절 사유를 받아 돌아간다.
    # (admin_follow 의 `_grants_lock` 은 자기 승인 딕셔너리를 지키는 것이지 이 경쟁이 아니다.)
    ok, msg = await _move_state(req.robot_id, GUIDE_STATE)
    if not ok:
        return {"granted": False, "reason": msg}

    # 전이가 된 **뒤에** 주행 명령을 낸다. 순서가 뒤집히면 BT 가 WORKING 이 아닌 채로
    # 명령을 받아 WorkingBranch 의 IsMode 에서 걸러진다(명령만 조용히 버려진다).
    args = dict(target)
    args["name"] = req.waypoint
    # PathRequest -> navigate 중계가 같은 시각에 들어와도 안내의 nav2 소유권을
    # 빼앗지 못하게, 명령을 보내기 *전에* 등록한다. send 실패면 바로 되돌린다.
    fleet_link.set_guide_target(req.robot_id, args)

    # ⚠️ [2026-08-02] **경로는 fleet_node 가 낸다 — 여기서 최종 목적지를 바로 쏘지 않는다.**
    #
    #   예전에는 승인 즉시 최종 목적지를 실은 `guide` 를 한 번 보냈다. 그러면 길잡이가
    #   교통관제 **밖에서** 혼자 몰고 가서 세 가지가 같이 없었다: 노드 예약, 간선 잠금,
    #   관제 지도의 경로선(계획 대상이 아니므로 아무것도 안 그려졌다).
    #
    #   이제 fleet 태스크로 등록만 하고, 실제 주행 명령은 fleet_node 가 허가한 홉을
    #   `fleet_dispatch_bridge.on_path_request` 가 `guide` 로 중계한다. 배달과 **같은
    #   교통관제 코드**를 타므로 계획·시간·점유·지연이 공짜로 따라온다.
    #
    #   ⚠️ 폴백을 남긴다. `fleet_node` 가 안 떠 있거나 정점 인덱스를 못 찾으면 안내가
    #      통째로 죽는다 — 그때는 옛 경로대로 최종 목적지를 직접 보낸다(교통관제 없이).
    #      "관제가 없으면 안내도 없다" 보다 "관제 없이라도 안내는 된다" 가 낫다.
    cmd_id = None
    fleet_task = await asyncio.to_thread(_submit_guide_task, req.robot_id, req.waypoint)
    if fleet_task:
        # 태스크 id 를 cmd_id 자리에 돌려준다 — 패널은 이 값을 상관용으로만 쓴다.
        cmd_id = fleet_task
    else:
        cmd_id = await asyncio.to_thread(
            fleet_telemetry.send_command_async, req.robot_id, GUIDE_COMMAND, args)
    if cmd_id is None:
        # 명령이 안 나갔으면 상태를 되돌린다 — WORKING 에 갇힌 로봇을 남기지 않는다.
        #
        # ⚠️ [2026-08-02] **`RELEASE_STATE` 를 쓰면 안 된다 — 원래 상태로 되돌려야 한다.**
        #   `RELEASE_STATE` 는 "안내를 정상적으로 마쳤다 → 순찰 재개" 라는 뜻이다.
        #   여기는 **아무 일도 안 일어난** 경우다. 그런데 PATROL 을 밀면 IDLE 이나
        #   INTERACTING 에 서 있던 로봇이 **갑자기 순찰 주행을 시작한다** — fleet_node 가
        #   task 없는 PATROL 에 순회 경로를 부여하기 때문이다(codex 지적 2026-08-02).
        #   사람이 패널 앞에 서 있는데 로봇이 떠나 버리는 것이 그 결과다.
        #   위에서 읽어 둔 `state`(ACCEPTING_STATES 중 하나)로 정확히 되돌린다.
        fleet_link.set_guide_target(req.robot_id, None)
        await _move_state(req.robot_id, state)
        return {"granted": False, "reason": "로봇 명령 링크가 없습니다 (브릿지 미기동?)."}

    # fleet_node의 routes는 순회/배차 task용이다. 길잡이는 GuideExec→nav2가 직접
    # 주행하므로 그 배열에 가짜 경로를 넣지 말고, 지도에는 목표만 명시한다.
    return {"granted": True, "cmd_id": cmd_id, "target": args}


@router.post("/release")
async def release_guide(req: ReleaseRequest):
    """안내 취소. **먼저 멈추고** 상태를 되돌린다.

    fail-open 이다 — 전이가 실패해도 정지는 이미 나갔다. 반대로 하면 "취소를 눌렀는데
    로봇이 계속 간다" 가 된다(libi_gui 의 추종 해제와 같은 원칙).
    """
    stopped = await asyncio.to_thread(
        fleet_telemetry.send_command_async, req.robot_id, STOP_COMMAND, {})
    fleet_link.set_guide_target(req.robot_id, None)

    # ⚠️ [2026-08-02] **fleet 태스크도 취소한다 — 안 하면 취소한 안내가 길을 계속 잡는다.**
    #
    #   길잡이가 교통관제를 타면서 승인 때 `submit_task` 로 fleet_node 에 태스크가 생긴다.
    #   여기서 취소를 안 보내면 그 태스크와 CBS 점유가 **그대로 남아** PathRequest 가
    #   계속 나온다 — 이용자가 취소했는데 다른 로봇은 그 길을 못 쓴다
    #   (codex 검토 2026-08-02 P0). nav2 정지(`mission_stop`)는 로봇을 세울 뿐
    #   fleet_node 의 예약과는 아무 상관이 없다.
    #
    #   `real_release` 를 쓰는 이유: 취소는 "안내를 끝낸다" 이므로 PATROL 로 돌아가는
    #   것이 맞다(아래 `_move_state(RELEASE_STATE)` 와 같은 방향). 소실 중 일시정지
    #   (`_pause_guide_reservation`)와 다른 점이 정확히 그거다.
    def _release_fleet() -> None:
        from app.fleet_dispatch_bridge import clear_guide_bookkeeping, real_release
        clear_guide_bookkeeping(req.robot_id)
        real_release(req.robot_id)

    try:
        await asyncio.to_thread(_release_fleet)
    except Exception:   # noqa: BLE001 — fail-open: 해제 실패로 취소 자체를 막지 않는다
        log.exception("[guide] %s fleet 태스크 취소 실패", req.robot_id)

    ok, msg = await _move_state(req.robot_id, RELEASE_STATE)
    return {"stopped": stopped is not None, "released": ok, "msg": msg}
