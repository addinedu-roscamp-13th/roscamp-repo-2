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


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
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
        subject = payload.get("sub")
        if subject is None:
            raise cred_exc
    except jwt.PyJWTError:
        raise cred_exc

    admin = db.get(AdminUser, int(subject))
    if admin is None or not admin.is_active:
        raise cred_exc
    return admin
