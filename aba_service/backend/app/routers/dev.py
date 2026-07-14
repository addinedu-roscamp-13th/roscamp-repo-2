"""Dev Center endpoints: live MariaDB schema introspection for the table
definition viewer and the ERD page. Read-only, admin-only."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminUser
from ..schemas import ErdResponse, ErdRelation, SchemaColumn, SchemaTable
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/dev", tags=["dev-center"])


def _load_schema(db: Session) -> tuple[list[SchemaTable], list[ErdRelation]]:
    # foreign keys (for FK badges + ERD relations)
    fk_rows = db.execute(
        text(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
        )
    ).all()
    fk_cols = {(r[0], r[1]) for r in fk_rows}
    relations = [
        ErdRelation(
            from_table=r[0], from_column=r[1], to_table=r[2], to_column=r[3]
        )
        for r in fk_rows
    ]

    # table comments
    table_rows = db.execute(
        text(
            "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME"
        )
    ).all()
    table_desc = {r[0]: (r[1] or "") for r in table_rows}

    # columns
    col_rows = db.execute(
        text(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY, IS_NULLABLE, "
            "COLUMN_DEFAULT, COLUMN_COMMENT "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
    ).all()

    by_table: dict[str, list[SchemaColumn]] = {}
    for tname, cname, ctype, ckey, nullable, default, comment in col_rows:
        if (tname, cname) in fk_cols:
            key = "FK"
        elif ckey == "PRI":
            key = "PK"
        elif ckey == "UNI":
            key = "UQ"
        else:
            key = ""
        by_table.setdefault(tname, []).append(
            SchemaColumn(
                name=cname,
                type=ctype,
                key=key,
                nullable=(nullable == "YES"),
                default_value=None if default is None else str(default),
                description=comment or "",
            )
        )

    tables = [
        SchemaTable(
            table_name=tname,
            description=table_desc.get(tname, ""),
            columns=by_table.get(tname, []),
        )
        for tname in table_desc
    ]
    return tables, relations


@router.get("/tables", response_model=list[SchemaTable])
def table_definitions(
    db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    tables, _rel = _load_schema(db)
    return tables


@router.get("/erd", response_model=ErdResponse)
def erd(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    tables, relations = _load_schema(db)
    return ErdResponse(tables=tables, relations=relations)
