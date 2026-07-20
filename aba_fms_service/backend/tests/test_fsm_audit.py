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
