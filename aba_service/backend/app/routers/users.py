from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminRole, AdminUser
from ..schemas import AdminCreate, AdminListResponse, AdminOut, AdminUpdate
from ..security import get_current_admin, hash_password

router = APIRouter(prefix="/api/admin/users", tags=["users"])


def _count_superadmins(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(AdminUser)
            .where(AdminUser.role == AdminRole.superadmin, AdminUser.is_active.is_(True))
        )
        or 0
    )


@router.get("", response_model=AdminListResponse)
def list_users(
    q: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    stmt = select(AdminUser)
    count_stmt = select(func.count()).select_from(AdminUser)
    if q:
        like = f"%{q}%"
        cond = or_(
            AdminUser.username.like(like),
            AdminUser.email.like(like),
            AdminUser.full_name.like(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.scalar(count_stmt) or 0
    items = db.scalars(
        stmt.order_by(AdminUser.created_at.desc()).offset(skip).limit(limit)
    ).all()
    return AdminListResponse(
        items=[AdminOut.model_validate(i) for i in items], total=total
    )


@router.post("", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
def create_user(
    data: AdminCreate,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    if db.scalar(select(AdminUser).where(AdminUser.username == data.username)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 사용자명입니다."
        )
    admin = AdminUser(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        role=data.role,
        is_active=data.is_active,
        hashed_password=hash_password(data.password),
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


@router.get("/{user_id}", response_model=AdminOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    admin = db.get(AdminUser, user_id)
    if admin is None:
        raise HTTPException(status_code=404, detail="관리자를 찾을 수 없습니다.")
    return admin


@router.put("/{user_id}", response_model=AdminOut)
def update_user(
    user_id: int,
    data: AdminUpdate,
    db: Session = Depends(get_db),
    current: AdminUser = Depends(get_current_admin),
):
    admin = db.get(AdminUser, user_id)
    if admin is None:
        raise HTTPException(status_code=404, detail="관리자를 찾을 수 없습니다.")

    if data.email is not None:
        admin.email = data.email
    if data.full_name is not None:
        admin.full_name = data.full_name
    if data.role is not None and data.role != admin.role:
        if admin.role == AdminRole.superadmin and _count_superadmins(db) <= 1:
            raise HTTPException(
                status_code=400, detail="마지막 슈퍼관리자의 권한은 변경할 수 없습니다."
            )
        admin.role = data.role
    if data.is_active is not None:
        if not data.is_active and admin.id == current.id:
            raise HTTPException(status_code=400, detail="자기 자신을 비활성화할 수 없습니다.")
        if (
            not data.is_active
            and admin.role == AdminRole.superadmin
            and _count_superadmins(db) <= 1
        ):
            raise HTTPException(
                status_code=400, detail="마지막 슈퍼관리자는 비활성화할 수 없습니다."
            )
        admin.is_active = data.is_active
    if data.password:
        admin.hashed_password = hash_password(data.password)

    db.commit()
    db.refresh(admin)
    return admin


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current: AdminUser = Depends(get_current_admin),
):
    admin = db.get(AdminUser, user_id)
    if admin is None:
        raise HTTPException(status_code=404, detail="관리자를 찾을 수 없습니다.")
    if admin.id == current.id:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
    if admin.role == AdminRole.superadmin and _count_superadmins(db) <= 1:
        raise HTTPException(status_code=400, detail="마지막 슈퍼관리자는 삭제할 수 없습니다.")

    db.delete(admin)
    db.commit()
    return None
