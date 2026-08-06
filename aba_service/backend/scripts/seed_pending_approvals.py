"""사서 승인 대기 중인 대여 요청을 `TARGET` 건만큼 만들어 둔다 — 데모용.

    .venv/bin/python scripts/seed_pending_approvals.py

`seed_demo_data.py` 는 요청을 만들 때 전부 승인/반려로 **결론까지 내 버려서**, 관리자
대시보드의 "승인 대기" 가 늘 0이다. 데모에서 사서가 승인 버튼을 누르는 장면을 찍으려면
대기 건이 있어야 한다.

## 규약 (models.DeliveryRequest 머리말)

`borrow`(대여)만 승인이 필요하다 — 책이 관외로 나가기 때문이다. 승인 전에는
`approval='PENDING_APPROVAL'` 이고 **`fms_task_id` 가 빈 문자열**이다(아직 FMS 주문이
안 나갔다는 뜻). `read`(열람)는 관내에 남으므로 승인 없이 바로 주문된다.

⚠️ `fms_task_id` 를 채운 채로 PENDING 을 만들면 **이미 로봇이 움직이는 중인데 승인
   대기**라는 모순 상태가 된다. 화면은 정상으로 보이고 승인을 눌러야 어긋남이 드러난다.

멱등하다 — 이미 `TARGET` 건 이상 대기 중이면 아무것도 안 한다.
"""

import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Book, DeliveryRequest, Member

TARGET = 3
PENDING = "PENDING_APPROVAL"
#: 서가 → 수령 장소. 기존 데이터와 같은 표기를 쓴다(`pickup='문학-1'` 꼴).
ZONE_PICKUP = {
    "문학서가": "문학-1",
    "예술서가": "예술-1",
    "과학-인문학서가": "과학-1",
}
DROPOFF = "안내데스크"

random.seed(20260807)


def main() -> int:
    db = SessionLocal()
    try:
        have = db.query(DeliveryRequest).filter(
            DeliveryRequest.approval == PENDING).count()
        need = TARGET - have
        if need <= 0:
            print(f"이미 승인 대기 {have}건 — 그대로 둔다")
            return 0

        members = db.query(Member).all()
        # 대출 가능한 책만 고른다 — 이미 나가 있는 책의 대여를 승인하라고 하면
        # 사서가 화면에서 앞뒤가 안 맞는 것을 본다.
        books = [b for b in db.query(Book).filter(Book.in_stock.is_(True)).all()
                 if b.zone in ZONE_PICKUP]
        if not members or not books:
            print("회원 또는 대출가능 도서가 없다 — 먼저 seed 를 돌려라")
            return 1

        now = datetime.now()
        picked = random.sample(books, min(need, len(books)))
        for i, b in enumerate(picked):
            db.add(DeliveryRequest(
                member_id=random.choice(members).id,
                book_id=b.id,
                kind="borrow",
                pickup=ZONE_PICKUP[b.zone],
                dropoff=DROPOFF,
                fms_task_id="",          # 승인 전이므로 비어 있어야 한다
                approval=PENDING,
                created_at=now - timedelta(minutes=random.randint(5, 180)),
                is_demo=True,
            ))
            print(f"  + {b.title_kr} ({b.zone}) 대여 승인 대기")
        db.commit()
        print(f"\n승인 대기 {have} → {have + len(picked)}건")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
