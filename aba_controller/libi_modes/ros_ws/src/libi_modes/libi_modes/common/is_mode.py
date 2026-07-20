import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class IsMode(py_trees.behaviour.Behaviour):
    """SUCCESS iff blackboard.current_mode == self.mode.

    Guard leaf — the first child of every branch, so exactly one branch body runs per tick.
    """

    def __init__(self, mode: str, name: str | None = None):
        super().__init__(name=name or f"IsMode[{mode}]")
        self.mode = mode

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.READ)

    def update(self) -> Status:
        return Status.SUCCESS if bb.get(self.blackboard, Keys.CURRENT_MODE) == self.mode else Status.FAILURE
