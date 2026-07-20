import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class CommandTimeout(py_trees.behaviour.Behaviour):
    """SUCCESS + next_mode=ERROR when no command has arrived for `timeout_sec` and nothing
    is currently executing.

    This is WORKING's only escape hatch. That branch has no battery exit either, so if the
    task adapter dies or the link drops, without this the robot waits in AwaitingCommand
    forever and cannot be recovered without a manual reset.

    The window runs from the last sign of activity — a command executing, a command
    timestamp from the adapter, or failing both, the first tick this leaf ever ran.

    It deliberately does NOT anchor on command_received_at alone: that key reads 0.0 until
    the adapter first writes it, while a monotonic clock reads system uptime, so the
    difference always exceeds any sane timeout and a freshly-assigned robot would be sent
    straight to ERROR on its first tick in WORKING.

    The anchor also cannot live in initialise(). This leaf returns FAILURE on the normal
    path (no timeout yet), so py_trees treats it as finished every tick and re-runs
    initialise() on the next one — an anchor set there resets forever and never fires.
    """

    def __init__(self, timeout_sec: float, clock=time.monotonic, name: str | None = None):
        super().__init__(name=name or f"CommandTimeout[{timeout_sec}s]")
        self.timeout_sec = timeout_sec
        self.clock = clock
        self._last_activity_at = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.COMMAND_RECEIVED_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        now = self.clock()
        if self._last_activity_at is None:
            self._last_activity_at = now

        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND):
            self._last_activity_at = now      # work in progress — window restarts
            return Status.FAILURE

        received_at = bb.get(self.blackboard, Keys.COMMAND_RECEIVED_AT, default=0.0) or 0.0
        since = max(self._last_activity_at, received_at)
        if now - since >= self.timeout_sec:
            self.blackboard.set(Keys.NEXT_MODE, "ERROR")
            return Status.SUCCESS
        return Status.FAILURE
