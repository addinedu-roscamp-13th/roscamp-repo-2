"""사서용 — 회원 관리 · 대여/반납 처리.

## 왜 대출은 여기서만 만들어지나
회원 앱의 「대여 신청」은 **로봇이 안내데스크로 책을 가져다 놓는 것**까지다. 실제 대출 확정은
사서가 회원 확인 후 누른다. 그래서 `cb_loans` 행은 이 라우터에서만 생긴다.

대출/반납은 `cb_books.in_stock` 을 함께 뒤집는다 — 그래야 회원 화면에서 대여 중인 책이
요청 차단되고 예약으로 유도된다.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser, Book, Loan, Member, Reservation
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/circulation", tags=["circulation"])

#: 기본 대출 기간.
LOAN_DAYS = 14


class MemberRow(BaseModel):
    id: int
    username: str
    full_name: str | None
    is_active: bool
    created_at: datetime
    active_loans: int
    total_loans: int


class LoanRow(BaseModel):
    id: int
    member_id: int
    member_name: str
    book_id: int
    book_title: str
    status: str
    borrowed_at: datetime
    due_at: datetime
    returned_at: datetime | None
    overdue: bool


class BorrowRequest(BaseModel):
    member_id: int
    book_id: int


@router.get("/members", response_model=list[MemberRow])
def list_members(
    db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    """회원 목록 + 대출 건수. 대출 수는 매번 집계한다(회원 수가 적어 캐시 불필요)."""
    active = dict(
        db.execute(
            select(Loan.member_id, func.count(Loan.id))
            .where(Loan.status == "borrowed")
            .group_by(Loan.member_id)
        ).all()
    )
    total = dict(
        db.execute(
            select(Loan.member_id, func.count(Loan.id)).group_by(Loan.member_id)
        ).all()
    )
    rows = db.scalars(select(Member).order_by(Member.id)).all()
    return [
        MemberRow(
            id=m.id,
            username=m.username,
            full_name=m.full_name,
            is_active=bool(m.is_active),
            created_at=m.created_at,
            active_loans=active.get(m.id, 0),
            total_loans=total.get(m.id, 0),
        )
        for m in rows
    ]


def _loan_row(loan: Loan, member: Member | None, book: Book | None) -> LoanRow:
    return LoanRow(
        id=loan.id,
        member_id=loan.member_id,
        member_name=(member.full_name or member.username) if member else "(삭제됨)",
        book_id=loan.book_id,
        book_title=book.title_kr if book else "(삭제된 도서)",
        status=loan.status,
        borrowed_at=loan.borrowed_at,
        due_at=loan.due_at,
        returned_at=loan.returned_at,
        overdue=loan.status == "borrowed" and loan.due_at < datetime.now(),
    )


@router.get("/loans", response_model=list[LoanRow])
def list_loans(
    member_id: int | None = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    stmt = select(Loan)
    if member_id is not None:
        stmt = stmt.where(Loan.member_id == member_id)
    if active_only:
        stmt = stmt.where(Loan.status == "borrowed")
    rows = db.scalars(
        stmt.order_by(Loan.returned_at.is_(None).desc(), Loan.due_at.asc())
    ).all()
    return [
        _loan_row(loan, db.get(Member, loan.member_id), db.get(Book, loan.book_id))
        for loan in rows
    ]


@router.post("/borrow", response_model=LoanRow, status_code=status.HTTP_201_CREATED)
def borrow(
    body: BorrowRequest,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """대출 확정 — 사서가 회원에게 책을 건네주며 누른다."""
    member = db.get(Member, body.member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")
    book = db.get(Book, body.book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="도서를 찾을 수 없습니다")
    if not book.in_stock:
        raise HTTPException(status_code=409, detail="이미 대출 중인 도서입니다")

    loan = Loan(
        member_id=member.id,
        book_id=book.id,
        status="borrowed",
        borrowed_at=datetime.now(),
        due_at=datetime.now() + timedelta(days=LOAN_DAYS),
    )
    # 재고를 함께 내려야 회원 화면에서 요청이 막히고 예약으로 유도된다.
    book.in_stock = False
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return _loan_row(loan, member, book)


@router.post("/loans/{loan_id}/return", response_model=LoanRow)
def return_loan(
    loan_id: int,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """반납 처리 — 재고를 되돌리고, 기다리던 예약이 있으면 첫 건을 `ready` 로 바꾼다."""
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail="대출 내역을 찾을 수 없습니다")
    if loan.status == "returned":
        raise HTTPException(status_code=400, detail="이미 반납된 도서입니다")

    loan.status = "returned"
    loan.returned_at = datetime.now()
    book = db.get(Book, loan.book_id)
    if book is not None:
        book.in_stock = True

    waiting = db.scalar(
        select(Reservation)
        .where(Reservation.book_id == loan.book_id, Reservation.status == "waiting")
        .order_by(Reservation.created_at.asc())
    )
    if waiting is not None:
        waiting.status = "ready"

    db.commit()
    db.refresh(loan)
    return _loan_row(loan, db.get(Member, loan.member_id), book)


@router.get("/available-books")
def available_books(
    q: str | None = None,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """대출 처리 화면에서 고를 수 있는 도서(재고 있는 것)."""
    stmt = select(Book).where(Book.in_stock.is_(True))
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Book.title_kr.like(like))
    rows = db.scalars(stmt.order_by(Book.title_kr).limit(50)).all()
    return [
        {"id": b.id, "title": b.title_kr, "author": b.author, "zone": b.zone}
        for b in rows
    ]
