from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


# ── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: AdminOut


# ── Admin ─────────────────────────────────────────────────────────────────────

AdminRole = Literal["admin", "superadmin"]


class AdminOut(BaseModel):
    id: int
    username: str
    email: str | None
    full_name: str | None
    role: AdminRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime | None

    model_config = {"from_attributes": True}


class AdminCreate(BaseModel):
    username: str
    password: str
    email: str | None = None
    full_name: str | None = None
    role: AdminRole = "admin"
    is_active: bool = True


class AdminUpdate(BaseModel):
    password: str | None = None
    email: str | None = None
    full_name: str | None = None
    role: AdminRole | None = None
    is_active: bool | None = None


class AdminListResponse(BaseModel):
    items: list[AdminOut]
    total: int


# ── Robot ─────────────────────────────────────────────────────────────────────

RobotType = Literal["arm", "pinky", "other", "server"]


class RobotOut(BaseModel):
    id: int
    name: str
    robot_type: RobotType
    ip_address: str
    port: int
    domain_id: int | None
    ai_server_url: str | None
    description: str | None
    is_active: bool
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class RobotCreate(BaseModel):
    name: str
    robot_type: RobotType = "arm"
    ip_address: str
    port: int = 9001
    domain_id: int | None = None
    ai_server_url: str | None = None
    description: str | None = None
    is_active: bool = True


class RobotUpdate(BaseModel):
    name: str | None = None
    robot_type: RobotType | None = None
    ip_address: str | None = None
    port: int | None = None
    domain_id: int | None = None
    ai_server_url: str | None = None
    description: str | None = None
    is_active: bool | None = None


class RobotListResponse(BaseModel):
    items: list[RobotOut]
    total: int


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DayCount(BaseModel):
    date: str
    count: int


class DashboardStats(BaseModel):
    total_admins: int
    active_admins: int
    total_conversations: int
    total_messages: int
    selected_model: str | None
    conversations_per_day: list[DayCount]
    recent_admins: list[AdminOut]


# ── Dev / Schema ──────────────────────────────────────────────────────────────

class SchemaColumn(BaseModel):
    name: str
    type: str
    key: Literal["", "PK", "FK", "UQ"]
    nullable: bool
    defaultValue: str | None
    description: str


class SchemaTable(BaseModel):
    tableName: str
    description: str
    columns: list[SchemaColumn]


class ErdRelation(BaseModel):
    fromTable: str
    fromColumn: str
    toTable: str
    toColumn: str


class ErdResponse(BaseModel):
    tables: list[SchemaTable]
    relations: list[ErdRelation]
