"""fsm_model 은 전이 박스(INSTRUCTION.md 1단계)의 유일한 진실 원천이다.

이 테스트들이 전이 박스 원문과 코드가 어긋나는 것을 막는다.
"""
from app import fsm_model

# libi_modes.registry.BRANCH_ORDER 와 반드시 같아야 한다.
EXPECTED_ORDER = [
    "ERROR", "RETURNING", "CHARGING", "WORKING",
    "INTERACTING", "SECURITY_PATROL", "PATROL", "IDLE",
]


def test_states_match_branch_order_exactly():
    assert list(fsm_model.STATES) == EXPECTED_ORDER


def test_transition_box_edges_are_complete():
    """전이 박스 18개 간선(그룹 전이 확장 후 20개)이 하나도 빠지거나 더해지지 않았는지."""
    pairs = {(e.source, e.target) for e in fsm_model.EDGES}
    expected = {
        (fsm_model.START, "RETURNING"),
        ("RETURNING", "CHARGING"),
        ("CHARGING", "IDLE"),
        ("IDLE", "PATROL"),
        ("IDLE", "WORKING"),
        ("IDLE", "SECURITY_PATROL"),
        ("PATROL", "WORKING"),
        ("PATROL", "INTERACTING"),
        ("PATROL", "IDLE"),
        ("INTERACTING", "PATROL"),
        ("INTERACTING", "WORKING"),
        ("INTERACTING", "IDLE"),
        ("WORKING", "PATROL"),
        ("WORKING", "IDLE"),
        ("SECURITY_PATROL", "IDLE"),
        ("IDLE", "RETURNING"),
        ("PATROL", "RETURNING"),
        ("SECURITY_PATROL", "RETURNING"),
        (fsm_model.ANY, "ERROR"),
        ("ERROR", "IDLE"),
    }
    assert pairs == expected
    assert len(fsm_model.EDGES) == 20


def test_deliberately_absent_edges_stay_absent():
    """INSTRUCTION.md '의도적으로 두지 않은 간선' — 실수로 생기면 잡는다."""
    pairs = {(e.source, e.target) for e in fsm_model.EDGES}
    for forbidden in [
        ("PATROL", "SECURITY_PATROL"),
        ("SECURITY_PATROL", "PATROL"),
        ("CHARGING", "PATROL"),
        ("CHARGING", "WORKING"),
        ("WORKING", "RETURNING"),
        ("INTERACTING", "RETURNING"),
        ("RETURNING", "IDLE"),
    ]:
        assert forbidden not in pairs, f"{forbidden} 은 의도적으로 없어야 하는 간선"


def test_boot_edge_does_not_make_returning_universally_reachable():
    """registry.py 는 boot 도 "*" 로 적지만 그건 시작 의사상태다.

    '모든 상태'로 해석하면 WORKING/INTERACTING 에서도 RETURNING 이 열려버려
    '작업/응대 중에는 복귀하지 않는다' 규칙이 조용히 깨진다.
    """
    assert "RETURNING" not in fsm_model.allowed_targets("WORKING")
    assert "RETURNING" not in fsm_model.allowed_targets("INTERACTING")
    assert "RETURNING" not in fsm_model.allowed_targets("CHARGING")


def test_allowed_targets_from_charging_is_idle_and_error_only():
    """INSTRUCTION.md 예시: '현재 CHARGING 이면 IDLE 만 활성화'.
    ERROR 는 (any)->ERROR 그룹 간선으로 항상 도달 가능하다."""
    assert fsm_model.allowed_targets("CHARGING") == ["ERROR", "IDLE"]


def test_allowed_targets_from_idle():
    assert fsm_model.allowed_targets("IDLE") == [
        "ERROR", "RETURNING", "WORKING", "SECURITY_PATROL", "PATROL",
    ]


def test_allowed_targets_from_returning_has_no_command_exit():
    """RETURNING 은 docked(->CHARGING) 와 fault(->ERROR) 로만 나간다."""
    assert fsm_model.allowed_targets("RETURNING") == ["ERROR", "CHARGING"]


def test_allowed_targets_for_unknown_state_is_empty():
    assert fsm_model.allowed_targets("BOGUS") == []


