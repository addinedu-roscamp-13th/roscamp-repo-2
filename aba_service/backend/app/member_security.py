"""회원 인증 — 관리자(`security.py`)와 **분리된** 토큰 체계.

## 왜 분리하나
관리자와 회원은 권한 범위가 완전히 다르다. 같은 JWT 로 둘을 태우면 `sub` 하나로
관리자 API 와 회원 API 가 모두 열려버린다. 그래서 페이로드에 `typ` 을 실어
발급하고, 검증할 때 `typ` 이 맞는지 확인한다 — **관리자 토큰으로 회원 API 를 부르면
401 이어야 한다.**

비밀번호 해시는 관리자와 같은 bcrypt 헬퍼(`security.hash_password`)를 쓴다.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Member

settings = get_settings()

TOKEN_TYPE = "member"

member_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/member/auth/login", auto_error=False
)


def create_member_token(member_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(member_id),
        "typ": TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_member(
    token: str | None = Depends(member_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Member:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="회원 인증이 필요합니다",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        raise cred_exc

    # 관리자 토큰(typ 없음)으로는 들어올 수 없다.
    if payload.get("typ") != TOKEN_TYPE:
        raise cred_exc

    subject = payload.get("sub")
    if subject is None:
        raise cred_exc

    member = db.get(Member, int(subject))
    if member is None or not member.is_active:
        raise cred_exc
    return member
