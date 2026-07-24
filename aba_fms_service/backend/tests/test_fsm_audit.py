from app.fsm_audit import build_log_entry


def test_log_entry_records_who_what_when():
    """INSTRUCTION.md 안전 규칙: '누가 언제 어떤 전이를 강제했는지 로그로 남긴다'."""
    entry = build_log_entry(
        admin_id=7,
        admin_username="libi_admin",
        robot_id="pinky1",
        from_state="CHARGING",
        to_state="WORKING",
        forced=True,
        accepted=True,
        reason="강제 전이: 'CHARGING' -> 'WORKING' 는 전이 박스에 없는 간선입니다.",
    )
    assert entry["admin_id"] == 7
    assert entry["admin_username"] == "libi_admin"
    assert entry["robot_id"] == "pinky1"
    assert entry["from_state"] == "CHARGING"
    assert entry["to_state"] == "WORKING"
    assert entry["forced"] is True
    assert entry["accepted"] is True
    assert "강제" in entry["reason"]


def test_rejected_transitions_are_also_logged():
    """거부된 시도도 남겨야 감사 가치가 있다."""
    entry = build_log_entry(
        admin_id=1, admin_username="op", robot_id="pinky2",
        from_state="ERROR", to_state="IDLE", forced=False, accepted=False,
        reason="ERROR 이탈은 error_code 확인 후에만 허용됩니다.",
    )
    assert entry["accepted"] is False
    assert entry["reason"]


def test_long_strings_are_truncated_to_column_width():
    """자르지 않으면 MariaDB 가 행 전체를 거부해 감사 기록이 통째로 사라진다."""
    entry = build_log_entry(
        admin_id=1, admin_username="u" * 500, robot_id="r" * 500,
        from_state="s" * 500, to_state="t" * 500, forced=False, accepted=True,
        reason="x" * 500,
    )
    assert len(entry["reason"]) <= 255
    assert len(entry["robot_id"]) <= 40
    assert len(entry["from_state"]) <= 24
    assert len(entry["to_state"]) <= 24
    assert len(entry["admin_username"]) <= 80


def test_none_values_become_empty_strings_not_crashes():
    """전이 실패 경로에서 current_state 가 None 으로 올 수 있다."""
    entry = build_log_entry(
        admin_id=None, admin_username="", robot_id="pinky1",
        from_state="", to_state="IDLE", forced=False, accepted=False,
        reason="",
    )
    assert entry["admin_id"] is None
    assert entry["admin_username"] == ""
    assert entry["reason"] == ""


def test_truthiness_is_normalised_to_bool():
    """SQLAlchemy Boolean 컬럼에 0/1 이나 문자열이 들어가지 않게 한다."""
    entry = build_log_entry(
        admin_id=1, admin_username="op", robot_id="pinky1",
        from_state="IDLE", to_state="WORKING", forced=1, accepted=0, reason="",
    )
    assert entry["forced"] is True
    assert entry["accepted"] is False


def test_module_imports_without_sqlalchemy():
    """순수 판정 로직은 DB 스택 없이 import 되어야 한다 (테스트 가능성의 근거)."""
    import inspect

    from app import fsm_audit

    header = inspect.getsource(fsm_audit).split("def build_log_entry")[0]
    assert "from sqlalchemy" not in header
    assert "from app.models" not in header


# ── 이력 삭제 ─────────────────────────────────────────────────────────────────
#
# 여기서 지키는 건 하나다: **한 로봇만 지우라고 했는데 전체를 지우는 일**이 없어야 한다.
# 되돌릴 수 없는 삭제라 이 실수는 조용하고 치명적이다.

import asyncio

import pytest

pytest.importorskip("sqlalchemy", reason="삭제 경로는 SQLAlchemy 가 있어야 한다")


class _FakeResult:
    rowcount = 3


class _FakeDb:
    """execute 로 넘어온 statement 를 붙잡아 두는 최소 스텁."""

    def __init__(self):
        self.stmt = None
        self.committed = False

    async def execute(self, stmt):
        self.stmt = stmt
        return _FakeResult()

    async def commit(self):
        self.committed = True


def _clear(robot_id=None):
    from app.fsm_audit import clear_transitions

    db = _FakeDb()
    removed = asyncio.run(clear_transitions(db, robot_id=robot_id))
    return db, removed


def test_clearing_one_robot_is_scoped_to_that_robot():
    db, removed = _clear("Pinky-3")
    sql = str(db.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "WHERE" in sql.upper()
    assert "Pinky-3" in sql
    assert removed == 3


def test_clearing_without_a_robot_deletes_everything():
    """전체 삭제는 **명시적으로 robot_id 를 안 줬을 때만** 일어나야 한다."""
    db, _ = _clear(None)
    assert "WHERE" not in str(db.stmt).upper()


def test_empty_robot_id_does_not_silently_wipe_all():
    """빈 문자열은 '전체'가 아니라 '지정 안 함'이다 — 둘을 헷갈리면 전량 삭제가 된다.

    지금은 falsy 라 전체 삭제로 떨어진다. 라우터가 빈 값을 None 으로 정규화하므로
    실제 경로에서는 도달하지 않지만, 이 함수를 직접 부르는 코드가 생기면 위험하다.
    """
    db, _ = _clear("")
    assert "WHERE" not in str(db.stmt).upper()


def test_delete_is_committed():
    """커밋을 빠뜨리면 화면은 비워졌다가 새로고침하면 되살아난다."""
    db, _ = _clear("Pinky-3")
    assert db.committed is True
