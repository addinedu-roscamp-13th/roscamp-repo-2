"""회원 도서 요청 — 로봇 배달 창구.

## 두 가지 요청
- **열람 요청(read)**: 로봇이 서가에서 책을 집어 **회원이 앉은 테이블**로 가져다 준다.
  대출이 아니다. 다 보면 자리에 두거나 반납대에 올린다.
- **대여 요청(borrow)**: 로봇이 책을 **안내데스크로 이송**한다. 여기까지가 로봇의 일이고,
  **실제 대출 확정은 사서**가 한다(`cb_loans` 행은 사서 화면에서 생긴다).

## fail-closed
FMS 가 주문을 받지 못하면 접수 기록을 남기지 않는다. 관제가 모르는 요청이 도는 것보다
거절이 낫다(`admin_follow.py` 와 같은 원칙).

## 대여 중인 책
`in_stock=False` 면 요청 자체를 막고 예약으로 안내한다 — 로봇이 없는 책을 찾으러 가면 안 된다.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import fms_client
from ..database import get_db
from ..member_security import get_current_member
from ..models import Book, DeliveryRequest, Member

router = APIRouter(prefix="/api/member/request", tags=["member-request"])

#: 대여 요청이 도착하는 곳 — waypoint.yaml 의 실제 철자(원본 오타 그대로여야 정점을 찾는다).
PICKUP_DESK = "안네데스크"

#: 열람 요청으로 고를 수 있는 자리. waypoint.yaml 의 테이블 정점만 허용한다(화이트리스트).
ALLOWED_TABLES = {
    "테이블-1번-상",
    "테이블-1번-좌",
    "테이블-1번-우",
    "테이블-2번-하",
    "테이블-2번-좌",
    "테이블-2번-우",
}


class ReadRequest(BaseModel):
    book_id: int
    table: str


class BorrowRequest(BaseModel):
    book_id: int


class RequestOut(BaseModel):
    id: int
    book_id: int
    book_title: str
    kind: str
    pickup: str
    dropoff: str
    fms_task_id: str
    created_at: datetime
    # FMS 스냅샷에서 붙여주는 진행 상황(없으면 None) — 우리 DB 의 사본이 아니다.
    status: str | None = None
    leg_idx: int | None = None
    leg_count: int | None = None


def _get_requestable_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="도서를 찾을 수 없습니다")
    if not book.in_stock:
        raise HTTPException(
            status_code=409,
            detail="현재 대여 중인 도서입니다. 예약하시면 반납될 때 알려드릴게요.",
        )
    if not book.zone:
        raise HTTPException(
            status_code=409, detail="이 도서의 서가 위치가 등록되어 있지 않습니다"
        )
    return book


def _submit(
    db: Session, member: Member, book: Book, kind: str, dropoff: str
) -> DeliveryRequest:
    ok, value = fms_client.submit_order(
        book=book.title_kr,
        pickup=book.zone,
        dropoff=dropoff,
        requester=member.username,
    )
    if not ok:
        # FMS 가 못 받았으면 기록도 남기지 않는다(fail-closed).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"지금 요청할 수 없습니다 — {value}",
        )

    row = DeliveryRequest(
        member_id=member.id,
        book_id=book.id,
        kind=kind,
        pickup=book.zone,
        dropoff=dropoff,
        fms_task_id=value,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _to_out(row: DeliveryRequest, title: str) -> RequestOut:
    return RequestOut(
        id=row.id,
        book_id=row.book_id,
        book_title=title,
        kind=row.kind,
        pickup=row.pickup,
        dropoff=row.dropoff,
        fms_task_id=row.fms_task_id,
        created_at=row.created_at,
    )


@router.post("/read", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def request_read(
    body: ReadRequest,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """열람 요청 — 테이블로 배달."""
    if body.table not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail="선택할 수 없는 자리입니다")
    book = _get_requestable_book(db, body.book_id)
    row = _submit(db, current, book, "read", body.table)
    return _to_out(row, book.title_kr)


@router.post("/borrow", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def request_borrow(
    body: BorrowRequest,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """대여 요청 — 안내데스크로 이송. 대출 확정은 사서가 한다."""
    book = _get_requestable_book(db, body.book_id)
    row = _submit(db, current, book, "borrow", PICKUP_DESK)
    return _to_out(row, book.title_kr)


@router.get("s", response_model=list[RequestOut])
def my_requests(
    db: Session = Depends(get_db), current: Member = Depends(get_current_member)
):
    """내 요청 현황. 진행 상황은 FMS 스냅샷에서 붙인다(우리 DB 에 사본을 두지 않는다).

    라우터 prefix 가 `/api/member/request` 이므로 경로가 `/api/member/requests` 가 된다.
    """
    rows = db.scalars(
        select(DeliveryRequest)
        .where(DeliveryRequest.member_id == current.id)
        .order_by(DeliveryRequest.created_at.desc())
        .limit(30)
    ).all()

    ok, orders = fms_client.list_orders()
    by_id = {o.get("id"): o for o in orders} if ok else {}

    out: list[RequestOut] = []
    for row in rows:
        book = db.get(Book, row.book_id)
        item = _to_out(row, book.title_kr if book else "(삭제된 도서)")
        snap = by_id.get(row.fms_task_id)
        if snap:
            item.status = snap.get("status")
            item.leg_idx = snap.get("leg_idx")
            item.leg_count = snap.get("leg_count")
        out.append(item)
    return out
