import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class CommandListener(py_trees.behaviour.Behaviour):
    """SUCCESS + writes next_mode when blackboard.last_command is a key in `mapping`.

    Consumes (clears) last_command on a match so the same command cannot re-fire on the
    next tick. A command this branch does not map is left in place — another branch may
    be the intended recipient after the transition lands.
    """

    def __init__(self, mapping: dict, name: str | None = None):
        super().__init__(name=name or f"CommandListener{sorted(mapping.keys())}")
        self.mapping = mapping

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.LAST_COMMAND, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        cmd = bb.get(self.blackboard, Keys.LAST_COMMAND)
        if cmd not in self.mapping:
            return Status.FAILURE
        self.blackboard.set(Keys.NEXT_MODE, self.mapping[cmd])
        self.blackboard.set(Keys.LAST_COMMAND, None)
        return Status.SUCCESS
