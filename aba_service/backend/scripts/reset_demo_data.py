"""데모/시연용으로 쌓인 대출·요청·예약·침입이력과 데모 도서를 지운다.

⚠️ `cb_loans`/`cb_delivery_requests`/`cb_reservations`/`cb_intrusion_events`는
`is_demo=True`인 행만 지운다 — `seed_demo_data.py`가 만든 행은 전부 이 플래그가
켜져 있고, 실제 사용자 활동으로 생긴 행은 절대 지워지지 않는다.
`cb_task_logs`는 `task_id`가 `demo-`로 시작하는 행만 지운다(같은 이유의 마커).

`cb_demo_robot_states`는 성격이 다르다 — 이 표엔 처음부터 데모 데이터만 들어간다(실제
로봇 상태는 FMS 텔레메트리에서만 오고 여기 저장되지 않는다), 그래서 구분 없이 통째로
비워도 안전하다.

실행 (aba_service/backend 에서):
    .venv/bin/python scripts/reset_demo_data.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Book,
    DemoRobotState,
    DeliveryRequest,
    IntrusionEvent,
    Loan,
    Reservation,
    TaskLog,
)


def reset_demo_data(db) -> dict[str, int]:
    """핵심 로직 — 세션을 받아 데모(`is_demo=True`) 행만 지운다. 실제 이력은 절대 안 지운다."""
    n_res = db.query(Reservation).filter(Reservation.is_demo.is_(True)).delete()
    n_req = db.query(DeliveryRequest).filter(DeliveryRequest.is_demo.is_(True)).delete()
    n_loans = db.query(Loan).filter(Loan.is_demo.is_(True)).delete()
    # TaskLog 는 is_demo 컬럼이 없다 — task_id 네이밍이 유일한 마커다.
    #   t9xxx  : 지금 시더가 쓰는 예약 번호대(`seed_demo_data.DEMO_TASK_ID_BASE`).
    #            화면에 진짜처럼(`t9001`) 보이게 하면서 데모를 골라내려는 것이다.
    #   demo-* : 그 이전 시더가 쓰던 접두사. 남아 있는 DB 를 위해 같이 지운다.
    # ⚠️ 실제 FMS 는 부팅마다 t1 부터 세므로 9000번대와 겹치지 않는다
    #    (`fleet_orchestrator.py` 의 `itertools.count(1)`).
    n_logs = db.query(TaskLog).filter(
        TaskLog.task_id.like("t9%") | TaskLog.task_id.like("demo-%")
    ).delete(synchronize_session=False)
    n_intrusions = db.query(IntrusionEvent).filter(IntrusionEvent.is_demo.is_(True)).delete()
    # DemoRobotState 는 태생부터 전부 데모 데이터라(파일 상단 docstring 참고) 통째로 비워도 안전.
    n_robots = db.query(DemoRobotState).delete()
    db.commit()

    n_books = (
        db.query(Book).filter(Book.title_kr.like("%데모도서%")).delete(
            synchronize_session=False
        )
    )
    db.commit()

    # in_stock 은 "지금 대출 중인가"의 source-of-truth인 Loan.status="borrowed" 로
    # 다시 계산한다 — 위 delete는 is_demo 행만 지웠으므로 남은 대출은 전부 실제 대출이다.
    # unavailable(사서가 파손/분실로 표시한 값)은 여기서 건드리지 않는다 — 데모도서는
    # 위에서 행 자체가 삭제됐으니 따로 초기화할 것도 없다.
    active_book_ids = {
        row[0]
        for row in db.query(Loan.book_id).filter(Loan.status == "borrowed").all()
    }
    for book in db.query(Book).all():
        book.in_stock = book.id not in active_book_ids
    db.commit()

    return {
        "예약": n_res, "요청": n_req, "대출": n_loans, "작업로그": n_logs,
        "침입이력": n_intrusions, "로봇상태": n_robots, "데모도서": n_books,
    }


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        counts = reset_demo_data(db)
        print(
            f"[reset_demo_data] 삭제: 예약={counts['예약']} 요청={counts['요청']} "
            f"대출={counts['대출']} 작업로그={counts['작업로그']} "
            f"침입이력={counts['침입이력']} 로봇상태={counts['로봇상태']} "
            f"데모도서={counts['데모도서']} — 남은 도서 재고 전부 대출가능으로 복구"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
