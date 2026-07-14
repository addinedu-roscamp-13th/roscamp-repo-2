from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser
from ..schemas import AdminOut, DashboardStats, DayCount
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/dashboard", tags=["dashboard"])


def _safe_count(db: Session, table: str) -> int:
    """COUNT(*) for a table that may not exist yet — degrade to 0."""
    try:
        return int(db.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
    except Exception:
        return 0


@router.get("/stats", response_model=DashboardStats)
def stats(
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    total_admins = db.scalar(select(func.count()).select_from(AdminUser)) or 0
    active_admins = (
        db.scalar(
            select(func.count())
            .select_from(AdminUser)
            .where(AdminUser.is_active.is_(True))
        )
        or 0
    )
    total_conversations = _safe_count(db, "cb_conversations")
    total_messages = _safe_count(db, "cb_messages")

    selected_model = None
    try:
        selected_model = db.scalar(
            text("SELECT selected_model FROM cb_ai_model_settings LIMIT 1")
        )
    except Exception:
        selected_model = None

    # conversations per day for the last 14 days (zero-filled)
    counts: dict[str, int] = {}
    try:
        since = date.today() - timedelta(days=13)
        rows = db.execute(
            text(
                "SELECT DATE(created_at) d, COUNT(*) c FROM cb_conversations "
                "WHERE created_at >= :since GROUP BY DATE(created_at)"
            ),
            {"since": since},
        ).all()
        counts = {str(r[0]): int(r[1]) for r in rows}
    except Exception:
        counts = {}

    per_day = [
        DayCount(date=(d := str(date.today() - timedelta(days=i))), count=counts.get(d, 0))
        for i in range(13, -1, -1)
    ]

    recent = db.scalars(
        select(AdminUser).order_by(AdminUser.created_at.desc()).limit(5)
    ).all()

    return DashboardStats(
        total_admins=total_admins,
        active_admins=active_admins,
        total_conversations=total_conversations,
        total_messages=total_messages,
        selected_model=selected_model,
        conversations_per_day=per_day,
        recent_admins=[AdminOut.model_validate(a) for a in recent],
    )
