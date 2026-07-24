import operator

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys

_OPS = {"<=": operator.le, ">=": operator.ge}


class BatteryCheck(py_trees.behaviour.Behaviour):
    """SUCCESS + writes next_mode when battery_percent {op} threshold, and — when
    require_docked is set — is_docked matches it.

    The dock guard is what keeps a stopped (undocked) robot from auto-leaving IDLE on a
    stale high battery reading, and keeps a charging (docked) robot from tripping the
    low-battery return path while it is still climbing past the threshold.
    """

    def __init__(self, op: str, threshold: float, next_mode: str,
                 require_docked: bool | None = None, name: str | None = None):
        if op not in _OPS:
            raise ValueError(f"unsupported op {op!r}, expected '<=' or '>='")
        super().__init__(name=name or f"BatteryCheck[{op}{threshold}->{next_mode}]")
        self.op = op
        self.threshold = threshold
        self.next_mode = next_mode
        self.require_docked = require_docked

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.BATTERY_PERCENT, access=Access.READ)
        self.blackboard.register_key(key=Keys.IS_DOCKED, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

    def update(self) -> Status:
        percent = bb.get(self.blackboard, Keys.BATTERY_PERCENT)
        if percent is None or not _OPS[self.op](percent, self.threshold):
            return Status.FAILURE
        if self.require_docked is not None and bb.get(self.blackboard, Keys.IS_DOCKED) != self.require_docked:
            return Status.FAILURE
        self.blackboard.set(Keys.NEXT_MODE, self.next_mode)
        return Status.SUCCESS
