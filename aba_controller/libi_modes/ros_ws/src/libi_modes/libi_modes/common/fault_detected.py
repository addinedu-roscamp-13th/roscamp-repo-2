import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class FaultDetected(py_trees.behaviour.Behaviour):
    """SUCCESS + next_mode=ERROR when blackboard.fault is truthy.

    Highest-priority exit condition in every branch EXCEPT ErrorBranch — that one is
    already in ERROR, so a self-transition would be pointless.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "FaultDetected")

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.FAULT, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        if bb.get(self.blackboard, Keys.FAULT):
            self.blackboard.set(Keys.NEXT_MODE, "ERROR")
            return Status.SUCCESS
        return Status.FAILURE
