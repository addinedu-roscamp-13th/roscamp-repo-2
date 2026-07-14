from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import ADMIN_DATABASE_URL, ROBOT_DATABASE_URL

# MariaDB (labi) 연결. pool_pre_ping 으로 끊긴 커넥션을 자동 감지하고,
# pool_recycle 로 서버의 wait_timeout 만료 전에 커넥션을 재활용한다.
admin_engine = create_async_engine(
    ADMIN_DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=3600
)
AdminSessionLocal = async_sessionmaker(admin_engine, expire_on_commit=False)

robot_engine = create_async_engine(
    ROBOT_DATABASE_URL, echo=False, pool_pre_ping=True, pool_recycle=3600
)
RobotSessionLocal = async_sessionmaker(robot_engine, expire_on_commit=False)


class AdminBase(DeclarativeBase):
    pass


class RobotBase(DeclarativeBase):
    pass


async def get_admin_db() -> AsyncSession:
    async with AdminSessionLocal() as session:
        yield session


async def get_robot_db() -> AsyncSession:
    async with RobotSessionLocal() as session:
        yield session


# Alias for compatibility
get_db = get_admin_db


async def init_db():
    """모델에 정의된 rc_ 테이블을 없으면 생성한다 (create_all 은 멱등).

    기존 labi DB 에 이미 rc_ 테이블이 존재하면 그대로 재사용된다.
    """
    from app import models  # noqa: F401 — import triggers table registration

    async with admin_engine.begin() as conn:
        await conn.run_sync(AdminBase.metadata.create_all)

    async with robot_engine.begin() as conn:
        await conn.run_sync(RobotBase.metadata.create_all)
