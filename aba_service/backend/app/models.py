import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AdminRole(str, enum.Enum):
    superadmin = "superadmin"
    admin = "admin"


class AdminUser(Base):
    """Admin account — used both for login and as the managed user list."""

    __tablename__ = "cb_admin_users"
    # Convention: every MariaDB table/column carries a COMMENT (see CLAUDE.md).
    __table_args__ = {"comment": "관리자 계정: 로그인 및 관리자 목록 CRUD 대상"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="로그인 아이디 (고유)"
    )
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, comment="이메일 (고유, 선택)"
    )
    full_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="표시 이름"
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="bcrypt 해시된 비밀번호 (평문 저장 금지)"
    )
    role: Mapped[AdminRole] = mapped_column(
        Enum(AdminRole),
        nullable=False,
        default=AdminRole.admin,
        comment="권한 등급: superadmin | admin",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="활성 여부 (0=비활성, 1=활성)"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="마지막 로그인 시각"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="생성 시각"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="수정 시각 (자동 갱신)",
    )


class Book(Base):
    """Bookstore catalog — customer-facing search & recommendation source."""

    __tablename__ = "cb_books"
    # Convention: every MariaDB table/column carries a COMMENT (see CLAUDE.md).
    __table_args__ = {"comment": "도서 카탈로그: 고객용 검색/추천 데이터 원천"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    title_kr: Mapped[str] = mapped_column(String(255), nullable=False, comment="도서 제목 (한국어)")
    title_en: Mapped[str] = mapped_column(String(255), nullable=False, comment="도서 제목 (영어)")
    title_zh: Mapped[str] = mapped_column(String(255), nullable=False, comment="도서 제목 (중국어)")
    title_vi: Mapped[str] = mapped_column(String(255), nullable=False, comment="도서 제목 (베트남어)")
    author: Mapped[str] = mapped_column(String(255), nullable=False, comment="저자명")
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="도서 카테고리 (literature | art | science | humanities | kids)",
    )
    cover: Mapped[str] = mapped_column(String(50), nullable=False, comment="도서 표지 이모지")
    color: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Tailwind 그라디언트 배경 클래스"
    )
    zone: Mapped[str] = mapped_column(String(50), nullable=False, comment="도서관 내 구역 (예: A-2)")
    shelf: Mapped[str] = mapped_column(String(50), nullable=False, comment="서가 위치/줄 설명")
    in_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="대출 가능 여부 (0=대출 중, 1=대출가능)"
    )
    summary_kr: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="도서 요약 (한국어)")
    summary_en: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="도서 요약 (영어)")
    summary_zh: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="도서 요약 (중국어)")
    summary_vi: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="도서 요약 (베트남어)")
    for_whom_kr: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="추천 대상/해시태그 JSON 배열 (한국어)")
    for_whom_en: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="추천 대상/해시태그 JSON 배열 (영어)")
    for_whom_zh: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="추천 대상/해시태그 JSON 배열 (중국어)")
    for_whom_vi: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="추천 대상/해시태그 JSON 배열 (베트남어)")


class Member(Base):
    """도서관 회원 계정 — 관리자(`cb_admin_users`)와 완전히 분리된 주체다.

    회원 토큰과 관리자 토큰이 섞이면 권한 사고가 나므로, 인증도 별도 모듈
    (`member_security.py`)에서 `typ="member"` 를 실어 발급/검증한다.
    """

    __tablename__ = "cb_members"
    __table_args__ = {"comment": "도서관 회원 계정: 로그인 및 대출 주체"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="로그인 아이디 (고유)"
    )
    full_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="회원 이름"
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="bcrypt 해시된 비밀번호 (평문 저장 금지)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="활성 여부 (0=정지, 1=정상)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="가입 시각"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="수정 시각 (자동 갱신)",
    )


class Loan(Base):
    """대출 1건.

    ⚠️ 대출은 **사서가 확정**한다. 회원 앱의 '대여 요청'은 로봇이 안내데스크로 책을
    가져다 놓는 것까지이고, 이 행이 생기는 시점은 사서가 대출 처리를 눌렀을 때다.
    """

    __tablename__ = "cb_loans"
    __table_args__ = {"comment": "도서 대출 기록 (사서가 확정)"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_members.id", ondelete="CASCADE"),
        nullable=False,
        comment="대출한 회원",
    )
    book_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_books.id", ondelete="CASCADE"),
        nullable=False,
        comment="대출된 도서",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="borrowed",
        comment="상태 (borrowed | returned)",
    )
    extend_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="연장 횟수 (최대 1회)"
    )
    borrowed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="대출 시각"
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="반납 예정일"
    )
    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="실제 반납 시각 (미반납이면 NULL)"
    )


class Reservation(Base):
    """대출 중인 책에 거는 예약(대기)."""

    __tablename__ = "cb_reservations"
    __table_args__ = {"comment": "도서 예약 대기열 (대출 중인 책 대상)"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_members.id", ondelete="CASCADE"),
        nullable=False,
        comment="예약한 회원",
    )
    book_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_books.id", ondelete="CASCADE"),
        nullable=False,
        comment="예약 대상 도서",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="waiting",
        comment="상태 (waiting | ready | cancelled | fulfilled)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="예약 시각"
    )


