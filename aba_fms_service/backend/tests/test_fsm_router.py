"""라우터의 결정 로직 테스트.

DB/ROS 를 붙이지 않고 순수 판정 헬퍼(`_decide`)와 식별자 정규화(`resolve_robot_id`)를
직접 검증한다. FastAPI 엔드포인트는 이 둘을 감싸고 감사 로그와 링크 호출만 얹기 때문에
규칙은 여기서 다 잡힌다.
"""
import pytest

fsm_router = pytest.importorskip(
    "app.routers.fsm",
    reason="라우터는 FastAPI/SQLAlchemy 가 있는 환경에서만 import 된다 (backend/.venv)",
)

_decide = fsm_router._decide
resolve_robot_id = fsm_router.resolve_robot_id


def test_valid_edge_is_dispatched():
    outcome = _decide(current="CHARGING", target="IDLE", force=False, error_code=None)
    assert outcome.accepted is True
    assert outcome.should_dispatch is True
    assert outcome.needs_task_cancel is False


def test_invalid_edge_is_rejected_before_dispatch():
    """거부는 로봇에 요청조차 보내지 않는다."""
    outcome = _decide(current="CHARGING", target="WORKING", force=False, error_code=None)
    assert outcome.accepted is False
    assert outcome.should_dispatch is False
    assert "간선" in outcome.reason


def test_forced_invalid_edge_is_dispatched():
    outcome = _decide(current="CHARGING", target="WORKING", force=True, error_code=None)
    assert outcome.accepted is True
    assert outcome.should_dispatch is True


def test_leaving_working_requires_task_cancel_report():
    """INSTRUCTION.md 안전 규칙: 'WORKING 이탈 — 진행 중인 태스크가 있으면
    FMS 에 task_cancelled 를 보고한 뒤 전이한다'."""
    outcome = _decide(current="WORKING", target="IDLE", force=False, error_code=None)
    assert outcome.accepted is True
    assert outcome.needs_task_cancel is True


def test_entering_working_does_not_need_task_cancel():
    outcome = _decide(current="IDLE", target="WORKING", force=False, error_code=None)
    assert outcome.needs_task_cancel is False


def test_error_entry_always_dispatches():
    outcome = _decide(current="WORKING", target="ERROR", force=False, error_code=None)
    assert outcome.accepted is True
    assert outcome.should_dispatch is True
    # WORKING 에서 나가므로 태스크 취소 보고는 여전히 필요하다
    assert outcome.needs_task_cancel is True


def test_rejected_transition_never_reports_task_cancel():
    """거부됐는데 태스크만 취소되면 로봇은 일하는데 FMS 는 취소로 아는 상태가 된다."""
    outcome = _decide(current="WORKING", target="CHARGING", force=False, error_code=None)
    assert outcome.accepted is False
    assert outcome.needs_task_cancel is False


def test_error_exit_without_code_is_rejected():
    outcome = _decide(current="ERROR", target="IDLE", force=False, error_code=None)
    assert outcome.accepted is False
    assert "error_code" in outcome.reason


def test_error_exit_with_code_is_allowed():
    outcome = _decide(current="ERROR", target="IDLE", force=False, error_code="E_DOCK_FAIL")
    assert outcome.accepted is True


def test_unknown_current_state_is_rejected():
    outcome = _decide(current=None, target="IDLE", force=True, error_code=None)
    assert outcome.accepted is False
    assert outcome.should_dispatch is False
    assert "링크" in outcome.reason


# ── 식별자 정규화 ─────────────────────────────────────────────────────────────

def test_robot_name_resolves_to_bridge_key():
    """UI 는 'Pinky-1' 을 갖고 있고 ROS 전송 계층은 'pinky1' 을 쓴다."""
    assert resolve_robot_id("Pinky-1") == "pinky1"
    assert resolve_robot_id("Pinky-3") == "pinky3"


def test_already_resolved_key_passes_through():
    assert resolve_robot_id("pinky1") == "pinky1"


def test_unknown_identifier_still_resolves():
    """미등록 로봇을 조용히 버리면 '왜 아무것도 안 보이지'가 된다 — 규칙을 그대로 적용해
    흘려보내고, 상태가 안 오면 '수신 대기'로 드러나게 한다."""
    assert resolve_robot_id("42") == "42"
    assert resolve_robot_id("Unregistered") == "unregistered"


def test_resolver_reuses_fleet_telemetry_rule():
    """규칙을 복사하지 않고 fleet_telemetry.bridge_key 를 그대로 쓴다."""
    from app.fleet_telemetry import bridge_key

    for name in ("Pinky-1", "Pinky-2", "Pinky-3", "PinkySim", "pinky1", "New-Robot-9"):
        assert resolve_robot_id(name) == bridge_key(name)


def test_new_robot_needs_no_code_change():
    """예전 딕셔너리 방식에서는 로봇을 등록해도 매핑을 추가하지 않으면 그 로봇만
    조용히 누락됐다. 규칙 기반이라 등록만으로 잡힌다."""
    assert resolve_robot_id("Pinky-4") == "pinky4"
    assert resolve_robot_id("PinkyLab") == "pinkyLab"
