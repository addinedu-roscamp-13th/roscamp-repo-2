"""`app/fsm_model.py` 가 `libi_modes/registry.py` 와 어긋나지 않았는지 감시한다.

INSTRUCTION.md 4단계: "registry.py 의 매핑은 2단계 웹 시각화에서 '상태 -> 표시할 서브트리'로
그대로 재사용한다. **별도 매핑 테이블을 만들지 않는다.**"

registry.py 를 import 하지 않고 `ast` 로 파싱한다:
  - registry.py 최상위가 `libi_modes.branches` 를 import 하므로 import 하면 py_trees 가
    딸려온다. FMS 백엔드는 py_trees 를 의존성으로 갖지 않는다.
  - 파싱은 실행이 아니므로 별도 워크스페이스를 sys.path 에 끼울 필요도 없다.

registry.py 가 없는 환경(FMS 만 따로 배포)에서는 skip 한다 — 그 경우 검증은 개발 머신과
CI 의 몫이다.
"""
import ast
from pathlib import Path

import pytest

from app import fsm_model

REGISTRY = (
    Path(__file__).resolve().parents[3]
    / "aba_controller/libi_modes/ros_ws/src/libi_modes/libi_modes/registry.py"
)


def _registry_literal(name: str):
    """registry.py 의 최상위 상수를 실행 없이 읽는다."""
    if not REGISTRY.exists():
        pytest.skip(f"libi_modes registry.py 없음: {REGISTRY}")
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    pytest.fail(f"registry.py 에 {name} 이 없습니다")


def test_registry_file_is_where_we_think_it_is():
    """경로가 틀리면 위 테스트들이 조용히 skip 되어 감시가 무력화된다."""
    assert REGISTRY.exists(), (
        f"registry.py 를 {REGISTRY} 에서 찾지 못했습니다. "
        "libi_modes 가 이동했다면 이 경로를 함께 고치세요."
    )


def test_states_match_registry_branch_order():
    """상태 목록과 순서가 registry.BRANCH_ORDER 와 완전히 같아야 한다."""
    branch_order = _registry_literal("BRANCH_ORDER")
    assert list(fsm_model.STATES) == list(branch_order)


def test_every_edge_matches_registry_transitions():
    """간선 집합 전체를 글자 단위로 비교한다.

    registry 는 두 의사상태를 서로 다른 마커로 구분한다:
      ("[*]",   "RETURNING", "boot")  -> 시작 의사상태
      ("(any)", "ERROR",     "fault") -> 진짜 그룹 전이
    fsm_model 은 같은 것을 START / ANY 라는 자기 값으로 담으므로, 비교 전에 되돌린다.
    """
    registry_edges = {tuple(t) for t in _registry_literal("TRANSITIONS")}

    to_registry = {
        fsm_model.START: fsm_model._REGISTRY_START,
        fsm_model.ANY: fsm_model._REGISTRY_ANY,
    }
    ours = {
        (to_registry.get(edge.source, edge.source), edge.target, edge.trigger)
        for edge in fsm_model.EDGES
    }

    missing = registry_edges - ours
    extra = ours - registry_edges
    assert not missing, f"registry 에만 있는 간선(fsm_model 에 추가 필요): {sorted(missing)}"
    assert not extra, f"fsm_model 에만 있는 간선(registry 와 불일치): {sorted(extra)}"


def test_edge_count_matches_registry():
    assert len(fsm_model.EDGES) == len(_registry_literal("TRANSITIONS"))


def test_pseudo_state_markers_stay_distinct():
    """registry 가 시작 의사상태와 그룹 전이를 계속 다른 마커로 구분하는지 고정한다.

    둘을 하나의 와일드카드로 합치고 그걸 '모든 상태에서'로 확장하면
    WORKING -> RETURNING, INTERACTING -> RETURNING 이 열린다 — 설계가 의도적으로
    뺀 두 간선이라, 작업 중인 로봇과 응대 중인 로봇이 배터리 때문에 이탈하게 된다.
    """
    transitions = _registry_literal("TRANSITIONS")
    assert fsm_model._REGISTRY_START != fsm_model._REGISTRY_ANY

    start_edges = [tuple(t) for t in transitions if t[0] == fsm_model._REGISTRY_START]
    any_edges = [tuple(t) for t in transitions if t[0] == fsm_model._REGISTRY_ANY]
    assert start_edges == [(fsm_model._REGISTRY_START, "RETURNING", "boot")], start_edges
    assert any_edges == [(fsm_model._REGISTRY_ANY, "ERROR", "fault")], any_edges

    pseudo = {fsm_model._REGISTRY_START, fsm_model._REGISTRY_ANY}
    leftover = {t[0] for t in transitions if t[0] not in pseudo} - set(fsm_model.STATES)
    assert not leftover, (
        f"registry 에 정체불명의 의사상태가 생겼습니다: {sorted(leftover)}. "
        "시작 의사상태인지 진짜 그룹 전이인지 판단해 fsm_model 을 함께 고치세요."
    )


def test_naive_wildcard_expansion_cannot_open_forbidden_returns():
    """registry 를 읽어 상태도를 그릴 때, 그룹 전이를 전개해도 배터리 예외가 유지되는지."""
    transitions = _registry_literal("TRANSITIONS")
    states = set(fsm_model.STATES)

    edges = set()
    for source, target, _ in transitions:
        if source == fsm_model._REGISTRY_ANY:
            edges.update((s, target) for s in states)
        elif source == fsm_model._REGISTRY_START:
            continue
        else:
            edges.add((source, target))

    for forbidden in (("WORKING", "RETURNING"), ("INTERACTING", "RETURNING")):
        assert forbidden not in edges, f"{forbidden[0]} 에 복귀 간선이 생겼습니다"
