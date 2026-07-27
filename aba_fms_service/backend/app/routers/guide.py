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

from app import fleet_telemetry, fsm_link

router = APIRouter(prefix="/api/robot/guide", tags=["guide"])

GUIDE_COMMAND = "guide"
#: 요청자를 놓쳤거나 이용자가 취소했을 때 실제로 멈추는 수단 → ros_bridge.cancel_nav()
STOP_COMMAND = "mission_stop"

# 안내를 받을 수 있는 상태. WORKING 은 이미 다른 태스크 중이라 가로채지 않는다.
# INTERACTING 이 들어 있는 이유는 admin_follow 와 같다 — 패널을 눌러 목적지를 고르는
# 순간 로봇은 이미 INTERACTING 이다. 빼면 패널에서 안내를 시작할 방법이 없다.
ACCEPTING_STATES = frozenset({"IDLE", "PATROL", "INTERACTING"})
GUIDE_STATE = "WORKING"
RELEASE_STATE = "IDLE"

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


@router.post("/request")
async def request_guide(req: GuideRequest):
    """승인되면 로봇을 WORKING 으로 옮기고 BT 층 `guide` 명령을 보낸다.

    거절 사유를 문장으로 돌려준다 — 패널은 그걸 그대로 띄운다. 실패를 조용히
    성공처럼 돌려주면 화면만 "안내 중"이고 로봇은 가만히 있는 상태가 된다.
    """
    target = _vertex(req.waypoint)
    if target is None:
        return {"granted": False, "reason": f"'{req.waypoint}' 정점을 내비 그래프에서 찾을 수 없습니다."}

    snap = fsm_link.snapshot(req.robot_id)
    if snap is None:
        return {"granted": False, "reason": f"알 수 없는 로봇입니다: {req.robot_id}"}
    if snap.get("stale"):
        return {"granted": False, "reason": "로봇 상태가 오래됐습니다 (브릿지 끊김?)."}
    state = snap.get("current_state")
    if state not in ACCEPTING_STATES:
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
    cmd_id = await asyncio.to_thread(
        fleet_telemetry.send_command_async, req.robot_id, GUIDE_COMMAND, args)
    if cmd_id is None:
        # 명령이 안 나갔으면 상태를 되돌린다 — WORKING 에 갇힌 로봇을 남기지 않는다.
        await _move_state(req.robot_id, RELEASE_STATE)
        return {"granted": False, "reason": "로봇 명령 링크가 없습니다 (브릿지 미기동?)."}

    return {"granted": True, "cmd_id": cmd_id, "target": args}


@router.post("/release")
async def release_guide(req: ReleaseRequest):
    """안내 취소. **먼저 멈추고** 상태를 되돌린다.

    fail-open 이다 — 전이가 실패해도 정지는 이미 나갔다. 반대로 하면 "취소를 눌렀는데
    로봇이 계속 간다" 가 된다(libi_gui 의 추종 해제와 같은 원칙).
    """
    stopped = await asyncio.to_thread(
        fleet_telemetry.send_command_async, req.robot_id, STOP_COMMAND, {})
    ok, msg = await _move_state(req.robot_id, RELEASE_STATE)
    return {"stopped": stopped is not None, "released": ok, "msg": msg}
