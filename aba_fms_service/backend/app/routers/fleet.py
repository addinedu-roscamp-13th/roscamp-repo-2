"""libi_fleet 배차·교통 코어 테스트 API — 태스크 투입, 배터리·상태 조작, 알고리즘 교체.

`fleet_ws` 의 `fleet_node` 를 브라우저에서 두드려 보기 위한 얇은 계층이다. 실시간 갱신은
`/ws/feed` 전용이고 `GET /snapshot` 은 페이지 최초 진입 시 1회용이다 (폴링 API 를 만들지 않는다).

거절 사유(`bad_goal_vertex`, `robot_stopped`, `insufficient_battery`, `bad_mode` …)는
**그대로 통과시킨다.** 이 패널의 목적이 바로 그 사유를 보는 것이므로 요약하거나 번역하지 않는다.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app import fleet_link
from app.deps import get_current_admin
from app.fsm_model import STATES
from app.models import Admin
from app.security import decode_token

router = APIRouter(prefix="/api/fleet", tags=["fleet"])

# plugins.xml 에 등록된 실제 구현. SetPlugins.srv 주석의 예시 이름은 구버전이라 믿지 않는다.
DISPATCHER_PLUGINS = ["libi_fleet::Auction"]
TRAFFIC_PLUGINS = ["libi_fleet::ReservationDeadlock"]


class SubmitTaskRequest(BaseModel):
    dropoff: str = Field(..., min_length=1, max_length=16, description="목표 정점 인덱스")
    robot: str = Field("", max_length=40, description="비우면 dispatcher 가 경매로 선택")
    arm_actions: int = Field(0, ge=0, le=100)
    task_type: str = Field("delivery", max_length=24)


class SetModeRequest(BaseModel):
    robot: str = Field(..., min_length=1, max_length=40)
    mode: str = Field(..., min_length=1, max_length=24)


class SetBatteryRequest(BaseModel):
    robot: str = Field(..., min_length=1, max_length=40)
    value: float = Field(..., ge=0.0, le=100.0)


class SetPluginsRequest(BaseModel):
    dispatcher: str = Field("", max_length=64)
    traffic: str = Field("", max_length=64)


@router.get("/meta")
async def fleet_meta(_: Admin = Depends(get_current_admin)):
    """패널이 렌더링에 필요한 상수. 상태 8종은 libi_modes 정의를 그대로 내려보낸다."""
    return {
        "ok": True,
        "states": list(STATES),
        "dispatchers": DISPATCHER_PLUGINS,
        "traffics": TRAFFIC_PLUGINS,
        "domain_id": fleet_link.FLEET_DOMAIN_ID,
    }


@router.get("/snapshot")
async def fleet_snapshot(_: Admin = Depends(get_current_admin)):
    """최초 진입 1회용. 이후 갱신은 /ws/feed 가 push 한다."""
    return {"ok": True, "snapshot": fleet_link.snapshot()}


@router.post("/task")
async def submit_task(body: SubmitTaskRequest, _: Admin = Depends(get_current_admin)):
    result = fleet_link.submit_task(
        dropoff=body.dropoff,
        robot=body.robot,
        arm_actions=body.arm_actions,
        task_type=body.task_type,
    )
    return {"ok": result["accepted"], **result}


@router.post("/mode")
async def set_mode(body: SetModeRequest, _: Admin = Depends(get_current_admin)):
    """⚠️ sim·디버그 전용. 상태의 소유자는 libi_modes 이며, 운영 중 상태 변경은
    FSM 패널의 전이 요청 경로로 해야 한다. 여기서 바꾸면 로봇의 실제 FSM 과 어긋난다."""
    return fleet_link.set_robot_mode(body.robot, body.mode)


@router.post("/battery")
async def set_battery(body: SetBatteryRequest, _: Admin = Depends(get_current_admin)):
    return fleet_link.set_battery(body.robot, body.value)


@router.post("/plugins")
async def set_plugins(body: SetPluginsRequest, _: Admin = Depends(get_current_admin)):
    return fleet_link.set_plugins(body.dispatcher, body.traffic)


@router.post("/navgraph/reload")
async def reload_navgraph(_: Admin = Depends(get_current_admin)):
    return fleet_link.reload_navgraph()


@router.websocket("/ws/feed")
async def fleet_feed_ws(websocket: WebSocket, token: str = Query(...)):
    """로봇 표·태스크 이력·점유·경로 실시간 push (폴링 아님)."""
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    fleet_link.add_listener(loop, queue)
    try:
        # 접속 즉시 현재 캐시를 한 번 밀어준다 (빈 화면 방지)
        await websocket.send_json({"ok": True, "snapshot": fleet_link.snapshot()})
        while True:
            payload = await queue.get()
            await websocket.send_json({"ok": True, "snapshot": payload})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        fleet_link.remove_listener(loop, queue)
