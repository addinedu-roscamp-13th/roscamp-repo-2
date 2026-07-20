"""배달 주문 API — orchestrator(composite task 시퀀서) 위의 HTTP 창구.

각 UI(사서/회원·libi_gui·관제)가 여기로 주문을 넣고(통일 창구), 관제 패널이 큐·진행을
읽고 자동/수동 배차·force-advance 로 디버깅한다.

- POST /api/fleet/order              주문 접수(배달) → task_id
- GET  /api/fleet/orders             전체 task 스냅샷 (패널 표시)
- GET  /api/fleet/orders/pending     대기 큐(미배정)
- POST /api/fleet/order/{id}/assign  배차(로봇 지정=수동, 자동은 dispatcher가 이 호출을 대신)
- POST /api/fleet/order/{id}/advance [디버그] 현재 다리 강제완료 → 다음 (로봇 없이 시퀀스 검증)
- POST /api/fleet/order/{id}/cancel  취소
- POST /api/fleet/order/{id}/result  [내부/배선] 다리 완료 보고 주입 (cmd_id, ok)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app import fleet_orchestrator_service as svc
from app.deps import get_current_admin
from app.models import Admin

router = APIRouter(prefix="/api/fleet", tags=["fleet-order"])


class OrderRequest(BaseModel):
    book: str
    pickup: str            # 선반 waypoint (도서→선반→waypoint 는 상위 데이터 계층, #27)
    dropoff: str           # 배달지 waypoint(고정 세트)
    requester: str = ""
    priority: int = 0


class AssignRequest(BaseModel):
    robot: str


class ResultRequest(BaseModel):
    cmd_id: str
    ok: bool
    msg: str = ""


@router.post("/order")
async def create_order(body: OrderRequest, _: Admin = Depends(get_current_admin)):
    tid = svc.orchestrator().submit_delivery(
        book=body.book, pickup=body.pickup, dropoff=body.dropoff,
        requester=body.requester, priority=body.priority)
    return {"ok": True, "task_id": tid}


@router.get("/orders")
async def list_orders(_: Admin = Depends(get_current_admin)):
    return {"orders": svc.orchestrator().snapshot()}


@router.get("/orders/pending")
async def list_pending(_: Admin = Depends(get_current_admin)):
    return {"pending": svc.orchestrator().pending()}


@router.post("/order/{task_id}/assign")
async def assign_order(task_id: str, body: AssignRequest, _: Admin = Depends(get_current_admin)):
    try:
        svc.orchestrator().assign(task_id, body.robot)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"없는 주문: {task_id}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"ok": True, "task": svc.orchestrator().get(task_id).snapshot()}


@router.post("/order/{task_id}/advance")
async def advance_order(task_id: str, _: Admin = Depends(get_current_admin)):
    """[디버그 전용] 현재 다리를 완료로 치고 다음. 운영 중 쓰면 로봇과 어긋난다."""
    try:
        svc.orchestrator().force_advance(task_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"없는 주문: {task_id}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"ok": True, "task": svc.orchestrator().get(task_id).snapshot()}


@router.post("/order/{task_id}/cancel")
async def cancel_order(task_id: str, _: Admin = Depends(get_current_admin)):
    try:
        svc.orchestrator().cancel(task_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"없는 주문: {task_id}")
    return {"ok": True, "task": svc.orchestrator().get(task_id).snapshot()}


@router.post("/order/result")
async def report_result(body: ResultRequest, _: Admin = Depends(get_current_admin)):
    """다리 완료 보고 주입 — 실제로는 fleet task_state/fleet_cmd_result 배선이 부른다."""
    svc.orchestrator().on_result(body.cmd_id, body.ok, body.msg)
    return {"ok": True}
