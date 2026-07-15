from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from .models import AdminRole


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    full_name: str | None = None
    role: AdminRole
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: AdminOut


class AdminCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=128)
    role: AdminRole = AdminRole.admin
    is_active: bool = True


class AdminUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=6, max_length=128)
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=128)
    role: AdminRole | None = None
    is_active: bool | None = None


class AdminListResponse(BaseModel):
    items: list[AdminOut]
    total: int


class DayCount(BaseModel):
    date: str
    count: int


class DashboardStats(BaseModel):
    total_admins: int
    active_admins: int
    total_conversations: int
    total_messages: int
    selected_model: str | None = None
    conversations_per_day: list[DayCount]
    recent_admins: list[AdminOut]


# --- Customer-facing books / recommendations ---


class BookOut(BaseModel):
    """A catalog book shaped like the frontend `Book` type (multi-lang dicts)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: dict[str, str]
    author: str
    category: str
    cover: str
    color: str
    zone: str
    shelf: str
    in_stock: bool = Field(serialization_alias="inStock")
    summary: dict[str, str]
    for_whom: dict[str, list[str]] = Field(serialization_alias="forWhom")


# --- Dev Center: schema introspection (camelCase JSON for the frontend) ---


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class SchemaColumn(_CamelModel):
    name: str
    type: str
    key: str = ""  # "PK" | "FK" | "UQ" | ""
    nullable: bool
    default_value: str | None = None
    description: str = ""


class SchemaTable(_CamelModel):
    table_name: str
    description: str = ""
    columns: list[SchemaColumn]


class ErdRelation(_CamelModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class ErdResponse(_CamelModel):
    tables: list[SchemaTable]
    relations: list[ErdRelation]
