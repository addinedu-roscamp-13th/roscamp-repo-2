from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_admin_db, get_robot_db
from app.deps import get_current_admin
from app.models import Admin, AiModelSettings, Conversation, Message
from app.schemas import DashboardStats, DayCount

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_stats(
    admin_db: AsyncSession = Depends(get_admin_db),
    robot_db: AsyncSession = Depends(get_robot_db),
    _admin: Admin = Depends(get_current_admin),
):
    total_admins = (await admin_db.execute(select(func.count()).select_from(Admin))).scalar_one()
    active_admins = (
        await admin_db.execute(select(func.count()).select_from(Admin).where(Admin.is_active == True))
    ).scalar_one()
    total_conversations = (
        await robot_db.execute(select(func.count()).select_from(Conversation))
    ).scalar_one()
    total_messages = (await robot_db.execute(select(func.count()).select_from(Message))).scalar_one()

    settings = (await robot_db.execute(select(AiModelSettings).where(AiModelSettings.id == 1))).scalar_one_or_none()
    selected_model = settings.selected_model if settings else None

    # conversations per day — last 14 days
    today = date.today()
    day_counts: list[DayCount] = []
    for offset in range(13, -1, -1):
        d = today - timedelta(days=offset)
        start = f"{d} 00:00:00"
        end = f"{d} 23:59:59"
        count = (
            await robot_db.execute(
                select(func.count())
                .select_from(Conversation)
                .where(func.date(Conversation.created_at) == d.isoformat())
            )
        ).scalar_one()
        day_counts.append(DayCount(date=d.isoformat(), count=count))

    recent_admins_rows = (
        await admin_db.execute(select(Admin).order_by(Admin.created_at.desc()).limit(5))
    ).scalars().all()

    from app.schemas import AdminOut
    return DashboardStats(
        total_admins=total_admins,
        active_admins=active_admins,
        total_conversations=total_conversations,
        total_messages=total_messages,
        selected_model=selected_model,
        conversations_per_day=day_counts,
        recent_admins=[AdminOut.model_validate(a) for a in recent_admins_rows],
    )
