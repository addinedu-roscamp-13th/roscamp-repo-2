"""state_io.validate() — 순수 함수라 rclpy 없이 바로 테스트한다.

fsm_model.py(FMS 패널 쪽)의 같은 이름 함수와 판정이 어긋나면 안 된다 — 패널은
승인이라 보여주는데 로봇은 거부하는 상황이 생긴다. 두 파일을 나란히 고칠 때마다
이 파일도 같이 갱신한다.
"""
from libi_modes.ros.state_io import validate


def test_leaving_error_without_error_code_is_rejected_by_default():
    accepted, reason = validate("ERROR", "IDLE", force=False, error_code="")
    assert accepted is False
    assert "error_code" in reason


def test_force_overrides_the_error_code_safety_rule():
    """요청: "강제전이는 모든 걸 가능하게" — force 는 ERROR 이탈의 error_code
    확인도 우회한다. 우회 사실은 reason 에 남아 감사 로그에서 추적된다."""
    accepted, reason = validate("ERROR", "IDLE", force=True, error_code="")
    assert accepted is True
    assert "error_code" in reason


def test_force_overrides_the_error_code_rule_even_off_the_transition_box():
    """전이 박스에 없는 간선(ERROR -> WORKING) + error_code 없음, 둘 다 force 로 뚫린다."""
    accepted, reason = validate("ERROR", "WORKING", force=True, error_code="")
    assert accepted is True
    assert "error_code" in reason


def test_error_entry_is_always_allowed_regardless_of_error_code():
    accepted, reason = validate("PATROL", "ERROR", force=False, error_code="")
    assert accepted is True
    assert reason == ""


def test_leaving_error_with_error_code_needs_no_force():
    accepted, reason = validate("ERROR", "IDLE", force=False, error_code="E42")
    assert accepted is True
    assert reason == ""


def test_same_state_is_rejected():
    accepted, reason = validate("IDLE", "IDLE", force=True, error_code="")
    assert accepted is False


def test_unknown_target_is_rejected():
    accepted, reason = validate("IDLE", "NOPE", force=True, error_code="")
    assert accepted is False
    assert "NOPE" in reason
