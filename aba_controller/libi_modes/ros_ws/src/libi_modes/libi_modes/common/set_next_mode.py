import py_trees
from py_trees.common import Access, Status

from libi_modes.blackboard import Keys


class SetNextMode(py_trees.behaviour.Behaviour):
    """Unconditionally SUCCESS, writing a fixed next_mode.

    Used after a self-completing action (one security patrol loop, a finished dock) where
    the transition follows from the action finishing rather than from an external condition.
    """

    def __init__(self, next_mode: str, name: str | None = None):
        super().__init__(name=name or f"SetNextMode[{next_mode}]")
        self.next_mode = next_mode

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        self.blackboard.set(Keys.NEXT_MODE, self.next_mode)
        return Status.SUCCESS
