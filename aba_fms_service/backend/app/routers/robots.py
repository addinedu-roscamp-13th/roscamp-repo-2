"""로봇 레지스트리 CRUD 라우터 — 로봇팔/핑키봇 등 각 로봇의 접속 IP를 관리."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.models import Admin, Robot
from app.schemas import RobotCreate, RobotListResponse, RobotOut, RobotUpdate

router = APIRouter(prefix="/api/robots", tags=["robots"])


@router.get("", response_model=RobotListResponse)
async def list_robots(
    q: str | None = Query(None),
    robot_type: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    stmt = select(Robot)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Robot.name.ilike(like),
                Robot.ip_address.ilike(like),
                Robot.description.ilike(like),
            )
        )
    if robot_type:
        stmt = stmt.where(Robot.robot_type == robot_type)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Robot.created_at.desc()).offset(skip).limit(limit))
    ).scalars().all()
    return RobotListResponse(items=[RobotOut.model_validate(r) for r in rows], total=total)


@router.post("", response_model=RobotOut, status_code=status.HTTP_201_CREATED)
async def create_robot(
    body: RobotCreate,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    existing = (
        await db.execute(select(Robot).where(Robot.name == body.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 로봇 이름입니다.")

    robot = Robot(
        name=body.name,
        robot_type=body.robot_type,
        ip_address=body.ip_address,
        port=body.port,
        domain_id=body.domain_id,
        ai_server_url=body.ai_server_url,
        description=body.description,
        is_active=body.is_active,
    )
    db.add(robot)
    await db.commit()
    await db.refresh(robot)
    return RobotOut.model_validate(robot)


@router.put("/{robot_id}", response_model=RobotOut)
async def update_robot(
    robot_id: int,
    body: RobotUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    target = (await db.execute(select(Robot).where(Robot.id == robot_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="로봇을 찾을 수 없습니다.")

    # 요청에 실제로 포함된 필드만 반영(exclude_unset) → domain_id/description 등을 빈값으로 지울 수 있다.
    data = body.model_dump(exclude_unset=True)

    new_name = data.get("name")
    if new_name and new_name != target.name:
        dup = (
            await db.execute(select(Robot).where(Robot.name == new_name, Robot.id != robot_id))
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 로봇 이름입니다.")

    for field, value in data.items():
        # 이름/IP는 빈값으로 지우지 않는다(필수 값).
        if field in ("name", "ip_address", "robot_type", "port", "is_active") and value is None:
            continue
        setattr(target, field, value)

    await db.commit()
    await db.refresh(target)
    return RobotOut.model_validate(target)


@router.delete("/{robot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_robot(
    robot_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    target = (await db.execute(select(Robot).where(Robot.id == robot_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="로봇을 찾을 수 없습니다.")

    await db.delete(target)
    await db.commit()
