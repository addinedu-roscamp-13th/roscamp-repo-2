from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True,
)

# 배포 타임존 계약(config._pin_process_timezone 의 DB 짝) — 세션 time_zone 을 app_timezone
# 오프셋으로 고정한다. 이게 없으면 `func.now()` 가 **DB 서버 tz** 를 따라, 앱(KST)과 어긋난
# naive 시각을 저장한다. Asia/Seoul 은 DST 가 없어 오프셋(+09:00)이 상수라 안전하다.
_DB_TIME_ZONE = datetime.now(ZoneInfo(settings.app_timezone)).strftime("%z")  # '+0900'
_DB_TIME_ZONE = f"{_DB_TIME_ZONE[:3]}:{_DB_TIME_ZONE[3:]}"  # '+09:00'


@event.listens_for(engine, "connect")
def _set_session_time_zone(dbapi_conn, _record):
    """새 커넥션마다 세션 tz 고정 — pool 재사용/재연결에도 매번 적용된다."""
    with dbapi_conn.cursor() as cur:
        cur.execute(f"SET time_zone = '{_DB_TIME_ZONE}'")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
