"""회원 대출 현황 · 연장 · 예약 · 위시리스트.

## 경계
- **대출 생성/반납은 여기 없다.** 사서가 확정한다(`/api/admin/circulation/*`).
  회원은 자기 대출을 **읽고 연장**할 수 있을 뿐이다.
- 예약은 **대출 중인 책에만** 건다. 재고가 있으면 그냥 요청하면 되므로 400 으로 막는다.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..member_security import get_current_member
from ..models import Book, Loan, Member, Reservation, Wishlist

router = APIRouter(prefix="/api/member", tags=["member-library"])

#: 연장 1회, 1회당 7일. 도서관 규정이 바뀌면 여기만 고친다.
EXTEND_DAYS = 7
EXTEND_MAX = 1
#: 반납 임박 경고를 띄우기 시작하는 남은 일수.
DUE_SOON_DAYS = 3


class BookBrief(BaseModel):
    id: int
    title: str
    author: str
    cover: str
    zone: str
    in_stock: bool


class LoanOut(BaseModel):
    id: int
    book: BookBrief
    status: str
    borrowed_at: datetime
    due_at: datetime
    returned_at: datetime | None
    extend_count: int
    days_left: int
    overdue: bool
    due_soon: bool
    can_extend: bool


class ReservationOut(BaseModel):
    id: int
    book: BookBrief
    status: str
    created_at: datetime


class WishlistOut(BaseModel):
    id: int
    book: BookBrief
    created_at: datetime


class BookIdRequest(BaseModel):
    book_id: int


def _brief(b: Book) -> BookBrief:
    return BookBrief(
        id=b.id,
        title=b.title_kr,
        author=b.author,
        cover=b.cover,
        zone=b.zone,
        in_stock=bool(b.in_stock),
    )


def _get_book(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="도서를 찾을 수 없습니다")
    return book


def _loan_out(loan: Loan, book: Book) -> LoanOut:
    now = datetime.now()
    days_left = (loan.due_at - now).days
    active = loan.status == "borrowed"
    overdue = active and loan.due_at < now
    return LoanOut(
        id=loan.id,
        book=_brief(book),
        status=loan.status,
        borrowed_at=loan.borrowed_at,
        due_at=loan.due_at,
        returned_at=loan.returned_at,
        extend_count=loan.extend_count,
        days_left=days_left,
        overdue=overdue,
        due_soon=active and not overdue and days_left <= DUE_SOON_DAYS,
        can_extend=active and not overdue and loan.extend_count < EXTEND_MAX,
    )


# ── 대출 ────────────────────────────────────────────────────────────────────


@router.get("/loans", response_model=list[LoanOut])
def my_loans(
    db: Session = Depends(get_db), current: Member = Depends(get_current_member)
):
    """내 대출 — 진행 중이 위, 그 다음 반납 이력."""
    rows = db.scalars(
        select(Loan)
        .where(Loan.member_id == current.id)
        .order_by(Loan.returned_at.is_(None).desc(), Loan.due_at.asc())
    ).all()
    out: list[LoanOut] = []
    for loan in rows:
        book = db.get(Book, loan.book_id)
        if book is None:
            continue  # 도서가 지워진 과거 이력은 건너뛴다
        out.append(_loan_out(loan, book))
    return out


@router.post("/loans/{loan_id}/extend", response_model=LoanOut)
def extend_loan(
    loan_id: int,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    loan = db.get(Loan, loan_id)
    if loan is None or loan.member_id != current.id:
        raise HTTPException(status_code=404, detail="대출 내역을 찾을 수 없습니다")
    if loan.status != "borrowed":
        raise HTTPException(status_code=400, detail="이미 반납된 도서입니다")
    if loan.extend_count >= EXTEND_MAX:
        raise HTTPException(
            status_code=400, detail=f"연장은 {EXTEND_MAX}회까지만 가능합니다"
        )
    if loan.due_at < datetime.now():
        raise HTTPException(
            status_code=400, detail="연체된 도서는 연장할 수 없습니다. 먼저 반납해 주세요"
        )

    loan.due_at = loan.due_at + timedelta(days=EXTEND_DAYS)
    loan.extend_count += 1
    db.commit()
    db.refresh(loan)
    return _loan_out(loan, _get_book(db, loan.book_id))


# ── 예약 ────────────────────────────────────────────────────────────────────


@router.get("/reservations", response_model=list[ReservationOut])
def my_reservations(
    db: Session = Depends(get_db), current: Member = Depends(get_current_member)
):
    rows = db.scalars(
        select(Reservation)
        .where(Reservation.member_id == current.id)
        .order_by(Reservation.created_at.desc())
    ).all()
    out: list[ReservationOut] = []
    for r in rows:
        book = db.get(Book, r.book_id)
        if book is None:
            continue
        out.append(
            ReservationOut(
                id=r.id, book=_brief(book), status=r.status, created_at=r.created_at
            )
        )
    return out


@router.post(
    "/reservations", response_model=ReservationOut, status_code=status.HTTP_201_CREATED
)
def create_reservation(
    body: BookIdRequest,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    book = _get_book(db, body.book_id)
    if book.in_stock:
        raise HTTPException(
            status_code=400,
            detail="지금 대출 가능한 도서입니다. 예약 대신 바로 요청해 주세요",
        )
    dup = db.scalar(
        select(Reservation).where(
            Reservation.member_id == current.id,
            Reservation.book_id == book.id,
            Reservation.status.in_(("waiting", "ready")),
        )
    )
    if dup is not None:
        raise HTTPException(status_code=400, detail="이미 예약한 도서입니다")

    row = Reservation(member_id=current.id, book_id=book.id, status="waiting")
    db.add(row)
    db.commit()
    db.refresh(row)
    return ReservationOut(
        id=row.id, book=_brief(book), status=row.status, created_at=row.created_at
    )


@router.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_reservation(
    reservation_id: int,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    row = db.get(Reservation, reservation_id)
    if row is None or row.member_id != current.id:
        raise HTTPException(status_code=404, detail="예약을 찾을 수 없습니다")
    row.status = "cancelled"
    db.commit()


# ── 위시리스트 ───────────────────────────────────────────────────────────────


@router.get("/wishlist", response_model=list[WishlistOut])
def my_wishlist(
    db: Session = Depends(get_db), current: Member = Depends(get_current_member)
):
    rows = db.scalars(
        select(Wishlist)
        .where(Wishlist.member_id == current.id)
        .order_by(Wishlist.created_at.desc())
    ).all()
    out: list[WishlistOut] = []
    for w in rows:
        book = db.get(Book, w.book_id)
        if book is None:
            continue
        out.append(WishlistOut(id=w.id, book=_brief(book), created_at=w.created_at))
    return out


@router.post("/wishlist", response_model=WishlistOut, status_code=status.HTTP_201_CREATED)
def add_wishlist(
    body: BookIdRequest,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    book = _get_book(db, body.book_id)
    existing = db.scalar(
        select(Wishlist).where(
            Wishlist.member_id == current.id, Wishlist.book_id == book.id
        )
    )
    if existing is not None:
        # 중복 담기는 에러가 아니라 멱등 — 이미 담긴 항목을 그대로 돌려준다.
        return WishlistOut(
            id=existing.id, book=_brief(book), created_at=existing.created_at
        )

    row = Wishlist(member_id=current.id, book_id=book.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return WishlistOut(id=row.id, book=_brief(book), created_at=row.created_at)


@router.delete("/wishlist/{wishlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_wishlist(
    wishlist_id: int,
    db: Session = Depends(get_db),
    current: Member = Depends(get_current_member),
):
    row = db.get(Wishlist, wishlist_id)
    if row is None or row.member_id != current.id:
        raise HTTPException(status_code=404, detail="목록에서 찾을 수 없습니다")
    db.delete(row)
    db.commit()
