"""FSM 전이 감사 로그 기록/조회.

INSTRUCTION.md 안전 규칙: "누가 언제 어떤 전이를 강제했는지 로그로 남긴다."
거부된 시도도 남긴다 — 강제 전이 추적이 목적이라 실패 이력이 오히려 중요하다.

`build_log_entry` 는 순수 함수이고 최상위에서 SQLAlchemy 를 import 하지 않는다.
판정·정규화 로직을 DB 없이 테스트하기 위해서다 (`app/models` 를 최상위에서 끌어오면
sqlalchemy 가 설치된 환경에서만 import 되는 모듈이 된다). DB 래퍼는 함수 안에서
지연 import 한다 — `fsm_link` 가 rclpy 를 다루는 방식과 같은 이유다.
"""
from __future__ import annotations

_REASON_MAX = 255
_ROBOT_ID_MAX = 40
_STATE_MAX = 24
_USERNAME_MAX = 80


def build_log_entry(
    *,
    admin_id: int | None,
    admin_username: str,
    robot_id: str,
    from_state: str,
    to_state: str,
    forced: bool,
    accepted: bool,
    reason: str,
) -> dict:
    """감사 로그 한 줄의 payload.

    문자열은 전부 컬럼 폭에 맞춰 자른다. 자르지 않으면 MariaDB 가 행 전체를 거부해서
    감사 기록이 통째로 사라진다 — 로그가 목적인 기능에서 가장 나쁜 실패 방식이다.
    """
    return {
        "admin_id": admin_id,
        "admin_username": (admin_username or "")[:_USERNAME_MAX],
        "robot_id": (robot_id or "")[:_ROBOT_ID_MAX],
        "from_state": (from_state or "")[:_STATE_MAX],
        "to_state": (to_state or "")[:_STATE_MAX],
        "forced": bool(forced),
        "accepted": bool(accepted),
        "reason": (reason or "")[:_REASON_MAX],
    }


async def record_transition(db, **kwargs):
    """감사 행 1건 기록. kwargs 는 build_log_entry 와 동일."""
    from app.models import FsmTransitionLog

    row = FsmTransitionLog(**build_log_entry(**kwargs))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def recent_transitions(db, robot_id: str | None = None, limit: int = 20) -> list:
    """최근 전이 이력 N건 (INSTRUCTION.md: '최근 전이 이력 N건을 목록으로 표시')."""
    from sqlalchemy import select

    from app.models import FsmTransitionLog

    stmt = select(FsmTransitionLog).order_by(FsmTransitionLog.id.desc()).limit(limit)
    if robot_id:
        stmt = stmt.where(FsmTransitionLog.robot_id == robot_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())