class Wishlist(Base):
    """읽고 싶은 책(즐겨찾기)."""

    __tablename__ = "cb_wishlist"
    __table_args__ = {"comment": "회원별 읽고 싶은 책 목록"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_members.id", ondelete="CASCADE"),
        nullable=False,
        comment="회원",
    )
    book_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_books.id", ondelete="CASCADE"),
        nullable=False,
        comment="도서",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="담은 시각"
    )


class DeliveryRequest(Base):
    """회원이 넣은 로봇 배달 요청 — FMS 주문과 1:1로 대응한다.

    FMS orchestrator 가 진짜 상태를 들고 있으므로 여기 상태는 **사본이 아니라 접수 기록**이다
    (누가·어떤 책을·어디로 요청했고 FMS task_id 가 무엇인지). 진행 상황은 FMS 에 물어본다.

    ## 승인(`approval`)
    `borrow`(대여)는 책이 관외로 나가므로 **사서 승인 뒤에야** FMS 주문이 나간다. 승인 전에는
    `approval='PENDING_APPROVAL'` 이고 `fms_task_id` 가 빈 문자열이다. `read`(열람)는 책이
    관내에 남아 되돌리기 쉬우므로 승인 없이 바로 주문한다(`approval='APPROVED'`).
    """

    __tablename__ = "cb_delivery_requests"
    __table_args__ = {"comment": "회원 로봇 배달 요청 접수 기록 (FMS 주문과 대응)"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_members.id", ondelete="CASCADE"),
        nullable=False,
        comment="요청한 회원",
    )
    book_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cb_books.id", ondelete="CASCADE"),
        nullable=False,
        comment="요청 도서",
    )
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="요청 종류 (read=열람·테이블 배달 | borrow=대여·안내데스크 이송)",
    )
    pickup: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="집는 waypoint (도서의 zone)"
    )
    dropoff: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="전달 waypoint (테이블 또는 안네데스크)"
    )
    fms_task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="FMS orchestrator 가 발급한 task_id (승인 전이면 빈 문자열)",
    )
    approval: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="APPROVED",
        server_default="APPROVED",
        comment="승인 상태 (PENDING_APPROVAL=사서 승인 대기 | APPROVED=승인·주문됨 | REJECTED=반려)",
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="사서가 승인/반려한 시각 (승인 불필요면 NULL)"
    )
    decided_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="승인/반려한 사서 계정"
    )
    reject_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="반려 사유 (반려가 아니면 NULL)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="접수 시각"
    )


class OpsSetting(Base):
    """운영 설정 — 키/값 한 줄짜리 저장소.

    지금은 야간 보안 모드(`security_mode`: day | night)만 쓴다. 설정이 늘어날 때마다
    테이블을 만들지 않으려고 키/값으로 둔다. 값은 문자열이고 해석은 호출부가 한다.
    """

    __tablename__ = "cb_ops_settings"
    __table_args__ = {"comment": "운영 설정 키/값 (야간 보안 모드 등)"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True, comment="설정 키")
    value: Mapped[str] = mapped_column(String(255), nullable=False, comment="설정 값")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="수정 시각",
    )


class IntrusionEvent(Base):
    """야간 침입 감지 기록.

    감지 주체는 로봇/AI 서비스다 — 여기는 **보고를 받아 쌓는 곳**이다. 영상은 파일 경로만
    들고 있고 바이트는 저장하지 않는다(용량).
    """

    __tablename__ = "cb_intrusion_events"
    __table_args__ = {"comment": "야간 보안 침입 감지 이벤트"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="감지 시각"
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="감지 주체 (로봇 이름 / 고정 카메라)"
    )
    zone: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="감지 위치(내비 정점)"
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="비고")
    clip_path: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="침입 영상 파일 경로 (없으면 NULL)"
    )
    acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="사서 확인 여부"
    )


class TaskLog(Base):
    """작업 성공/실패·재시도 로그.

    FMS orchestrator 는 진행 중인 큐만 들고 있고 **끝난 작업은 치우면 사라진다.** 운영
    돌아보기(리포트·재시도 추적)를 하려면 별도 보관이 필요해서 여기 남긴다.
    """

    __tablename__ = "cb_task_logs"
    __table_args__ = {"comment": "로봇 작업 결과 로그 (성공/실패/재시도)"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="기본키"
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="FMS task_id")
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="작업 종류 (transfer/sort/patrol ...)"
    )
    robot: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="수행 로봇")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="결과 (COMPLETED | FAILED | CANCELLED)"
    )
    leg_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="완료 다리 수")
    leg_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="전체 다리 수")
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="재시도 횟수")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="실패 사유")
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="기록 시각"
    )


class RobotControlLog(Base):
    """Logs of chatbot robot control commands."""

    __tablename__ = "cb_robot_control_logs"
    __table_args__ = {"comment": "로봇 자연어 제어 및 실행 로그"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="기본키")
    user_message: Mapped[str] = mapped_column(String(500), nullable=False, comment="사용자 입력 메시지")
    robot_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="제어 대상 로봇 (mobile | arm)")
    action: Mapped[str] = mapped_column(String(50), nullable=False, comment="분석된 액션")
    parameters: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="파라미터 (JSON 문자열)")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", comment="실행 상태 (pending | success | failed)")
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="오류 메시지")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="생성 시각")

