"""Customer-facing book catalog & recommendations.

Public (no auth) — these power the chatbot's "recommend a book" feature and
the customer search/recommend pages. Data comes from the `cb_books` table so the
bot recommends real, in-stock titles instead of hard-coded mock data.
"""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Book
from ..schemas import BookOut

router = APIRouter(prefix="/api/books", tags=["books"])

# Categories used by the frontend filters / chat intent mapping.
CATEGORIES = {"literature", "art", "science"}


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
    limit: int = Query(default=50, ge=1, le=200),
):
    """Search / list catalog books (title, author, summary, tags)."""
    stmt = select(Book)
    if category and category in CATEGORIES:
        stmt = stmt.where(Book.category == category)
    if q and q.strip():
        stmt = _keyword_filter(stmt, q)
    stmt = stmt.order_by(Book.in_stock.desc(), Book.id.desc()).limit(limit)
    return [_to_out(b) for b in db.scalars(stmt).all()]
