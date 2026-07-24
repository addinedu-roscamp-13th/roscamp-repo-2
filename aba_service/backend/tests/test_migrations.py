"""run_migrations 의 dialect 가드 — sqlite(테스트)에서는 손대지 않고 스킵하는지.

MariaDB 전용 문법(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)이라 실제 컬럼 추가
동작 자체는 sqlite로 검증할 수 없다 — 여기서 검증하는 건 "다른 dialect 에서는
예외 없이 조용히 넘어간다"는 안전장치뿐이다. MariaDB 대상 동작은 운영 배포 시
수동 확인 대상으로 남긴다.
"""

from sqlalchemy import create_engine

from app.migrations import run_migrations


def test_sqlite_에서는_아무것도_안_하고_예외도_없다():
    engine = create_engine("sqlite://")
    run_migrations(engine)  # 예외 없이 조용히 스킵되면 성공
