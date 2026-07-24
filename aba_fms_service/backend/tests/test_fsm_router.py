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
#
# FSM 경로의 정본은 **로봇이 스스로 발행한 robot_id** 다. fsm_link 가 그 문자열을
# 그대로 캐시 키로 쓰고, 로봇 쪽(state_io.py)도 전이 요청을 그 문자열과 정확히 비교해
# 거른다. 그래서 resolve_robot_id 는 캐시에 실제로 있는 키를 돌려줘야 한다.
#
# 예전엔 여기서 브릿지 키(Pinky-3 → pinky3)로 바꿨는데, 그건 토픽 접두사를 쓰는
# 텔레메트리 경로의 규칙이라 FSM 캐시(Pinky-3)와 어긋났다. 화면은 늘 "수신 대기" 였고
# 전이 요청은 로봇이 이름 불일치로 버렸다. 아래 테스트가 그 회귀를 막는다.

def test_exact_id_from_cache_wins():
    """로봇이 발행한 이름 그대로 들어오면 그대로 나간다."""
    assert resolve_robot_id("Pinky-3", known=["Pinky-3", "Pinkysim"]) == "Pinky-3"
    assert resolve_robot_id("Pinkysim", known=["Pinky-3", "Pinkysim"]) == "Pinkysim"


def test_notation_difference_resolves_to_cached_id():
    """하이픈·대소문자 차이는 흡수하되, 돌려주는 값은 **캐시의 원본 키**다.
    브릿지 키를 돌려주면 로봇이 전이 요청을 버린다."""
    assert resolve_robot_id("pinky3", known=["Pinky-3"]) == "Pinky-3"
    assert resolve_robot_id("PINKY_3", known=["Pinky-3"]) == "Pinky-3"
    assert resolve_robot_id("PinkySim", known=["Pinkysim"]) == "Pinkysim"


def test_never_returns_bridge_key():
    """회귀 방지 — 브릿지 키로 내려보내면 fsm_link.snapshot 이 영원히 None 이다."""
    from app.fleet_telemetry import bridge_key

    assert bridge_key("Pinky-3") == "pinky3"  # 텔레메트리 규칙 자체는 그대로다
    assert resolve_robot_id("Pinky-3", known=["Pinky-3"]) != bridge_key("Pinky-3")


def test_unknown_identifier_passes_through_unchanged():
    """캐시에 없는 이름을 조용히 버리거나 변형하면 '왜 안 보이지'가 된다.
    그대로 흘려보내고 화면에 '수신 대기'로 드러나게 한다."""
    assert resolve_robot_id("42", known=["Pinky-3"]) == "42"
    assert resolve_robot_id("Unregistered", known=["Pinky-3"]) == "Unregistered"
    assert resolve_robot_id("Pinky-4", known=[]) == "Pinky-4"


def test_empty_identifier_is_not_matched():
    assert resolve_robot_id("", known=["Pinky-3"]) == ""
