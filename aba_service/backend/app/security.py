from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import AdminUser

settings = get_settings()

# tokenUrl is informational (used by Swagger UI); auth is via Bearer header.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# 토큰 종류. 회원 토큰(`member_security.TOKEN_TYPE == "member"`)과 같은 시크릿을 쓰므로,
# 이 값을 검증하지 않으면 회원 토큰의 sub(회원 id)가 AdminUser id 로 해석되어
# **회원이 관리자 API 를 통과한다.** 반드시 발급·검증 양쪽에서 확인한다.
TOKEN_TYPE = "admin"


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "typ": TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def get_current_admin(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> AdminUser:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise cred_exc
    try:
        payload = decode_token(token)
        # 회원 토큰(typ="member")을 관리자 토큰으로 오인하지 않는다.
        if payload.get("typ") != TOKEN_TYPE:
            raise cred_exc
        subject = payload.get("sub")
        if subject is None:
            raise cred_exc
    except jwt.PyJWTError:
        raise cred_exc

    admin = db.get(AdminUser, int(subject))
    if admin is None or not admin.is_active:
        raise cred_exc
    return admin
