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

from app import fleet_events, fleet_link
from app.deps import get_current_admin
from app.fsm_model import STATES
from app.models import Admin
from app.security import decode_token

router = APIRouter(prefix="/api/fleet", tags=["fleet"])

# plugins.xml 에 등록된 실제 구현. SetPlugins.srv 주석의 예시 이름은 구버전이라 믿지 않는다.
DISPATCHER_PLUGINS = ["libi_fleet::Auction"]
# 첫 항목이 fleet_node 기본값(CBS + 가중 Space-Time A*). 뒤 둘은 폴백/진단용 수동 교체.
TRAFFIC_PLUGINS = [
    "libi_fleet::CbsTraffic",
    "libi_fleet::ReservationDeadlock",
    "libi_fleet::GrantAllTraffic",
]


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


# ── 도착 알림 ────────────────────────────────────────────────────────────────
#
# 주문 **상태**는 GET /api/fleet/orders 로 폴링하면 되지만, 그걸로는 "방금 도착했다"를
# 표현할 수 없다 — 도착한 순간과 도착한 지 10분 뒤가 화면에서 똑같이 보인다.
# 그래서 **사건**을 따로 낸다. 두 가지 방식을 다 지원한다:
#   push  /api/fleet/ws/events   계속 열려 있는 화면(관제)
#   pull  GET /api/fleet/events  폴링하는 쪽(도서관 웹은 별도 서비스라 소켓이 번거롭다)


@router.get("/events")
async def list_events(since: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      _: Admin = Depends(get_current_admin)):
    """`since` 보다 뒤에 일어난 사건들.

    응답의 `latest` 를 다음 요청의 `since` 로 넘기면 놓치지 않는다. 화면이 잠깐 닫혀
    있었어도 보관된 범위 안이면 뒤늦게 받을 수 있다.
    """
    return {"ok": True, "events": fleet_events.since(since, limit),
            "latest": fleet_events.latest_seq()}


@router.websocket("/ws/events")
async def fleet_events_ws(websocket: WebSocket, token: str = Query(...),
                          since: int = Query(0)):
    """주문 사건 실시간 push — 도착 알림용.

    `/ws/feed` 와 나눠 둔 이유: 그쪽은 `{"snapshot": …}` 만 보내기로 화면과 약속돼
    있어서, 모양이 다른 메시지를 섞으면 기존 화면이 스냅샷을 잃는다.
    """
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=fleet_events.LISTENER_QUEUE_MAX)
    fleet_events.add_listener(loop, queue)
    try:
        # 접속하는 동안 일어난 사건을 먼저 흘려준다 — 재접속으로 알림이 사라지지 않게.
        for event in fleet_events.since(since):
            await websocket.send_json({"ok": True, "event": event})
        while True:
            await websocket.send_json({"ok": True, "event": await queue.get()})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        fleet_events.remove_listener(loop, queue)
