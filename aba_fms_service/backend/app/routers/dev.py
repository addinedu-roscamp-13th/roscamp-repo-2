from fastapi import APIRouter, Depends
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import admin_engine, robot_engine, get_db
from app.deps import get_current_admin
from app.models import Admin
from app.schemas import ErdRelation, ErdResponse, SchemaColumn, SchemaTable

router = APIRouter(prefix="/api/dev", tags=["dev"])

_TABLE_DESCRIPTIONS: dict[str, str] = {
    "admins": "관리자 계정 정보",
    "conversations": "챗봇 대화 세션",
    "messages": "대화 메시지 내역",
    "ai_model_settings": "AI 모델 설정 (싱글톤)",
}

_COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "admins": {
        "id": "기본 키",
        "username": "로그인 아이디",
        "password_hash": "bcrypt 해시 비밀번호",
        "email": "이메일 주소",
        "full_name": "이름",
        "role": "권한 (admin / superadmin)",
        "is_active": "계정 활성 여부",
        "last_login_at": "마지막 로그인 시각",
        "created_at": "계정 생성 시각",
    },
    "conversations": {
        "id": "기본 키",
        "session_id": "세션 식별자 (고유)",
        "title": "대화 제목",
        "model_name": "사용 AI 모델",
        "created_at": "생성 시각",
        "updated_at": "마지막 수정 시각",
    },
    "messages": {
        "id": "기본 키",
        "conversation_id": "대화 FK",
        "role": "발신자 역할 (user / assistant / system)",
        "content": "메시지 내용",
        "model_name": "사용 AI 모델",
        "created_at": "생성 시각",
    },
    "ai_model_settings": {
        "id": "항상 1 (싱글톤)",
        "selected_model": "선택된 Ollama 모델 이름",
        "ollama_url": "Ollama 서버 URL",
        "updated_at": "마지막 수정 시각",
    },
}


@router.get("/tables", response_model=list[SchemaTable])
async def get_tables(_admin: Admin = Depends(get_current_admin)):
    tables: list[SchemaTable] = []

    def _inspect(sync_conn):
        insp = inspect(sync_conn)
        result = []
        for table_name in insp.get_table_names():
            cols_raw = insp.get_columns(table_name)
            pk_cols = set(insp.get_pk_constraint(table_name).get("constrained_columns", []))
            fk_cols = {
                fk["constrained_columns"][0]
                for fk in insp.get_foreign_keys(table_name)
                if fk["constrained_columns"]
            }
            uq_cols = {
                col
                for uq in insp.get_unique_constraints(table_name)
                for col in uq["column_names"]
            }
            columns: list[SchemaColumn] = []
            for c in cols_raw:
                name = c["name"]
                if name in pk_cols:
                    key = "PK"
                elif name in fk_cols:
                    key = "FK"
                elif name in uq_cols:
                    key = "UQ"
                else:
                    key = ""
                default = c.get("default")
                columns.append(
                    SchemaColumn(
                        name=name,
                        type=str(c["type"]),
                        key=key,
                        nullable=c.get("nullable", True),
                        defaultValue=str(default) if default is not None else None,
                        description=_COLUMN_DESCRIPTIONS.get(table_name, {}).get(name, ""),
                    )
                )
            result.append(
                SchemaTable(
                    tableName=table_name,
                    description=_TABLE_DESCRIPTIONS.get(table_name, ""),
                    columns=columns,
                )
            )
        return result

    async with admin_engine.connect() as conn:
        tables.extend(await conn.run_sync(_inspect))

    async with robot_engine.connect() as conn:
        tables.extend(await conn.run_sync(_inspect))

    return tables


@router.get("/erd", response_model=ErdResponse)
async def get_erd(_admin: Admin = Depends(get_current_admin)):
    tables: list[SchemaTable] = []
    relations: list[ErdRelation] = []

    def _inspect(sync_conn):
        insp = inspect(sync_conn)
        tbls = []
        rels = []
        for table_name in insp.get_table_names():
            cols_raw = insp.get_columns(table_name)
            pk_cols = set(insp.get_pk_constraint(table_name).get("constrained_columns", []))
            fk_map = {
                fk["constrained_columns"][0]: fk
                for fk in insp.get_foreign_keys(table_name)
                if fk["constrained_columns"]
            }
            uq_cols = {
                col
                for uq in insp.get_unique_constraints(table_name)
                for col in uq["column_names"]
            }
            columns = []
            for c in cols_raw:
                name = c["name"]
                if name in pk_cols:
                    key = "PK"
                elif name in fk_map:
                    key = "FK"
                elif name in uq_cols:
                    key = "UQ"
                else:
                    key = ""
                default = c.get("default")
                columns.append(
                    SchemaColumn(
                        name=name,
                        type=str(c["type"]),
                        key=key,
                        nullable=c.get("nullable", True),
                        defaultValue=str(default) if default is not None else None,
                        description=_COLUMN_DESCRIPTIONS.get(table_name, {}).get(name, ""),
                    )
                )
            tbls.append(
                SchemaTable(
                    tableName=table_name,
                    description=_TABLE_DESCRIPTIONS.get(table_name, ""),
                    columns=columns,
                )
            )
            for col_name, fk in fk_map.items():
                referred = fk["referred_table"]
                referred_cols = fk.get("referred_columns", ["id"])
                rels.append(
                    ErdRelation(
                        fromTable=table_name,
                        fromColumn=col_name,
                        toTable=referred,
                        toColumn=referred_cols[0] if referred_cols else "id",
                    )
                )
        return tbls, rels

    async with admin_engine.connect() as conn:
        tbls, rels = await conn.run_sync(_inspect)
        tables.extend(tbls)
        relations.extend(rels)

    async with robot_engine.connect() as conn:
        tbls, rels = await conn.run_sync(_inspect)
        tables.extend(tbls)
        relations.extend(rels)

    return ErdResponse(tables=tables, relations=relations)
