"""아르코 마커 ID별 액션 설정 CRUD (관리자용).

마커를 인식했을 때 실행할 액션을 마커 ID(0~10 등)마다 지정한다.
실제 실행(로봇 호출)은 관제 페이지(/admin/aruco)가 담당하고, 여기서는 설정만 저장한다.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_robot_db
from app.deps import get_current_admin
from app.models import MarkerAction

router = APIRouter(prefix="/api/marker-actions", tags=["marker-actions"])

VALID_ACTIONS = {"none", "dock", "rotate", "move", "lcd_emotion", "lcd_text", "lcd_image"}


class MarkerActionIn(BaseModel):
    label: str | None = None
    action_type: str = "none"
    params: dict | None = None
    enabled: bool = True


class MarkerActionOut(BaseModel):
    marker_id: int
    label: str | None
    action_type: str
    params: dict | None
    enabled: bool


def _to_out(m: MarkerAction) -> MarkerActionOut:
    return MarkerActionOut(
        marker_id=m.marker_id,
        label=m.label,
        action_type=m.action_type,
        params=json.loads(m.params) if m.params else None,
        enabled=m.enabled,
    )


@router.get("")
async def list_actions(
    db: AsyncSession = Depends(get_robot_db), _=Depends(get_current_admin)
):
    rows = (
        await db.execute(select(MarkerAction).order_by(MarkerAction.marker_id))
    ).scalars().all()
    return [_to_out(m) for m in rows]


@router.put("/{marker_id}")
async def upsert_action(
    marker_id: int,
    body: MarkerActionIn,
    db: AsyncSession = Depends(get_robot_db),
    _=Depends(get_current_admin),
):
    if body.action_type not in VALID_ACTIONS:
        raise HTTPException(400, f"유효하지 않은 action_type: {body.action_type}")
    m = (
        await db.execute(select(MarkerAction).where(MarkerAction.marker_id == marker_id))
    ).scalar_one_or_none()
    if m is None:
        m = MarkerAction(marker_id=marker_id)
        db.add(m)
    m.label = body.label
    m.action_type = body.action_type
    m.params = json.dumps(body.params, ensure_ascii=False) if body.params else None
    m.enabled = body.enabled
    await db.commit()
    await db.refresh(m)
    return _to_out(m)


@router.delete("/{marker_id}")
async def delete_action(
    marker_id: int,
    db: AsyncSession = Depends(get_robot_db),
    _=Depends(get_current_admin),
):
    m = (
        await db.execute(select(MarkerAction).where(MarkerAction.marker_id == marker_id))
    ).scalar_one_or_none()
    if m:
        await db.delete(m)
        await db.commit()
    return {"success": True}
