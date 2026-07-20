import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class RequestTransition(py_trees.behaviour.Behaviour):
    """Applies blackboard.next_mode to current_mode, then clears next_mode.

    Always the LAST child of a branch's root Sequence, never inside a Parallel — inside a
    Parallel the action child can finish first and the transition never runs, leaving the
    robot repeating the same state forever.

    Clearing next_mode is load-bearing: leaving it set would re-fire the same transition
    on the following tick.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "RequestTransition")

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        target = bb.get(self.blackboard, Keys.NEXT_MODE)
        if not target:
            return Status.FAILURE
        self.blackboard.set(Keys.CURRENT_MODE, target)
        self.blackboard.set(Keys.NEXT_MODE, None)
        return Status.SUCCESS
