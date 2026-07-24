"""reset_demo_data — is_demo 마커가 있는 행만 지우고 실제 이력은 남기는지.

`main()`은 자체 SessionLocal 을 만들어서 테스트하기 번거로우므로, 핵심 로직을
세션을 인자로 받는 `reset_demo_data(db)` 로 뽑아 `main()`은 그걸 부르는 얇은
래퍼로만 남긴다(그래야 `db_session` 픽스처로 직접 검증할 수 있다).
"""

from datetime import datetime, timedelta

from app.models import Book, DeliveryRequest, IntrusionEvent, Loan, Member, Reservation
from scripts.reset_demo_data import reset_demo_data


def _make_member(db, username="real_user"):
    from app.security import hash_password

    m = Member(username=username, hashed_password=hash_password("pw"), is_active=True)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _make_book(db, title="실제 책"):
    b = Book(
        title_kr=title, title_en=title, title_zh=title, title_vi=title,
        author="누군가", category="literature", cover="📘",
        color="from-slate-200 to-slate-300", zone="문학-1", shelf="1단",
        in_stock=True, unavailable=False,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def test_실제_대출은_안_지우고_데모_대출만_지운다(db_session):
    member = _make_member(db_session)
    book = _make_book(db_session)
    now = datetime.now()

    real_loan = Loan(
        member_id=member.id, book_id=book.id, status="returned",
        borrowed_at=now - timedelta(days=10), due_at=now - timedelta(days=3),
        returned_at=now - timedelta(days=5), is_demo=False,
    )
    demo_loan = Loan(
        member_id=member.id, book_id=book.id, status="returned",
        borrowed_at=now - timedelta(days=10), due_at=now - timedelta(days=3),
        returned_at=now - timedelta(days=5), is_demo=True,
    )
    db_session.add_all([real_loan, demo_loan])
    db_session.commit()

    reset_demo_data(db_session)

    remaining = db_session.query(Loan).all()
    assert len(remaining) == 1
    assert remaining[0].is_demo is False


def test_실제_침입기록도_보존한다(db_session):
    real_event = IntrusionEvent(source="pinky1", is_demo=False)
    demo_event = IntrusionEvent(source="pinky2", is_demo=True)
    db_session.add_all([real_event, demo_event])
    db_session.commit()

    reset_demo_data(db_session)

    remaining = db_session.query(IntrusionEvent).all()
    assert len(remaining) == 1
    assert remaining[0].source == "pinky1"


def test_실제_대출중인_책은_in_stock을_강제로_되돌리지_않는다(db_session):
    member = _make_member(db_session)
    book = _make_book(db_session)
    now = datetime.now()

    real_borrowed = Loan(
        member_id=member.id, book_id=book.id, status="borrowed",
        borrowed_at=now - timedelta(days=1), due_at=now + timedelta(days=13),
        returned_at=None, is_demo=False,
    )
    db_session.add(real_borrowed)
    book.in_stock = False
    db_session.commit()

    reset_demo_data(db_session)

    db_session.refresh(book)
    assert db_session.query(Loan).filter(Loan.is_demo.is_(False)).count() == 1
    assert book.in_stock is False


def test_예약과_배달요청도_is_demo만_지운다(db_session):
    member = _make_member(db_session)
    book = _make_book(db_session)

    db_session.add_all([
        Reservation(member_id=member.id, book_id=book.id, status="waiting", is_demo=False),
        Reservation(member_id=member.id, book_id=book.id, status="waiting", is_demo=True),
        DeliveryRequest(
            member_id=member.id, book_id=book.id, kind="read",
            pickup="문학-1", dropoff="테이블-1번-좌", is_demo=False,
        ),
        DeliveryRequest(
            member_id=member.id, book_id=book.id, kind="read",
            pickup="문학-1", dropoff="테이블-1번-좌", is_demo=True,
        ),
    ])
    db_session.commit()

    reset_demo_data(db_session)

    assert db_session.query(Reservation).count() == 1
    assert db_session.query(DeliveryRequest).count() == 1
