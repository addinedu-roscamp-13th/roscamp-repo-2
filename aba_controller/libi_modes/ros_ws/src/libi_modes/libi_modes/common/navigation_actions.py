from py_trees.common import Status

from libi_modes.common.driver_action import DriverAction


class PatrolNavigation(DriverAction):
    """Continuous library round. Never completes on its own — patrol is an endless loop,
    so a driver reporting "success" just means "lap done, keep going". It ends only when
    the sibling exit-condition Selector wins the Parallel and invalidates this leaf.
    """

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, name or "PatrolNavigation")

    def update(self) -> Status:
        result = super().update()
        return Status.RUNNING if result == Status.SUCCESS else result


class SecurityPatrolNavigation(DriverAction):
    """One night-security lap. Unlike PatrolNavigation this DOES complete — SUCCESS after
    a single lap, which the branch turns into a return to IDLE.

    Intrusion handling (record, store, notify admin — SR-19) lives inside the driver, not
    as separate leaves, so the tree stays about sequencing rather than perception detail.
    """

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, name or "SecurityPatrolNavigation")
