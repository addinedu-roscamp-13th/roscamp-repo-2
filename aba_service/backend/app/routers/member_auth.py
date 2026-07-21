"""회원 로그인 / 내 정보.

관리자 로그인(`/api/admin/auth/*`)과 경로도 토큰도 분리돼 있다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..member_security import create_member_token, get_current_member
from ..models import Member
from ..security import verify_password

router = APIRouter(prefix="/api/member/auth", tags=["member-auth"])


class MemberLoginRequest(BaseModel):
    username: str
    password: str


class MemberTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    member: "MemberOut"


class MemberOut(BaseModel):
    id: int
    username: str
    full_name: str | None = None


MemberTokenResponse.model_rebuild()


def _to_out(m: Member) -> MemberOut:
    return MemberOut(id=m.id, username=m.username, full_name=m.full_name)


@router.post("/login", response_model=MemberTokenResponse)
def login(data: MemberLoginRequest, db: Session = Depends(get_db)):
    member = db.scalar(select(Member).where(Member.username == data.username))
    # 아이디 존재 여부를 노출하지 않도록 실패 메시지는 하나로 통일한다.
    if member is None or not verify_password(data.password, member.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다",
        )
    if not member.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="이용이 정지된 계정입니다"
        )
    return MemberTokenResponse(
        access_token=create_member_token(member.id), member=_to_out(member)
    )


@router.get("/me", response_model=MemberOut)
def me(current: Member = Depends(get_current_member)):
    return _to_out(current)
