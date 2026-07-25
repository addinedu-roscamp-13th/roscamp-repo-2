"""Customer-facing book catalog & recommendations.

Public (no auth) — these power the chatbot's "recommend a book" feature and
the customer search/recommend pages. Data comes from the `cb_books` table so the
bot recommends real, in-stock titles instead of hard-coded mock data.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Book, Loan
from ..schemas import BookOut

router = APIRouter(prefix="/api/books", tags=["books"])

# Categories used by the frontend filters / chat intent mapping.
# ⚠️ 시드(`scripts/seed_books.py`)가 쓰는 5분야와 반드시 같아야 한다 — 여기 없는 값이 오면
# 필터가 조용히 무시되어 "전체 목록"이 돌아간다(예전에 humanities/kids 가 그랬다).
CATEGORIES = {"literature", "art", "science", "humanities", "kids"}


def _parse_tags(raw: str | None) -> list[str]:
    """for_whom_* columns store a JSON array string; degrade to [] on bad data."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return [str(x) for x in value] if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def _to_out(b: Book) -> BookOut:
    return BookOut(
        id=str(b.id),
        title={"KR": b.title_kr, "EN": b.title_en, "ZH": b.title_zh, "VI": b.title_vi},
        author=b.author,
        category=b.category,
        cover=b.cover,
        color=b.color,
        zone=b.zone,
        shelf=b.shelf,
        in_stock=bool(b.in_stock),
        unavailable=bool(b.unavailable),
        summary={
            "KR": b.summary_kr or "",
            "EN": b.summary_en or "",
            "ZH": b.summary_zh or "",
            "VI": b.summary_vi or "",
        },
        for_whom={
            "KR": _parse_tags(b.for_whom_kr),
            "EN": _parse_tags(b.for_whom_en),
            "ZH": _parse_tags(b.for_whom_zh),
            "VI": _parse_tags(b.for_whom_vi),
        },
    )


def _keyword_filter(stmt, q: str):
    like = f"%{q.strip()}%"
    return stmt.where(
        or_(
            Book.title_kr.like(like),
            Book.title_en.like(like),
            Book.title_zh.like(like),
            Book.author.like(like),
            Book.summary_kr.like(like),
            Book.summary_en.like(like),
            Book.for_whom_kr.like(like),
        )
    )


@router.get("/popular", response_model=list[BookOut])
def popular(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
):
    """대출 횟수 기준 인기 도서.

    랭킹 근거는 `cb_loans` 뿐이다. 대출 이력이 없는 책도 0회로 함께 나오게
    outer join 한다 — 시드 직후처럼 이력이 비어 있어도 화면이 비지 않아야 한다.
    동점은 (대출가능 우선, 최근 입고 우선)으로 안정적으로 갈라 매번 같은 순서를 준다.
    """
    counts = (
        select(Loan.book_id.label("book_id"), func.count(Loan.id).label("cnt"))
        .group_by(Loan.book_id)
        .subquery()
    )
    stmt = (
        select(Book)
        .outerjoin(counts, counts.c.book_id == Book.id)
        .order_by(
            func.coalesce(counts.c.cnt, 0).desc(),
            Book.in_stock.desc(),
            Book.id.desc(),
        )
        .limit(limit)
    )
    if category and category in CATEGORIES:
        stmt = stmt.where(Book.category == category)
    return [_to_out(b) for b in db.scalars(stmt).all()]


@router.get("/recommend", response_model=list[BookOut])
def recommend(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None, description="fiction|self|foreign|humanities|economy|poetry"),
    q: str | None = Query(default=None, description="keyword across title/author/summary/tags"),
    limit: int = Query(default=5, ge=1, le=20),
    in_stock_only: bool = Query(default=True, description="only recommend borrowable books"),
):
    """Recommend books from the DB. Prefers in-stock titles, randomized for variety."""
    stmt = select(Book)
    if category and category in CATEGORIES:
        stmt = stmt.where(Book.category == category)
    if q and q.strip():
        stmt = _keyword_filter(stmt, q)
    if in_stock_only:
        stmt = stmt.where(Book.in_stock.is_(True))

    # in-stock first, then random for variety on repeat asks
    stmt = stmt.order_by(Book.in_stock.desc(), func.rand()).limit(limit)
    rows = db.scalars(stmt).all()

    # If a strict filter found nothing, fall back to any in-stock book.
    if not rows and (category or q):
        rows = db.scalars(
            select(Book).where(Book.in_stock.is_(True)).order_by(func.rand()).limit(limit)
        ).all()

    return [_to_out(b) for b in rows]


@router.get("", response_model=list[BookOut])
def list_books(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    zone: list[str] | None = Query(default=None, description="서가 정점 이름. 여러 번 줄 수 있다"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search / list catalog books (title, author, summary, tags).

    `zone` 은 지도 화면이 쓴다. 예전에는 전체 목록을 받아 클라이언트에서 걸렀는데,
    상한(200)을 넘는 장서에서는 뒤쪽 책이 통째로 빠진다 — 거르는 일을 DB 에 맡긴다.
    """
    stmt = select(Book)
    if category and category in CATEGORIES:
        stmt = stmt.where(Book.category == category)
    if zone:
        stmt = stmt.where(Book.zone.in_(zone))
    if q and q.strip():
        stmt = _keyword_filter(stmt, q)
    stmt = stmt.order_by(Book.in_stock.desc(), Book.id.desc()).limit(limit)
    return [_to_out(b) for b in db.scalars(stmt).all()]


@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, db: Session = Depends(get_db)):
    """도서 1권.

    상세 시트는 목록이 들고 있는 객체를 그대로 쓰므로 이 엔드포인트가 필요 없다.
    필요한 곳은 **딥링크/새로고침 복구**다 — `/request?bookId=123` 으로 바로 들어오면
    화면에 아무 목록도 없어서 id 로 한 건만 가져와야 한다.
    """
    row = db.get(Book, book_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "도서를 찾을 수 없습니다")
    return _to_out(row)
