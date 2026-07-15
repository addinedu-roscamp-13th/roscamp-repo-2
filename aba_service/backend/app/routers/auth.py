from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser
from ..schemas import AdminOut, LoginRequest, TokenResponse
from ..security import create_access_token, get_current_admin, verify_password

router = APIRouter(prefix="/api/admin/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    admin = db.scalar(select(AdminUser).where(AdminUser.username == data.username))
    if admin is None or not verify_password(data.password, admin.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다."
        )

    admin.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(admin)

    token = create_access_token(str(admin.id))
    return TokenResponse(access_token=token, admin=AdminOut.model_validate(admin))


@router.get("/me", response_model=AdminOut)
def me(current: AdminUser = Depends(get_current_admin)):
    return current
