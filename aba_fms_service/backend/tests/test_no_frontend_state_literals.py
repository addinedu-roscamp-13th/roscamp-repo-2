"""프론트엔드에 상태 이름이 하드코딩되지 않았는지 감시한다.

INSTRUCTION.md: "상태 다이어그램은 1단계 전이 박스를 기준으로 생성하며, 전이 박스와
화면이 어긋나지 않도록 한 곳에서 정의를 읽어 렌더링한다."

상태 목록을 `.tsx` 에 복사해두면 전이 박스만 바뀌었을 때 화면이 조용히 옛 정의로 남는다.
전부 `GET /api/fsm/model` 응답에서 읽어야 한다.
"""
from pathlib import Path

import pytest

from app.fsm_model import STATES

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

WATCHED = [
    "components/admin/FsmBtPanel.tsx",
    "components/admin/FsmStateDiagram.tsx",
    "components/admin/BtTreeView.tsx",
    "routes/admin/_authed/fsm.tsx",
    "lib/admin-api.ts",
]


def test_frontend_directory_is_where_we_think_it_is():
    """경로가 틀리면 아래 테스트들이 전부 skip 되어 감시가 무력화된다."""
    assert FRONTEND.is_dir(), f"프론트엔드 소스를 {FRONTEND} 에서 찾지 못했습니다."


@pytest.mark.parametrize("relative", WATCHED)
def test_frontend_file_has_no_state_name_literals(relative):
    path = FRONTEND / relative
    if not path.exists():
        pytest.skip(f"{relative} 아직 없음")
    source = path.read_text(encoding="utf-8")
    for state in STATES:
        for quoted in (f'"{state}"', f"'{state}'", f"`{state}`"):
            assert quoted not in source, (
                f"{relative} 에 상태 이름 {quoted} 이 하드코딩되어 있습니다. "
                f"GET /api/fsm/model 응답에서 읽어 쓰세요."
            )


def test_py_trees_statuses_are_not_mistaken_for_fsm_states():
    """BtTreeView 는 SUCCESS/FAILURE/RUNNING/INVALID 를 문자열로 갖는다.

    그건 py_trees **노드 상태**이지 FSM 상태가 아니다. 지금은 이름이 겹치지 않아 위
    테스트가 통과한다. 만약 FSM 상태에 같은 이름이 생기면 이 테스트가 먼저 실패해서
    사람이 판단하도록 강제한다.
    """
    bt_statuses = {"SUCCESS", "FAILURE", "RUNNING", "INVALID"}
    collision = bt_statuses & set(STATES)
    assert not collision, (
        f"FSM 상태와 py_trees 노드 상태 이름이 겹칩니다: {collision}. "
        "겹치면 하드코딩 감시 테스트가 오탐을 내므로 감시 방식을 다시 설계해야 합니다."
    )
