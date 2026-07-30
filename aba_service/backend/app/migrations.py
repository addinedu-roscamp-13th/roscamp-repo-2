"""가벼운 자동 마이그레이션 — 기존 MariaDB 표에 새 컬럼을 멱등하게 더한다.

`Base.metadata.create_all()` 은 새 DB 에는 완벽하지만 **이미 있는 표에는 컬럼을
안 더한다**. 컬럼 하나 늘 때마다 운영자가 따로 SQL 을 손으로 돌려야 했는데, 깜빡
하면 그 컬럼을 읽는 쿼리부터 배포 직후 깨진다. 여기 목록에 넣어두면 앱이 뜰 때마다
자동으로 맞춰준다. `IF NOT EXISTS` 덕분에 여러 번 돌려도 안전하다.

sqlite(테스트)에서는 스킵한다 — 테스트는 `create_all` 로 이미 최신 스키마로 뜨고,
아래 문법은 MariaDB 전용이라 sqlite에서 에러난다.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS = [
    # db/add_book_unavailable_column.sql 과 동일 — 실행 지점을 여기로 옮긴다.
    "ALTER TABLE cb_books "
    "ADD COLUMN IF NOT EXISTS unavailable TINYINT(1) NOT NULL DEFAULT 0",
    # 로봇팔용 서가 좌표. 컬럼명이 `tier`/`row` 가 아니라 `shelf_*` 인 이유:
    # `row` 는 MariaDB 예약어라 백틱 없이는 문법 오류가 난다(파이썬 속성명은 tier/row).
    "ALTER TABLE cb_books ADD COLUMN IF NOT EXISTS shelf_tier INT NOT NULL DEFAULT 0",
    "ALTER TABLE cb_books ADD COLUMN IF NOT EXISTS shelf_row INT NOT NULL DEFAULT 0",
    "ALTER TABLE cb_loans ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE cb_reservations ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE cb_delivery_requests ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE cb_intrusion_events ADD COLUMN IF NOT EXISTS is_demo TINYINT(1) NOT NULL DEFAULT 0",
]


def run_migrations(engine: Engine) -> None:
    if not engine.dialect.name.startswith("mysql"):
        return  # sqlite 등 — create_all 이 이미 최신 스키마를 만든다.
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))
