import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class IsMode(py_trees.behaviour.Behaviour):
    """SUCCESS iff blackboard.current_mode == self.mode.

    Guard leaf — the first child of every branch, so exactly one branch body runs per tick.

    [디버그] blackboard.disabled_branches 에 self.mode 가 들어 있으면 (current_mode 가
    맞더라도) FAILURE 를 반환한다 — 그 브랜치를 세션 동안 "잠가" 실행 안 되게 한다.
    Selector 는 그 브랜치를 건너뛰고, 잠긴 게 현재 상태면 NoBranchMatched(Running) 로
    떨어져 트리는 살아있고 로봇은 그 상태에서 정지(안전 프리즈)한다. 기본값(빈 집합)이면
    아무 변화 없다.
    """

    def __init__(self, mode: str, name: str | None = None):
        super().__init__(name=name or f"IsMode[{mode}]")
        self.mode = mode

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.READ)
        self.blackboard.register_key(key=Keys.DISABLED_BRANCHES, access=Access.READ)

    def update(self) -> Status:
        if self.mode in bb.get(self.blackboard, Keys.DISABLED_BRANCHES, default=()):
            return Status.FAILURE
        return Status.SUCCESS if bb.get(self.blackboard, Keys.CURRENT_MODE) == self.mode else Status.FAILURE
