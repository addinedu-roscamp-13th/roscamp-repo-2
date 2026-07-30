"""배달 주문 API — orchestrator(composite task 시퀀서) 위의 HTTP 창구.

각 UI(사서/회원·libi_gui·관제)가 여기로 주문을 넣고(통일 창구), 관제 패널이 큐·진행을
읽고 자동/수동 배차·force-advance 로 디버깅한다.

- POST /api/fleet/order              주문 접수 → task_id
                                     `kind`: delivery(4다리, 기본) | navigate(주행 1다리)
- GET  /api/fleet/orders             전체 task 스냅샷 (패널 표시)
- GET  /api/fleet/orders/pending     대기 큐(미배정)
- POST /api/fleet/order/{id}/assign  배차(로봇 지정=수동, 자동은 dispatcher가 이 호출을 대신)
- POST /api/fleet/order/{id}/advance [디버그] 현재 다리 강제완료 → 다음 (로봇 없이 시퀀스 검증)
- POST /api/fleet/order/{id}/cancel  취소
- POST /api/fleet/order/{id}/result  [내부/배선] 다리 완료 보고 주입 (cmd_id, ok)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import fleet_orchestrator_service as svc
from app.database import get_admin_db
from app.deps import get_current_admin
from app.models import Admin

router = APIRouter(prefix="/api/fleet", tags=["fleet-order"])

#: 주문 종류 → 다리 구성. 사서 화면의 「작업 종류」 8종은 상위(aba_service)가 이 둘 중
#: 하나로 접어서 보낸다 — 여기는 "몇 다리짜리인가"만 안다.
#:   delivery : 주행 → 집기 → 주행 → 놓기 (이송·진열·짐꾼)
#:   navigate : 주행 (정리·파견)
#:   collect  : 주행(수거함 고정) → 팔×3 (수거). book/pickup/dropoff 전부 없다.
#: 「복귀」·「순찰」은 주문이 아니라 **로봇 모드 변경**이라 여기로 오지 않는다
#: (POST /api/fsm/transition).
ORDER_KINDS = ("delivery", "navigate", "collect")


class OrderRequest(BaseModel):
    #: 배달이면 집을 물건과 집을 곳이 필요하고, 주행이면 목적지만, 수거는 아무것도 필요 없다.
    kind: str = "delivery"
    book: str = ""
    pickup: str = ""       # 선반 waypoint (도서→선반→waypoint 는 상위 데이터 계층, #27)
    #: `collect` 만 비워도 된다 — 수거함에 선 채로 끝나 두 번째 주행 자체가 없다.
    dropoff: str = ""
    requester: str = ""
    priority: int = 0
    #: 서가 좌표 — 팔이 손을 뻗을 층·줄이다. `pickup` 정점은 "어느 서가"까지만 말해준다.
    #
    # 원본은 `aba_service` 의 `books.tier`/`books.row` 이고, 주문을 만드는 쪽이 조회해
    # 실어 보낸다(`delivery.py` 가 이미 `pickup=book.zone` 을 그렇게 보낸다).
    # `0` 은 "층·줄 정보 없음" — 팔이 시각으로 찾는다. 사서가 아직 입력하지 않은 도서가
    # 그 상태다. 여기서 추측해 채우지 않는다(틀린 칸을 잡는 것보다 못 찾는 게 낫다).
    #
    # ⚠️ 범위를 여기서 막는다. 실물이 3층 × 3줄이라 `tier=99` 는 존재하지 않는 칸이고,
    #    로봇 쪽 중계는 범위 밖 좌표를 받으면 goal 을 아예 안 만든다(`arm_task_map`).
    #    그러면 원인이 주문 접수 시점이 아니라 주행 중 실패로 뒤늦게 드러난다 — 들어올 때 막는다.
    tier: int = Field(0, ge=0, le=3)
    row: int = Field(0, ge=0, le=3)

    @model_validator(mode="after")
    def _check_kind(self) -> "OrderRequest":
        """종류에 필요한 값이 다 왔는지 본다.

        pydantic 단계에서 막아야 기존처럼 422 로 거절된다 — 배달 주문에 book 이 빠지면
        예전에도 422 였고, kind 를 도입했다고 그 계약이 바뀌면 안 된다.
        """
        if self.kind not in ORDER_KINDS:
            raise ValueError(f"kind 는 {ORDER_KINDS} 중 하나여야 합니다")
        if self.kind != "collect" and not self.dropoff:
            raise ValueError("dropoff 는 비울 수 없습니다")
        if self.kind == "delivery" and not (self.book and self.pickup):
            raise ValueError("배달 주문은 book 과 pickup 이 필요합니다")
        return self


class AssignRequest(BaseModel):
    robot: str


class ResultRequest(BaseModel):
    cmd_id: str
    ok: bool
    msg: str = ""


@router.post("/order")
async def create_order(body: OrderRequest, _: Admin = Depends(get_current_admin)):
    if body.kind == "navigate":
        tid = svc.orchestrator().submit_navigation(
            dropoff=body.dropoff, requester=body.requester, priority=body.priority)
    elif body.kind == "collect":
        tid = svc.orchestrator().submit_collection(
            requester=body.requester, priority=body.priority)
    else:
        tid = svc.orchestrator().submit_delivery(
            book=body.book, pickup=body.pickup, dropoff=body.dropoff,
            requester=body.requester, priority=body.priority,
            tier=body.tier, row=body.row)
    return {"ok": True, "task_id": tid}


@router.get("/books")
async def list_books(
    db: AsyncSession = Depends(get_admin_db),
    _: Admin = Depends(get_current_admin),
):
    """주문 화면의 「도서」 목록 — aba_service 의 `cb_books` 를 그대로 읽는다.

    ⚠️ FMS 는 cb_* 를 모델로 갖지 않는다(소유자는 aba_service). 같은 MariaDB(`aba`) 안에
    있으니 **읽기만** raw SQL 로 한다 — 여기서 모델을 복제하면 스키마 정본이 두 곳으로 갈린다.

    `zone` 이 곧 waypoint 정점 이름이다(문학서가·예술서가·과학-인문학서가 — waypoint.yaml 과
    글자까지 같다). 그래서 프런트가 책을 고르면 출발지를 별도 매핑 없이 채울 수 있다.
    """
    rows = await db.execute(text(
        "SELECT id, title_kr, author, category, zone, shelf, in_stock, unavailable "
        "FROM cb_books ORDER BY zone, title_kr"
    ))
    return {"books": [dict(r) for r in rows.mappings()]}


def _with_leg_labels(order: dict) -> dict:
    """`Task.snapshot()` 에 `leg_labels` 를 붙인다.

    `OrderSnapshot`(프런트 타입)이 이 필드를 **필수**로 선언하므로, task 스냅샷을 클라이언트에
    돌려주는 곳(`/orders`·`/assign`·`/advance`·`/cancel`)은 전부 이걸 거쳐야 한다 — 한 곳만
    빠뜨리면 그 응답만 타입과 실제 값이 어긋난다.
    """
    from app.fleet_dispatch_bridge import snapshot_leg_label

    order["leg_labels"] = [snapshot_leg_label(leg) for leg in order["legs"]]
    return order


@router.get("/orders")
async def list_orders(_: Admin = Depends(get_current_admin)):
    return {"orders": [_with_leg_labels(o) for o in svc.orchestrator().snapshot()]}


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
    return {"ok": True, "task": _with_leg_labels(svc.orchestrator().get(task_id).snapshot())}


@router.post("/order/{task_id}/advance")
async def advance_order(task_id: str, _: Admin = Depends(get_current_admin)):
    """[디버그 전용] 현재 다리를 완료로 치고 다음. 운영 중 쓰면 로봇과 어긋난다."""
    try:
        svc.orchestrator().force_advance(task_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"없는 주문: {task_id}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"ok": True, "task": _with_leg_labels(svc.orchestrator().get(task_id).snapshot())}


@router.post("/order/{task_id}/cancel")
async def cancel_order(task_id: str, _: Admin = Depends(get_current_admin)):
    try:
        svc.orchestrator().cancel(task_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"없는 주문: {task_id}")
    return {"ok": True, "task": _with_leg_labels(svc.orchestrator().get(task_id).snapshot())}


@router.post("/order/result")
async def report_result(body: ResultRequest, _: Admin = Depends(get_current_admin)):
    """다리 완료 보고 주입 — 실제로는 fleet task_state/fleet_cmd_result 배선이 부른다."""
    svc.orchestrator().on_result(body.cmd_id, body.ok, body.msg)
    return {"ok": True}

@router.delete("/order/{task_id}")
async def dismiss_order(task_id: str, _: Admin = Depends(get_current_admin)):
    """끝난 주문을 큐에서 치운다.

    `cancel` 은 종료 상태(COMPLETED/FAILED/CANCELLED)를 무시하도록 되어 있어서, 한 번
    실패한 주문은 화면에서 없앨 방법이 없었다. 실패 주문이 쌓이면 큐를 읽을 수 없다.
    진행 중인 주문은 지우지 않는다 — 그건 cancel 의 몫이다.
    """
    ok = svc.orchestrator().dismiss(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail="진행 중이거나 없는 주문입니다")
    return {"ok": True, "dismissed": task_id}
