"""데모용 대량 허구 데이터 — 거의 한 달간 운영된 것처럼 보이게 대출·요청·예약 이력을 채운다.

⚠️ `seed_members.py`/`seed_books.py` 가 만든 진짜 회원/도서 위에 **얹는다** — 새 회원/도서는
만들지 않고, 그 회원·도서로 가짜 이력만 잔뜩 쌓는다. 멱등하지 않다(데모용이라 여러 번 돌리면
그때마다 더 쌓인다) — 지우고 싶으면 `cb_loans`/`cb_delivery_requests`/`cb_reservations` 를
직접 비우면 된다.

실행 (aba_service/backend 에서):
    .venv/bin/python scripts/seed_demo_data.py
"""

import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Book,
    DemoRobotState,
    DeliveryRequest,
    IntrusionEvent,
    Loan,
    Member,
    Reservation,
    TaskLog,
)

LOAN_DAYS = 14
DAYS_BACK = 30
N_LOANS = 230
N_REQUESTS = 90
N_RESERVATIONS = 20
N_DUE_TOMORROW_LOANS = 6  # 대시보드 "반납 임박(1일 이내)" 이 확실히 채워지게 따로 몇 건
N_TASK_LOGS = 120
N_INTRUSIONS = 8

# ops.py 의 TASK_KINDS 표(운영 지시 폼)와 같은 종류 키만 씀 — 화면 라벨과 어긋나지 않게.
TASK_KIND_KEYS = ["transfer", "sort", "tidy", "porter", "dispatch", "patrol"]
# 실제 로봇 이름(pinky1~3) — README/도메인 브릿지 설정과 동일, 여기선 로그용 문자열일 뿐.
ROBOTS = ["pinky1", "pinky2", "pinky3"]
INTRUSION_NOTES = [
    "열람실 창문 근처 움직임 감지",
    "야간 순찰 중 인기척 감지",
    "출입구 부근 이상 움직임",
    None,
]

# 데모용 도서 보강 — 진짜 카탈로그(seed_books.py, 카테고리당 3권)는 그대로 두고, 데모에서만
# 카테고리당 이 수만큼 채워서 인기도서 TOP·분야별 대출이 더 다양하게 보이게 한다.
TARGET_BOOKS_PER_CATEGORY = 20
CATEGORY_META = {
    "literature": {"label": "문학", "zone": "문학-1", "cover": "📖", "color": "from-rose-200 to-rose-300"},
    "art": {"label": "예술", "zone": "예술-1", "cover": "🎨", "color": "from-orange-200 to-amber-300"},
    "science": {"label": "과학", "zone": "과학-1", "cover": "🔬", "color": "from-sky-200 to-blue-300"},
    "humanities": {"label": "인문학", "zone": "인문학-1", "cover": "📚", "color": "from-indigo-200 to-blue-300"},
    "kids": {"label": "유아", "zone": "유아", "cover": "🧸", "color": "from-lime-200 to-green-300"},
}


def _top_up_books(db) -> None:
    """카테고리별 도서를 `TARGET_BOOKS_PER_CATEGORY` 권까지 가짜로 채운다(이미 있으면 skip)."""
    for category, meta in CATEGORY_META.items():
        existing = db.query(Book).filter(Book.category == category).count()
        missing = TARGET_BOOKS_PER_CATEGORY - existing
        for i in range(missing):
            n = existing + i + 1
            title = f"{meta['label']} 데모도서 {n}"
            db.add(
                Book(
                    title_kr=title,
                    title_en=title,
                    title_zh=title,
                    title_vi=title,
                    author="데모 작가",
                    category=category,
                    cover=meta["cover"],
                    color=meta["color"],
                    zone=meta["zone"],
                    shelf="셋째 줄",
                    in_stock=True,
                    unavailable=False,
                )
            )
    db.commit()


def _seed_task_logs(db, now) -> int:
    """최근 7일에 걸쳐 작업 로그를 뿌린다 — ops.py `/stats` 의 7일 버킷과 어긋나지
    않게 day_offset 을 0~6 정수로 딱 맞춘다(시:분만 랜덤)."""
    for i in range(N_TASK_LOGS):
        day_offset = random.randint(0, 6)
        recorded_at = now - timedelta(
            days=day_offset, hours=random.uniform(0, 23), minutes=random.uniform(0, 59)
        )
        status = random.choices(["COMPLETED", "FAILED"], weights=[87, 13])[0]
        db.add(
            TaskLog(
                task_id=f"demo-task-{i}",
                kind=random.choice(TASK_KIND_KEYS),
                robot=random.choice(ROBOTS),
                status=status,
                leg_idx=random.randint(1, 3),
                leg_count=3,
                retries=random.randint(0, 2) if status == "FAILED" else 0,
                reason="배터리 부족으로 임무 중단" if status == "FAILED" else None,
                recorded_at=recorded_at,
            )
        )
    db.commit()
    return N_TASK_LOGS


def _seed_intrusions(db, now) -> int:
    """야간 침입 이력 — 앞 3건만 미확인으로 남겨 대시보드 "침입감지 이력"에 실제로 뜨게 한다."""
    zones = [meta["zone"] for meta in CATEGORY_META.values()] + ["로비", "안내데스크"]
    for i in range(N_INTRUSIONS):
        detected_at = now - timedelta(days=random.uniform(0, 5), hours=random.uniform(0, 23))
        db.add(
            IntrusionEvent(
                source=random.choice(ROBOTS),
                zone=random.choice(zones),
                note=random.choice(INTRUSION_NOTES),
                acknowledged=i >= 3,
                detected_at=detected_at,
            )
        )
    db.commit()
    return N_INTRUSIONS


