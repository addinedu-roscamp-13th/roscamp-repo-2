"""인기 도서(대출 횟수 랭킹)와 도서 응답의 `unavailable` 노출 검증.

랭킹의 근거 데이터는 `cb_loans` 뿐이다. 대출이 한 건도 없어도 화면은 떠야 하므로
빈 목록이 아니라 '순서만 무의미한 목록'이 나와야 한다.
"""

from datetime import datetime, timedelta

from app.models import Loan
from tests.conftest import make_book


def _lend(db_session, member, book, times: int) -> None:
    """같은 책을 `times` 번 대출한 이력을 만든다."""
    for _ in range(times):
        db_session.add(
            Loan(
                member_id=member.id,
                book_id=book.id,
                status="returned",
                due_at=datetime.now() + timedelta(days=7),
            )
        )
    db_session.commit()


def test_popular_orders_by_loan_count(client, db_session, member):
    cold = make_book(db_session, title="아무도 안 빌린 책")
    warm = make_book(db_session, title="가끔 빌리는 책")
    hot = make_book(db_session, title="제일 많이 빌린 책")
    _lend(db_session, member, warm, 2)
    _lend(db_session, member, hot, 5)

    res = client.get("/api/books/popular?limit=10")

    assert res.status_code == 200
    titles = [b["title"]["KR"] for b in res.json()]
    assert titles.index(hot.title_kr) < titles.index(warm.title_kr)
    assert titles.index(warm.title_kr) < titles.index(cold.title_kr)


def test_popular_respects_limit(client, db_session):
    for i in range(15):
        make_book(db_session, title=f"책{i}")

    res = client.get("/api/books/popular?limit=10")

    assert res.status_code == 200
    assert len(res.json()) == 10


def test_popular_filters_by_category(client, db_session):
    make_book(db_session, title="문학책")
    kid = make_book(db_session, title="그림책")
    kid.category = "kids"
    db_session.commit()

    res = client.get("/api/books/popular?category=kids&limit=10")

    assert res.status_code == 200
    assert [b["title"]["KR"] for b in res.json()] == ["그림책"]


def test_popular_without_any_loan_still_returns_books(client, db_session):
    make_book(db_session, title="대출 이력 없는 책")

    res = client.get("/api/books/popular?limit=10")

    assert res.status_code == 200
    assert len(res.json()) == 1


def test_book_response_exposes_unavailable(client, db_session):
    make_book(db_session, title="훼손된 책", unavailable=True)

    res = client.get("/api/books?limit=10")

    assert res.status_code == 200
    assert res.json()[0]["unavailable"] is True


def test_book_response_keeps_existing_fields(client, db_session):
    make_book(db_session, title="정상 책")

    body = client.get("/api/books?limit=10").json()[0]

    assert body["inStock"] is True
    assert body["unavailable"] is False
    assert body["zone"] == "문학-1"


def test_get_single_book(client, db_session):
    row = make_book(db_session, title="한 권만")

    res = client.get(f"/api/books/{row.id}")

    assert res.status_code == 200
    assert res.json()["title"]["KR"] == "한 권만"


def test_get_missing_book_is_404(client, db_session):
    res = client.get("/api/books/99999")

    assert res.status_code == 404


def test_popular_path_is_not_swallowed_by_single_book_route(client, db_session):
    """`/popular` 가 `book_id='popular'` 로 잡히면 안 된다 — 경로 선언 순서 회귀 방지."""
    make_book(db_session, title="아무 책")

    res = client.get("/api/books/popular?limit=5")

    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_list_filters_by_zone(client, db_session):
    make_book(db_session, title="문학책", zone="문학-1")
    make_book(db_session, title="과학책", zone="과학-1")

    res = client.get("/api/books?zone=문학-1&limit=50")

    assert res.status_code == 200
    assert [b["title"]["KR"] for b in res.json()] == ["문학책"]


def test_list_accepts_multiple_zones(client, db_session):
    make_book(db_session, title="문학1", zone="문학-1")
    make_book(db_session, title="문학2", zone="문학-2")
    make_book(db_session, title="과학1", zone="과학-1")

    res = client.get("/api/books?zone=문학-1&zone=문학-2&limit=50")

    assert res.status_code == 200
    assert sorted(b["title"]["KR"] for b in res.json()) == ["문학1", "문학2"]
