from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(subject: str | int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(subject), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub: str | None = payload.get("sub")
        if sub is None:
            raise ValueError("no sub")
        return sub
    except JWTError as exc:
        raise ValueError("invalid token") from exc
