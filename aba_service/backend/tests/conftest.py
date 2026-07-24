"""도서관 웹 백엔드(aba_service) 테스트 공통 준비.

## 왜 sqlite 인가
운영은 MariaDB 지만 테스트가 DB 를 띄우게 하면 아무도 안 돌린다. 스키마는 같은
`Base.metadata` 에서 나오므로 sqlite 인메모리로 충분하다 — 우리가 검증하는 건 SQL 방언이
아니라 **승인 절차의 순서**(누가 언제 FMS 를 부르는가)다.

`BigInteger` 를 sqlite 에서 `INTEGER` 로 바꿔 컴파일하는 훅이 필요하다. sqlite 는 선언 타입이
정확히 `INTEGER` 인 기본키만 rowid 별칭으로 삼아 자동 증가시키는데, 모델이 `BigInteger`
(=`BIGINT`)를 쓰기 때문이다. 모델을 건드리지 않고 테스트 쪽에서만 우회한다.

## FMS 는 절대 부르지 않는다
`fms_client` 는 매 테스트에서 가짜로 갈아끼운다(`fms` 픽스처). 진짜 FMS 가 떠 있든 말든
결과가 같아야 하고, "승인 전에는 FMS 를 부르지 않는다"는 것 자체가 검증 대상이다.
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 백엔드 루트를 import 경로에 넣는다 — `app.*` 를 그대로 쓰기 위해.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# .env 의 운영 DB 로 붙지 않도록 import 전에 sqlite 로 못박는다.
os.environ.setdefault("DATABASE_URL", "sqlite://")


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):  # noqa: ANN001, ANN202
    """sqlite 에서 BIGINT 기본키가 자동 증가하도록 INTEGER 로 컴파일한다."""
    return "INTEGER"


from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.member_security import create_member_token  # noqa: E402
from app.models import AdminUser, Book, Member  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402


@pytest.fixture()
def db_session():
    """테스트마다 새 인메모리 DB. StaticPool 이라 커넥션이 하나여서 표가 유지된다."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """`get_db` 를 테스트 세션으로 갈아끼운 TestClient.

    `with TestClient(...)` 를 쓰지 않는다 — startup 이벤트가 운영 MariaDB 로 `create_all`
    을 시도하기 때문이다. 라우팅만 필요하므로 lifespan 은 돌리지 않는다.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class FakeFms:
    """`fms_client` 대역. 어떤 호출이 몇 번 갔는지 세어 둔다."""

    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.next_task_id = "t-1"
        self.ok = True
        self.reason = "FMS 연결 실패: 대역"
        #: FMS 가 내려줄 사건들 (도착 알림). 시험이 직접 채운다.
        self.events: list[dict] = []

    def submit_order(self, **kwargs) -> tuple[bool, str]:
        if not self.ok:
            return False, self.reason
        self.orders.append(kwargs)
        return True, self.next_task_id

    def list_orders(self) -> tuple[bool, list[dict]]:
        return True, []

    def list_events(self, since: int = 0, limit: int = 50) -> tuple[bool, list[dict], int]:
        if not self.ok:
            return False, [], since
        fresh = [e for e in self.events if e["seq"] > since]
        return True, fresh, max([e["seq"] for e in self.events], default=since)

    @property
    def submit_count(self) -> int:
        return len(self.orders)


@pytest.fixture()
def fms(monkeypatch):
    """`routers.delivery` 와 `routers.approvals` 가 보는 FMS 를 대역으로 바꾼다."""
    from app.routers import delivery

    fake = FakeFms()
    monkeypatch.setattr(delivery.fms_client, "submit_order", fake.submit_order)
    monkeypatch.setattr(delivery.fms_client, "list_orders", fake.list_orders)
    monkeypatch.setattr(delivery.fms_client, "list_events", fake.list_events)
    return fake


@pytest.fixture()
def member(db_session) -> Member:
    row = Member(
        username="hong",
        full_name="홍길동",
        hashed_password=hash_password("pw"),
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture()
def member_auth(member) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_member_token(member.id)}"}


@pytest.fixture()
def admin(db_session) -> AdminUser:
    row = AdminUser(
        username="사서1",
        full_name="사서 하나",
        hashed_password=hash_password("pw"),
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture()
def admin_auth(admin) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(admin.id))}"}


def make_book(
    db_session, *, title="어린 왕자", zone="문학-1", in_stock=True, unavailable=False
) -> Book:
    row = Book(
        title_kr=title,
        title_en=title,
        title_zh=title,
        title_vi=title,
        author="생텍쥐페리",
        category="literature",
        cover="📘",
        color="from-slate-200 to-slate-300",
        zone=zone,
        shelf="1단",
        in_stock=in_stock,
        unavailable=unavailable,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture()
def book(db_session) -> Book:
    return make_book(db_session)
