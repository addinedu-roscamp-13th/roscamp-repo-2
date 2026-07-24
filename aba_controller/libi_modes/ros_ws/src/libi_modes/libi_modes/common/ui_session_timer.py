import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class UiSessionTimer(py_trees.behaviour.Behaviour):
    """RUNNING while a visitor is using the touch panel; SUCCESS + next_mode=PATROL once
    `timeout_sec` have passed with no new touch.

    Also owns the interlock. Both locks are taken, not just drive: a visitor standing at
    the panel is inside the arm's working radius, so locking only the base would still let
    the arm swing into them.

    terminate() MUST release both. A missed release presents as "the state changed but the
    robot won't move", which is painful to trace back to here.
    """

    def __init__(self, timeout_sec: float, clock=time.monotonic, name: str | None = None):
        super().__init__(name=name or f"UiSessionTimer[{timeout_sec}s]")
        self.timeout_sec = timeout_sec
        self.clock = clock

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.UI_LAST_TOUCH_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.DRIVE_LOCK, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.ARM_LOCK, access=Access.WRITE)

    def initialise(self):
        self.blackboard.set(Keys.DRIVE_LOCK, True)
        self.blackboard.set(Keys.ARM_LOCK, True)

    def update(self) -> Status:
        last_touch = bb.get(self.blackboard, Keys.UI_LAST_TOUCH_AT, default=0.0)
        if self.clock() - last_touch >= self.timeout_sec:
            self.blackboard.set(Keys.NEXT_MODE, "PATROL")
            return Status.SUCCESS
        return Status.RUNNING

    def terminate(self, new_status):
        self.blackboard.set(Keys.DRIVE_LOCK, False)
        self.blackboard.set(Keys.ARM_LOCK, False)
