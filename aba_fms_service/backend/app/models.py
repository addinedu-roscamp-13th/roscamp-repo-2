from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import AdminBase, RobotBase


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Admin(AdminBase):
    __tablename__ = "rc_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Robot(AdminBase):
    """로봇 레지스트리 — 로봇팔(arm)/핑키봇(pinky) 등 각 로봇의 접속 IP를 관리."""

    __tablename__ = "rc_robots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    robot_type: Mapped[str] = mapped_column(String(16), nullable=False, default="arm")
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=9001)
    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_server_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class RobotLocation(AdminBase):
    """관제시스템 구역 위치. 중앙 MariaDB에 저장되는 로봇별 명명 위치."""

    __tablename__ = "rc_robot_locations"
    __table_args__ = (UniqueConstraint("robot_id", "name", name="uq_robot_location_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    robot_id: Mapped[int] = mapped_column(Integer, ForeignKey("rc_robots.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    yaw: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class LocationAction(AdminBase):
    """관제 구역(RobotLocation) 도착 시 실행할 액션 설정.

    구역 이름(robot_id + name)마다 액션 타입/파라미터를 저장한다.
    실제 실행은 관제 페이지(/admin/control)가 로봇을 직접 호출해 담당하고,
    여기서는 마커 액션(rc_marker_actions)과 동일한 스키마로 설정만 저장한다.
    """

    __tablename__ = "rc_location_actions"
    __table_args__ = (UniqueConstraint("robot_id", "name", name="uq_location_action_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    robot_id: Mapped[int] = mapped_column(Integer, ForeignKey("rc_robots.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False, default="none")
    params: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 문자열
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class RobotLearnedAction(AdminBase):
    """Natural-language training entry mapped to a concrete driving robot command."""

    __tablename__ = "rc_robot_learned_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_phrases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    robot_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="selected")
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class RobotLearnedScenario(AdminBase):
    """Node graph scenario for driving robot workflows."""

    __tablename__ = "rc_robot_learned_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_phrases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    nodes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    edges: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class PinkyGreetingSettings(AdminBase):
    __tablename__ = "rc_pinky_greeting_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="안녕하세요!\nPinky Pro 감지됨 :)")
    font_name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    font_size: Mapped[int] = mapped_column(Integer, nullable=False, default=28)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#ffffff")
    bg_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#000000")
    align: Mapped[str] = mapped_column(String(16), nullable=False, default="center")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class FleetCoordinatorSettings(AdminBase):
    """근접 안전 코디네이터 설정 (단일 행). in-memory 만 쓰면 백엔드 재시작 시
    enabled/거리 설정이 리셋되어 보호가 조용히 꺼지므로 DB 에 영속화한다."""
    __tablename__ = "rc_fleet_coordinator_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 맵이 작고 구역 간격이 ~0.85m 라, 인접 구역 정상 순회를 오정지하지 않도록 값을 맞춘다.
    min_distance: Mapped[float] = mapped_column(Float, nullable=False, default=0.45)
    clear_distance: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    priorities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {robot_id: priority}
    # 막는 로봇을 경로 밖으로 자동 비켜세우기(evasion). 자동 주행이라 기본 OFF(운영자 명시적 opt-in).
    evasion_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class Conversation(RobotBase):
    __tablename__ = "rc_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(RobotBase):
    __tablename__ = "rc_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rc_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")


class AiModelSettings(RobotBase):
    __tablename__ = "rc_ai_model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    selected_model: Mapped[str] = mapped_column(String(128), nullable=False, default="qwen3:1.7b")
    ollama_url: Mapped[str] = mapped_column(
        String(255), nullable=False, default="http://127.0.0.1:11434"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

class LcdImage(RobotBase):
    __tablename__ = "rc_lcd_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class MotionSequence(RobotBase):
    __tablename__ = "rc_motion_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    waypoints: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-serialized waypoints
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class MarkerAction(RobotBase):
    """아르코 마커 ID별로 관리자가 지정한 액션. 마커 인식 시 실행된다."""

    __tablename__ = "rc_marker_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marker_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # dock | rotate | move | lcd_emotion | lcd_text | lcd_image | none
    action_type: Mapped[str] = mapped_column(String(24), nullable=False, default="none")
    params: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 문자열
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


