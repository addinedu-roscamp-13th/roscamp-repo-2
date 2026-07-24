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
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser, Book, DeliveryRequest, Loan, Member, Reservation
from ..security import get_current_admin, hash_password

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


class CreateMemberRequest(BaseModel):
    username: str
    full_name: str | None = None
    password: str


class UpdateMemberRequest(BaseModel):
    full_name: str | None = None
    password: str | None = None
    is_active: bool | None = None


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


def _member_row(db: Session, member: Member) -> MemberRow:
    active_loans = db.scalar(
        select(func.count(Loan.id)).where(
            Loan.member_id == member.id, Loan.status == "borrowed"
        )
    )
    total_loans = db.scalar(
        select(func.count(Loan.id)).where(Loan.member_id == member.id)
    )
    return MemberRow(
        id=member.id,
        username=member.username,
        full_name=member.full_name,
        is_active=bool(member.is_active),
        created_at=member.created_at,
        active_loans=active_loans or 0,
        total_loans=total_loans or 0,
    )


@router.post(
    "/members", response_model=MemberRow, status_code=status.HTTP_201_CREATED
)
def create_member(
    body: CreateMemberRequest,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """사서용 회원 등록 — 현장에서 신규 회원을 바로 만든다."""
    if db.scalar(select(Member).where(Member.username == body.username)) is not None:
        raise HTTPException(status_code=409, detail="이미 존재하는 아이디입니다")

    member = Member(
        username=body.username,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        is_active=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return _member_row(db, member)


@router.patch("/members/{member_id}", response_model=MemberRow)
def update_member(
    member_id: int,
    body: UpdateMemberRequest,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """회원 정보 수정 — 이름, (선택) 비밀번호 재설정, (선택) 활성/비활성 전환.

    비활성 회원은 로그인만 막힌다(`member_auth.py`) — 대출/이력은 그대로 남고,
    삭제(`DELETE`, hard delete)와 달리 언제든 다시 활성화할 수 있다.
    """
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")

    if body.full_name is not None:
        member.full_name = body.full_name
    if body.password:
        member.hashed_password = hash_password(body.password)
    if body.is_active is not None:
        member.is_active = body.is_active
    db.commit()
    db.refresh(member)
    return _member_row(db, member)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_member(
    member_id: int,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """회원 삭제(hard delete). 처리 중인 대출/요청/예약이 있으면 막는다.

    ⚠️ Loan/Reservation/Wishlist/DeliveryRequest 가 전부 `ondelete="CASCADE"`로
    이 회원을 물고 있어, 삭제하면 과거 대출·요청 이력까지 함께 지워지고 복구할 수 없다.
    """
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")

    has_open_loan = (
        db.scalar(
            select(Loan.id).where(
                Loan.member_id == member.id, Loan.status == "borrowed"
            )
        )
        is not None
    )
    has_pending_request = (
        db.scalar(
            select(DeliveryRequest.id).where(
                DeliveryRequest.member_id == member.id,
                DeliveryRequest.approval == "PENDING_APPROVAL",
            )
        )
        is not None
    )
    has_waiting_reservation = (
        db.scalar(
            select(Reservation.id).where(
                Reservation.member_id == member.id,
                Reservation.status == "waiting",
            )
        )
        is not None
    )
    if has_open_loan or has_pending_request or has_waiting_reservation:
        raise HTTPException(
            status_code=409,
            detail="처리 중인 대출/요청/예약이 있어 삭제할 수 없습니다",
        )

    db.delete(member)
    db.commit()


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
    if book.unavailable:
        raise HTTPException(
            status_code=409, detail="훼손/분실 처리되어 대출할 수 없는 도서입니다"
        )

    # 체크와 반영을 한 UPDATE로 묶는다 — read-then-write 로 두면 동시에 같은 책을
    # 대출하는 두 세션이 둘 다 통과할 수 있다. rowcount == 0 이면 그 사이 누가 먼저
    # 가져간 것이다(이미 in_stock=False).
    result = db.execute(
        update(Book)
        .where(Book.id == book.id, Book.in_stock.is_(True))
        .values(in_stock=False)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="이미 대출 중인 도서입니다")

    loan = Loan(
        member_id=member.id,
        book_id=book.id,
        status="borrowed",
        borrowed_at=datetime.now(),
        due_at=datetime.now() + timedelta(days=LOAN_DAYS),
    )
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
    category: str | None = None,
    include_unavailable: bool = False,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """대출 처리 화면에서 고를 수 있는 도서(재고 있고 훼손/분실 아닌 것).

    `include_unavailable=true` 면 대출중/훼손·분실 도서도 같이 보여준다(선택은 못 하게
    프론트에서 막고, 상태 표시용). 기본값은 그대로 재고 있는 것만 — 기존 화면들이
    이 응답을 바로 "고를 수 있는 도서"로 쓰기 때문에 기본 동작은 바꾸지 않는다.
    """
    stmt = select(Book)
    if not include_unavailable:
        stmt = stmt.where(Book.in_stock.is_(True), Book.unavailable.is_(False))
    if q and q.strip():
        # 띄어쓰기 차이로 검색이 실패하지 않도록 제목·검색어 둘 다 공백을 지우고 비교한다
        # (예: "어린왕자" 검색이 "어린 왕자" 도서를 못 찾던 문제).
        like = f"%{q.strip().replace(' ', '')}%"
        stmt = stmt.where(func.replace(Book.title_kr, " ", "").like(like))
    if category:
        stmt = stmt.where(Book.category == category)
    rows = db.scalars(stmt.order_by(Book.title_kr).limit(300)).all()
    return [
        {
            "id": b.id,
            "title": b.title_kr,
            "author": b.author,
            "category": b.category,
            "zone": b.zone,
            "in_stock": b.in_stock,
            "unavailable": b.unavailable,
        }
        for b in rows
    ]
