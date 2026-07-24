"""데모/시연용으로 쌓인 대출·요청·예약·작업로그·침입이력·로봇상태와 데모 도서를 지운다.

⚠️ `cb_loans`/`cb_delivery_requests`/`cb_reservations`/`cb_task_logs`/`cb_intrusion_events`
를 **통째로** 비운다 — `seed_demo_data.py`가 만든 가짜 이력과 실제 이력을 구분할 표시(마커)가
없어서, 이 스크립트를 돌리면 진짜 대출/승인/작업/침입 이력도 같이 지워진다. 회원 계정
(cb_members)·관리자(cb_admin_users)·진짜 카탈로그는 안 건드린다.

`cb_demo_robot_states` 는 성격이 다르다 — 이 테이블엔 처음부터 데모 데이터만 들어간다(실제
로봇 상태는 FMS 텔레메트리에서만 오고 여기 저장되지 않는다), 그래서 구분 없이 통째로 비워도
안전하다.

지우는 것:
  1. cb_reservations / cb_delivery_requests / cb_loans / cb_task_logs / cb_intrusion_events /
     cb_demo_robot_states 전체 행
  2. 제목에 "데모도서"가 들어간 Book (seed_demo_data.py 가 만든 것) — 위 1번 삭제 뒤라
     FK 참조가 없어 안전하게 지워진다.
  3. 남은 모든 Book 의 in_stock=True, unavailable=False 로 복구(데모가 흐트러뜨린 재고 상태 리셋).

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


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        n_res = db.query(Reservation).delete()
        n_req = db.query(DeliveryRequest).delete()
        n_loans = db.query(Loan).delete()
        n_logs = db.query(TaskLog).delete()
        n_intrusions = db.query(IntrusionEvent).delete()
        n_robots = db.query(DemoRobotState).delete()
        db.commit()

        n_books = (
            db.query(Book).filter(Book.title_kr.like("%데모도서%")).delete(
                synchronize_session=False
            )
        )
        db.commit()

        db.query(Book).update({Book.in_stock: True, Book.unavailable: False})
        db.commit()

        print(
            f"[reset_demo_data] 삭제: 예약={n_res} 요청={n_req} 대출={n_loans} "
            f"작업로그={n_logs} 침입이력={n_intrusions} 로봇상태={n_robots} "
            f"데모도서={n_books} — 남은 도서 재고 전부 대출가능으로 복구"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
