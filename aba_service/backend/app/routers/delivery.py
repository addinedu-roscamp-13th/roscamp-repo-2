"""회원 도서 요청 — 로봇 배달 창구.

## 두 가지 요청
- **열람 요청(read)**: 로봇이 서가에서 책을 집어 **회원이 앉은 테이블**로 가져다 준다.
  대출이 아니다. 다 보면 자리에 두거나 반납대에 올린다.
- **대여 요청(borrow)**: 로봇이 책을 **안내데스크로 이송**한다. 여기까지가 로봇의 일이고,
  **실제 대출 확정은 사서**가 한다(`cb_loans` 행은 사서 화면에서 생긴다).

## 승인 절차 — 대여만
| 요청 종류 | 승인 | 흐름 |
|---|---|---|
| 열람(read) | 불필요 | 회원 요청 → 즉시 FMS 주문 → 로봇 |
| 대여(borrow) | **필요** | 회원 요청 → 승인 대기 → 사서 승인 → FMS 주문 → 로봇 |

열람은 책이 관내에 남으므로 되돌리기 쉽고, 대여는 반출이라 사서 확인이 필요하다.
승인/반려는 사서 운영 API(`routers/approvals.py`)가 처리한다.

## fail-closed
FMS 가 주문을 받지 못하면 **접수 기록을 남기지 않는다**. 관제가 모르는 요청이 도는 것보다
거절이 낫다(`admin_follow.py` 와 같은 원칙). 단 이 원칙은 *주문을 내는 순간*에만 적용된다 —
승인 대기 기록은 아직 주문이 아니므로 FMS 와 무관하게 남는다.

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

#: 승인 상태 — `DeliveryRequest.approval` 이 가질 수 있는 값.
PENDING = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

#: 사서 승인이 필요한 요청 종류. 지금은 대여만이다(열람은 관내에 남으므로 불필요).
NEEDS_APPROVAL = {"borrow"}

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
    # 승인 상태 — 우리 DB 가 진짜 주인이다(FMS 와 무관).
    approval: str = APPROVED
    reject_reason: str | None = None
    # FMS 스냅샷에서 붙여주는 진행 상황(없으면 None) — 우리 DB 의 사본이 아니다.
    status: str | None = None
    leg_idx: int | None = None
    leg_count: int | None = None


def _get_requestable_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="도서를 찾을 수 없습니다")
    if book.unavailable:
        raise HTTPException(
            status_code=409, detail="훼손/분실 처리되어 대출할 수 없는 도서입니다"
        )
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


def dispatch_to_fms(book: Book, dropoff: str, requester: str) -> str:
    """FMS 에 배달 주문을 내고 task_id 를 돌려준다. 실패하면 503 을 올린다(fail-closed).

    사서 승인(`routers/approvals.py`)도 이 함수를 그대로 쓴다 — 주문을 내는 경로는 하나여야
    한다. 두 곳에서 각자 FMS 를 부르면 fail-closed 규칙이 한쪽에서만 지켜지는 사고가 난다.
    """
    ok, value = fms_client.submit_order(
        book=book.title_kr,
        pickup=book.zone,
        dropoff=dropoff,
        requester=requester,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"지금 요청할 수 없습니다 — {value}",
        )
    return value


def _submit_now(
    db: Session, member: Member, book: Book, kind: str, dropoff: str
) -> DeliveryRequest:
    """승인이 필요 없는 요청 — 지금 바로 FMS 주문을 내고 접수 기록을 남긴다."""
    task_id = dispatch_to_fms(book, dropoff, member.username)
    row = DeliveryRequest(
        member_id=member.id,
        book_id=book.id,
        kind=kind,
        pickup=book.zone,
        dropoff=dropoff,
        fms_task_id=task_id,
        approval=APPROVED,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _submit_pending(
    db: Session, member: Member, book: Book, kind: str, dropoff: str
) -> DeliveryRequest:
    """승인이 필요한 요청 — **FMS 를 부르지 않고** 대기 기록만 남긴다.

    여기서 FMS 를 부르면 사서가 반려해도 로봇이 이미 움직인 뒤가 된다.
    """
    row = DeliveryRequest(
        member_id=member.id,
        book_id=book.id,
        kind=kind,
        pickup=book.zone,
        dropoff=dropoff,
        fms_task_id="",
        approval=PENDING,
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
        approval=row.approval,
        reject_reason=row.reject_reason,
    )


@router.post("/read", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def request_read(
    body: ReadRequest,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """열람 요청 — 테이블로 배달. 승인 없이 바로 나간다."""
    if body.table not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail="선택할 수 없는 자리입니다")
    book = _get_requestable_book(db, body.book_id)
    row = _submit_now(db, current, book, "read", body.table)
    return _to_out(row, book.title_kr)


@router.post("/borrow", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def request_borrow(
    body: BorrowRequest,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """대여 요청 — **사서 승인 대기**로 접수한다. 승인 시점에 FMS 주문이 나간다."""
    book = _get_requestable_book(db, body.book_id)
    row = _submit_pending(db, current, book, "borrow", PICKUP_DESK)
    return _to_out(row, book.title_kr)


class NotificationOut(BaseModel):
    """회원에게 보여줄 알림 한 줄."""

    seq: int
    ts: float
    kind: str          # leg_done | task_done | task_failed
    text: str          # "테이블-1번-좌 도착" / "배달 완료"
    task_id: str
    book_title: str = ""


@router.get("/notifications", response_model=list[NotificationOut])
def my_notifications(
    since: int = 0,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """**내** 주문에서 일어난 사건만. `since` 에 마지막으로 본 seq 를 준다.

    ## 왜 상태 조회(`/requests`)로는 안 되나

    거기서 오는 건 "지금 EXECUTING 이다" 같은 **상태**다. 책이 자리에 도착한 순간과
    도착한 지 10분 지난 순간이 똑같이 보인다 — 알림을 띄울 근거가 없다.

    ## 왜 서버가 걸러 주나

    FMS 사건에는 **모든 회원의 주문**이 섞여 있다. 그대로 내려보내면 남의 배달 알림이
    보인다. 그래서 내 `fms_task_id` 에 속한 것만 남긴다.
    """
    rows = db.scalars(
        select(DeliveryRequest)
        .where(DeliveryRequest.member_id == current.id)
        .where(DeliveryRequest.fms_task_id != "")
        .order_by(DeliveryRequest.created_at.desc())
        .limit(30)
    ).all()
    mine = {r.fms_task_id: r for r in rows}
    if not mine:
        return []

    ok, events, _ = fms_client.list_events(since)
    if not ok:
        return []              # FMS 가 없어도 화면은 떠야 한다

    titles: dict[int, str] = {}
    out: list[NotificationOut] = []
    for e in events:
        row = mine.get(str(e.get("task_id") or ""))
        if row is None:
            continue           # 남의 주문
        if row.book_id not in titles:
            book = db.get(Book, row.book_id)
            titles[row.book_id] = book.title_kr if book else ""
        out.append(NotificationOut(
            seq=int(e.get("seq", 0)), ts=float(e.get("ts", 0.0)),
            kind=str(e.get("kind", "")), text=str(e.get("text", "")),
            task_id=str(e.get("task_id", "")), book_title=titles[row.book_id],
        ))
    return out


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

    # 아직 주문이 안 나간 건(승인 대기·반려)만 있으면 FMS 를 부를 이유가 없다.
    need_snapshot = any(r.fms_task_id for r in rows)
    ok, orders = fms_client.list_orders() if need_snapshot else (False, [])
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


@router.delete("s/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_request(
    request_id: int,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    """내 요청 이력 1건 삭제.

    라우터 prefix 때문에 실제 경로는 `/api/member/requests/{id}` 다.

    남의 요청은 **존재 여부도 알려주지 않는다**(403 이 아니라 404). 승인 대기 중인
    요청은 사서 승인 큐에 걸려 있으므로 막는다 — 회원이 지워도 사서 화면에는 남아
    양쪽이 어긋난다. 승인됐지만 로봇이 아직 옮기는 중인 요청도 막는다 — FMS 가
    아직 그 주문을 들고 있으면(터미널 상태가 아니면) 지워도 로봇은 계속 움직이는데
    회원은 그 사실을 알 방법이 없어진다.
    """
    row = db.get(DeliveryRequest, request_id)
    if row is None or row.member_id != current.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "요청을 찾을 수 없습니다")
    if row.approval == PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "사서 승인을 기다리는 요청은 삭제할 수 없습니다"
        )
    if row.fms_task_id:
        ok, orders = fms_client.list_orders()
        if ok:
            order = next((o for o in orders if o.get("id") == row.fms_task_id), None)
            if order is not None and order.get("status") not in ("COMPLETED", "FAILED"):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "로봇이 아직 옮기는 중인 요청은 삭제할 수 없습니다",
                )
    db.delete(row)
    db.commit()