def _seed_robot_states(db) -> int:
    """로봇 상태 도넛(가용중/작업중/충전중/에러) 데모 대체값 — FMS 가 하나도 안 붙어있는
    개발 환경에서 도넛이 항상 빈값이 되는 걸 막는다. `ops.py`/`/dashboard` 는 실제 FMS
    스냅샷이 있으면 이 테이블을 절대 안 쓴다. 재실행해도 안 쌓이게 먼저 비운다."""
    db.query(DemoRobotState).delete()
    # 주행 3대(pinky1-3) + 팔 2대(arm1-2) — 4개 상태 전부 채워지게 일부러 스프레드.
    demo_fleet = [
        ("pinky1", "PATROL"),
        ("pinky2", "WORKING"),
        ("pinky3", "CHARGING"),
        ("arm1", "IDLE"),
        ("arm2", "ERROR"),
    ]
    for robot, state in demo_fleet:
        db.add(DemoRobotState(robot=robot, state=state))
    db.commit()
    return len(demo_fleet)


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        members = db.query(Member).all()
        if not members or db.query(Book).count() == 0:
            print(
                "[seed_demo_data] 회원/도서가 없습니다 — "
                "먼저 seed_members.py / seed_books.py 를 돌리세요"
            )
            return

        _top_up_books(db)
        books = db.query(Book).all()

        now = datetime.now()

        # 1) 대출 이력 — 대부분 반납 완료, 일부는 지금 대출중(자연히 연체/반납임박도 섞인다)
        for _ in range(N_LOANS):
            member = random.choice(members)
            book = random.choice(books)
            borrowed_at = now - timedelta(days=random.uniform(1, DAYS_BACK))
            due_at = borrowed_at + timedelta(days=LOAN_DAYS)
            returned_at = borrowed_at + timedelta(days=random.uniform(1, LOAN_DAYS + 5))
            if returned_at > now:
                returned_at = None
                status = "borrowed"
            else:
                status = "returned"
            db.add(
                Loan(
                    member_id=member.id,
                    book_id=book.id,
                    status=status,
                    borrowed_at=borrowed_at,
                    due_at=due_at,
                    returned_at=returned_at,
                )
            )
        db.commit()

        # 1-1) 반납 임박(1일 이내) 전용 배치 — 위 랜덤 분포만으로는 24시간 창에 잘 안 걸려서
        # 대시보드 "반납 임박(1일 이내)" 리스트가 매번 비어 보이는 걸 막으려고 따로 몇 건 박아둔다.
        for _ in range(N_DUE_TOMORROW_LOANS):
            member = random.choice(members)
            book = random.choice(books)
            borrowed_at = now - timedelta(days=LOAN_DAYS) + timedelta(hours=random.uniform(1, 23))
            due_at = now + timedelta(hours=random.uniform(1, 23))
            db.add(
                Loan(
                    member_id=member.id,
                    book_id=book.id,
                    status="borrowed",
                    borrowed_at=borrowed_at,
                    due_at=due_at,
                    returned_at=None,
                )
            )
        db.commit()

        # 지금 대출중으로 남은 책은 in_stock=False 로 맞춰 화면 배지와 어긋나지 않게 한다.
        active_book_ids = {
            row[0]
            for row in db.query(Loan.book_id).filter(Loan.status == "borrowed").all()
        }
        for book in books:
            if not book.unavailable:
                book.in_stock = book.id not in active_book_ids
        db.commit()

        # 2) 회원 배달요청 이력 — 대여/열람 섞어서, 대부분 승인, 일부 반려
        for i in range(N_REQUESTS):
            member = random.choice(members)
            book = random.choice(books)
            kind = random.choice(["borrow", "read"])
            created_at = now - timedelta(days=random.uniform(0, DAYS_BACK))
            approval = random.choices(["APPROVED", "REJECTED"], weights=[85, 15])[0]
            db.add(
                DeliveryRequest(
                    member_id=member.id,
                    book_id=book.id,
                    kind=kind,
                    pickup=book.zone,
                    dropoff="안내데스크" if kind == "borrow" else "테이블-1번-좌",
                    approval=approval,
                    fms_task_id=f"demo-{i}" if approval == "APPROVED" else "",
                    reject_reason="재고 확인이 필요합니다" if approval == "REJECTED" else None,
                    created_at=created_at,
                    decided_at=created_at,
                    decided_by="admin",
                )
            )
        db.commit()

        # 3) 예약 대기열 — 화면에 몇 건 보이게
        for _ in range(N_RESERVATIONS):
            member = random.choice(members)
            book = random.choice(books)
            status = random.choices(
                ["waiting", "ready", "fulfilled", "cancelled"],
                weights=[40, 15, 25, 20],
            )[0]
            db.add(Reservation(member_id=member.id, book_id=book.id, status=status))
        db.commit()

        # 4) 작업 로그(최근 7일) + 5) 야간 침입 이력 + 6) 로봇 상태(FMS 미연결 시 대체)
        n_logs = _seed_task_logs(db, now)
        n_intrusions = _seed_intrusions(db, now)
        n_robots = _seed_robot_states(db)

        print(
            f"[seed_demo_data] books_total={len(books)} "
            f"loans+={N_LOANS + N_DUE_TOMORROW_LOANS} requests+={N_REQUESTS} "
            f"reservations+={N_RESERVATIONS} task_logs+={n_logs} "
            f"intrusions+={n_intrusions}(미확인 3건) robot_states={n_robots}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
