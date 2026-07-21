"""회원 계정 시드 — member1 ~ member10 (비밀번호 공통 `member1234`).

멱등하다: 이미 있는 아이디는 건너뛰고 없는 것만 만든다. 표를 지우지 않는다.

실행 (aba_service/backend 에서):
    .venv/bin/python scripts/seed_members.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402,F401  (import 만으로 테이블이 등록된다)
    Book,
    DeliveryRequest,
    Loan,
    Member,
    Reservation,
    Wishlist,
)
from app.security import hash_password  # noqa: E402

COUNT = 10
PASSWORD = "member1234"


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        created, skipped = 0, 0
        for i in range(1, COUNT + 1):
            username = f"member{i}"
            exists = db.query(Member).filter(Member.username == username).first()
            if exists is not None:
                skipped += 1
                continue
            db.add(
                Member(
                    username=username,
                    full_name=f"회원{i}",
                    hashed_password=hash_password(PASSWORD),
                    is_active=True,
                )
            )
            created += 1
        db.commit()
        total = db.query(Member).count()
        print(f"[seed_members] created={created} skipped={skipped} total={total}")
        print(f"[seed_members] 비밀번호는 모두 '{PASSWORD}'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
