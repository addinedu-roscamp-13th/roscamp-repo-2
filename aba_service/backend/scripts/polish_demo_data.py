"""데모 데이터 다듬기 — 숫자가 **그럴듯해** 보이게 맞춘다.

    .venv/bin/python scripts/polish_demo_data.py

`seed_demo_data.py` 를 돌린 뒤에 한 번 실행한다. 여러 번 돌려도 같은 상태로 수렴한다.

## 무엇을 고치나 (그리고 왜)

1. **재고 정합성** — `cb_books.in_stock` 을 "지금 대출중인 대출이 있나"로 다시 계산한다.
   예전 이력은 재고를 안 내려서 `대출중 102건 / 대출불가 78권` 처럼 앞뒤가 안 맞았다.
   대시보드가 두 숫자를 나란히 보여 주므로 어긋나면 바로 보인다.

2. **연체율** — 연체가 전체 대출의 39%였다. 실제 도서관은 5~10% 선이다. 초과분을
   반납 처리한다(반납일은 만기 직후로 둔다 — 지어낸 티가 덜 난다).

3. **인기 분산** — 대출 이력이 기존 12권에만 쌓여 있어 인기도서 상위가 늘 그 6권이었다.
   `/api/books/popular` 이 추천 화면(`recommend.tsx`)의 원천이라, 신간 188권이 한 번도
   안 뜬다. 신규 도서에 이력을 얹어 상위권을 섞는다.

4. **위시리스트** — 0건이었다. 회원 화면의 찜 목록이 빈 채로 나온다.

⚠️ 여기서 만드는 대출·위시리스트는 전부 `is_demo=True` 다(`Wishlist` 는 그 컬럼이
   없어 회원 접두사 `demo_` 로만 구분된다). `reset_demo_data.py` 가 걷어 간다.
"""

import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Book, Loan, Member, Wishlist

#: 목표 연체 비율(대출중 대비). 실제 공공도서관 통계 언저리다.
TARGET_OVERDUE_RATIO = 0.08
#: 인기 상위권에 올릴 신규 도서 수와, 각 책에 얹을 대출 이력 범위.
POPULAR_PICKS = 24
POPULAR_LOANS = (9, 26)
#: 위시리스트 총 건수.
N_WISHLIST = 90

random.seed(20260807)          # 여러 번 돌려도 같은 그림


def fix_stock(db) -> int:
    """`in_stock` 을 활성 대출에서 다시 계산한다. 이게 정본이다."""
    active = {bid for (bid,) in db.execute(
        select(Loan.book_id).where(Loan.status == "borrowed")).all()}
    changed = 0
    for b in db.query(Book).all():
        want = b.id not in active
        if b.in_stock != want:
            b.in_stock = want
            changed += 1
    db.commit()
    return changed


def fix_overdue(db) -> int:
    """연체가 목표 비율을 넘으면 초과분을 반납 처리한다."""
    now = datetime.now()
    borrowed = db.query(Loan).filter(Loan.status == "borrowed").count()
    overdue = db.query(Loan).filter(Loan.status == "borrowed", Loan.due_at < now).all()
    keep = max(1, int(borrowed * TARGET_OVERDUE_RATIO))
    excess = overdue[keep:]
    for ln in excess:
        ln.status = "returned"
        # 만기 직후에 반납한 것으로 둔다 — 지금 시각으로 몰면 반납이 한 시점에 뭉친다.
        ln.returned_at = ln.due_at + timedelta(hours=random.randint(2, 60))
    db.commit()
    return len(excess)


def spread_popularity(db) -> int:
    """신규 도서에 대출 이력을 얹어 인기 상위권을 섞는다.

    ⚠️ 전부 **반납 완료**로 만든다. 활성 대출로 만들면 재고가 또 어긋나고, 위에서 맞춘
       `in_stock` 을 다시 깨뜨린다.
    """
    members = db.query(Member).all()
    if not members:
        return 0
    # 이력이 가장 적은 책들 = 이번에 새로 들어온 188권
    counts = dict(db.execute(
        select(Loan.book_id, func.count(Loan.id)).group_by(Loan.book_id)).all())
    books = sorted(db.query(Book).all(), key=lambda b: counts.get(b.id, 0))
    picks = books[:POPULAR_PICKS]
    now = datetime.now()
    added = 0
    for b in picks:
        for _ in range(random.randint(*POPULAR_LOANS)):
            start = now - timedelta(days=random.randint(20, 300))
            due = start + timedelta(days=14)
            db.add(Loan(
                member_id=random.choice(members).id, book_id=b.id,
                status="returned", borrowed_at=start, due_at=due,
                returned_at=due - timedelta(days=random.randint(0, 6)),
                is_demo=True,
            ))
            added += 1
    db.commit()
    return added


def seed_wishlist(db) -> int:
    """찜 목록. 회원 화면이 비어 보이지 않게."""
    have = db.query(Wishlist).count()
    if have >= N_WISHLIST:
        return 0
    members = db.query(Member).all()
    books = db.query(Book).all()
    pairs = set(db.execute(select(Wishlist.member_id, Wishlist.book_id)).all())
    added = 0
    while added < N_WISHLIST - have:
        m, b = random.choice(members), random.choice(books)
        if (m.id, b.id) in pairs:
            continue
        pairs.add((m.id, b.id))
        db.add(Wishlist(member_id=m.id, book_id=b.id,
                        created_at=datetime.now() - timedelta(days=random.randint(0, 60))))
        added += 1
    db.commit()
    return added


def main() -> int:
    db = SessionLocal()
    try:
        print(f"인기 분산      +{spread_popularity(db)}건 대출 이력")
        print(f"연체 정리      {fix_overdue(db)}건 반납 처리")
        print(f"재고 정합성    {fix_stock(db)}권 갱신")
        print(f"위시리스트     +{seed_wishlist(db)}건")

        now = datetime.now()
        borrowed = db.scalar(select(func.count(Loan.id)).where(Loan.status == "borrowed"))
        overdue = db.scalar(select(func.count(Loan.id)).where(
            Loan.status == "borrowed", Loan.due_at < now))
        out = db.scalar(select(func.count(Book.id)).where(Book.in_stock.is_(False)))
        print(f"\n대출중 {borrowed} · 연체 {overdue}"
              f" ({overdue / max(1, borrowed) * 100:.0f}%) · 대출불가 {out}권")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
