"""회복 BT 서브트리가 **지금 도는 실행 leaf 밑에** 붙는지.

왜 있나: 회복 BT 는 추종과 길잡이 양쪽에서 돈다(길잡이도 사람을 놓치면 반대 캠으로
찾는다). 그런데 `CommandDispatch` 는 memory=False Selector 라 길잡이 중에는
`GuideExec` 이 tick 을 집어가고 `FollowExec` 은 한 번도 안 돌아 INVALID 로 남는다.

접합점을 `FollowExec` 으로 고정하면 화면에 **꺼진 노드 밑에서 무언가 돌고 있는**
그림이 나온다 — 코드는 멀쩡한데 화면만 거짓이 되는 상황. 실측 2026-07-28.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_trees.common import Status  # noqa: E402

from libi_modes.ros.state_io import _to_dict  # noqa: E402

SUB = {"name": "BT_Searching", "kind": "Selector", "status": "RUNNING", "children": []}


class _Leaf:
    def __init__(self, name, status=Status.INVALID):
        self.name = name
        self.status = status
        self.children = []


class _Composite:
    """`_kind` 가 클래스명을 보므로 Selector 로 위장한다."""

    def __init__(self, name, children, status=Status.RUNNING):
        self.name = name
        self.status = status
        self.children = children
        self.memory = False


def _tree(follow_status, guide_status):
    return _Composite("CommandDispatch", [
        _Leaf("NavigationExec"),
        _Leaf("GuideExec", guide_status),
        _Leaf("ArmExec"),
        _Leaf("FollowExec", follow_status),
    ])


def _child_names(d, target):
    """`target` 이름 노드의 자식 이름들."""
    if d["name"] == target:
        return [c["name"] for c in d["children"]]
    for c in d["children"]:
        got = _child_names(c, target)
        if got is not None:
            return got
    return None


def test_grafts_under_follow_when_following():
    out = _to_dict(_tree(Status.RUNNING, Status.INVALID), SUB)
    assert _child_names(out, "FollowExec") == ["BT_Searching"]
    assert _child_names(out, "GuideExec") == []


def test_grafts_under_guide_when_guiding():
    """이게 이 파일의 존재 이유다 — 예전엔 FollowExec 에 붙었다."""
    out = _to_dict(_tree(Status.INVALID, Status.RUNNING), SUB)
    assert _child_names(out, "GuideExec") == ["BT_Searching"]
    assert _child_names(out, "FollowExec") == []


def test_never_grafts_twice():
    """둘 다 RUNNING 인 이상 상황에서도 한 군데만. 두 번 그리면 같은 트리가 겹쳐 보인다."""
    out = _to_dict(_tree(Status.RUNNING, Status.RUNNING), SUB)
    grafted = [n for n in ("FollowExec", "GuideExec") if _child_names(out, n)]
    assert len(grafted) == 1


def test_falls_back_when_neither_runs():
    """붙일 데가 없다고 **버리면** 안 된다 — 돌고 있는데 화면에서 사라진다."""
    out = _to_dict(_tree(Status.INVALID, Status.INVALID), SUB)
    assert _child_names(out, "FollowExec") == ["BT_Searching"]


def test_no_subtree_leaves_leaves_empty():
    out = _to_dict(_tree(Status.RUNNING, Status.INVALID), None)
    assert _child_names(out, "FollowExec") == []


def test_existing_children_are_not_replaced():
    """접합은 leaf 에만 한다. 자식이 있는 노드를 덮으면 진짜 트리를 지우는 것이다."""
    node = _Composite("FollowExec", [_Leaf("Real")], status=Status.RUNNING)
    out = _to_dict(_Composite("Root", [node]), SUB)
    assert _child_names(out, "FollowExec") == ["Real"]