def test_validate_accepts_a_valid_edge():
    accepted, reason = fsm_model.validate("CHARGING", "IDLE", force=False, error_code=None)
    assert accepted is True
    assert reason == ""


def test_validate_rejects_an_invalid_edge_without_force():
    accepted, reason = fsm_model.validate("CHARGING", "WORKING", force=False, error_code=None)
    assert accepted is False
    assert "CHARGING" in reason and "WORKING" in reason


def test_validate_allows_an_invalid_edge_with_force():
    accepted, reason = fsm_model.validate("CHARGING", "WORKING", force=True, error_code=None)
    assert accepted is True
    assert "강제" in reason


def test_error_is_always_enterable_even_without_an_edge():
    """안전 규칙: 'ERROR 진입 — 언제든 허용한다 (비상 수단)'.

    ERROR 자기 자신은 제외한다 — 같은 상태로의 전이는 아래에서 따로 막는다.
    """
    for state in fsm_model.STATES:
        if state == "ERROR":
            continue
        accepted, _ = fsm_model.validate(state, "ERROR", force=False, error_code=None)
        assert accepted is True, f"{state} -> ERROR 는 항상 허용되어야 한다"


def test_self_transition_is_rejected():
    """이미 그 상태인데 또 전이하면 BT 가 같은 브랜치를 재진입해 부작용이 난다."""
    accepted, reason = fsm_model.validate("ERROR", "ERROR", force=True, error_code="E_X")
    assert accepted is False
    assert "이미" in reason


def test_leaving_error_requires_an_error_code():
    """안전 규칙: 'ERROR 이탈 — error_code 확인 후에만 허용한다'."""
    accepted, reason = fsm_model.validate("ERROR", "IDLE", force=False, error_code=None)
    assert accepted is False
    assert "error_code" in reason

    accepted, _ = fsm_model.validate("ERROR", "IDLE", force=False, error_code="E_DOCK_FAIL")
    assert accepted is True


def test_leaving_error_without_error_code_is_rejected_by_default():
    """force 없이는 원인 미상 상태에서 자율 주행을 재개시킬 수 없다."""
    accepted, reason = fsm_model.validate("ERROR", "PATROL", force=False, error_code=None)
    assert accepted is False
    assert "error_code" in reason


def test_force_overrides_the_error_code_safety_rule():
    """요청: "강제전이는 모든 걸 가능하게" — force 는 간선 제약뿐 아니라 ERROR 이탈의
    error_code 확인도 우회한다. 대신 그 사실이 reason 에 남아 감사 로그에서 추적된다."""
    accepted, reason = fsm_model.validate("ERROR", "PATROL", force=True, error_code=None)
    assert accepted is True
    assert "error_code" in reason


def test_unknown_state_is_rejected():
    accepted, reason = fsm_model.validate("IDLE", "NOPE", force=True, error_code=None)
    assert accepted is False
    assert "NOPE" in reason


def test_mermaid_source_contains_every_edge():
    src = fsm_model.to_mermaid()
    assert src.startswith("stateDiagram-v2")
    assert "[*] --> RETURNING" in src
    for edge in fsm_model.EDGES:
        if edge.source == fsm_model.START:
            continue
        source = "AnyState" if edge.source == fsm_model.ANY else edge.source
        assert f"{source} --> {edge.target}" in src


def test_guard_is_extracted_for_display():
    charging_to_idle = next(
        e for e in fsm_model.EDGES if e.source == "CHARGING" and e.target == "IDLE"
    )
    assert charging_to_idle.guard == "battery >= 40%"
    docked = next(e for e in fsm_model.EDGES if e.trigger == "docked")
    assert docked.guard == ""


def test_as_dict_carries_everything_the_frontend_needs():
    payload = fsm_model.as_dict()
    assert payload["states"] == list(fsm_model.STATES)
    assert len(payload["edges"]) == len(fsm_model.EDGES)
    assert payload["allowed_targets"]["CHARGING"] == ["ERROR", "IDLE"]
    assert payload["mermaid"].startswith("stateDiagram-v2")
    assert set(payload["descriptions"]) == set(fsm_model.STATES)
