from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.models import Admin
from app.schemas import AdminCreate, AdminListResponse, AdminOut, AdminUpdate
from app.security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=AdminListResponse)
async def list_users(
    q: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _admin: Admin = Depends(get_current_admin),
):
    stmt = select(Admin)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Admin.username.ilike(like),
                Admin.full_name.ilike(like),
                Admin.email.ilike(like),
            )
        )
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Admin.created_at.desc()).offset(skip).limit(limit))).scalars().all()
    return AdminListResponse(items=[AdminOut.model_validate(r) for r in rows], total=total)


@router.post("", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminCreate,
    db: AsyncSession = Depends(get_db),
    current: Admin = Depends(get_current_admin),
):
    if current.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="슈퍼어드민 권한이 필요합니다.")

    existing = (await db.execute(select(Admin).where(Admin.username == body.username))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 아이디입니다.")

    admin = Admin(
        username=body.username,
        password_hash=hash_password(body.password),
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        is_active=body.is_active,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return AdminOut.model_validate(admin)


@router.put("/{admin_id}", response_model=AdminOut)
async def update_user(
    admin_id: int,
    body: AdminUpdate,
    db: AsyncSession = Depends(get_db),
    current: Admin = Depends(get_current_admin),
):
    if current.role != "superadmin" and current.id != admin_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="권한이 없습니다.")

    target = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="관리자를 찾을 수 없습니다.")

    if body.password is not None:
        target.password_hash = hash_password(body.password)
    if body.email is not None:
        target.email = body.email
    if body.full_name is not None:
        target.full_name = body.full_name
    if body.role is not None and current.role == "superadmin":
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active

    await db.commit()
    await db.refresh(target)
    return AdminOut.model_validate(target)


@router.delete("/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    admin_id: int,
    db: AsyncSession = Depends(get_db),
    current: Admin = Depends(get_current_admin),
):
    if current.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="슈퍼어드민 권한이 필요합니다.")
    if current.id == admin_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="자신의 계정은 삭제할 수 없습니다.")

    target = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="관리자를 찾을 수 없습니다.")

    await db.delete(target)
    await db.commit()
