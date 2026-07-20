import logging

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class CommandDrivenAction(py_trees.behaviour.Behaviour):
    """Executes one dispatched command, or fails instantly so a sibling can claim it.

    Siblings sit under a Selector, so returning FAILURE when `active_command` is not ours
    is how dispatch works — the Selector walks on to the next handler.

    Clears active_command once the command finishes so the adapter's next command can be
    picked up. Sequencing across commands belongs to the task adapter; this leaf only ever
    knows about the one command in front of it.
    """

    def __init__(self, driver, handles: set, name: str):
        super().__init__(name=name)
        self.driver = driver
        self.handles = handles
        self._started = False

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.WRITE)

    def initialise(self):
        self._started = False

    def update(self) -> Status:
        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND) not in self.handles:
            return Status.FAILURE
        if not self._started:
            self.driver.start()
            self._started = True
        result = self.driver.poll()
        if result in ("success", "failure"):
            self.blackboard.set(Keys.ACTIVE_COMMAND, None)
            self._started = False
            return Status.SUCCESS if result == "success" else Status.FAILURE
        return Status.RUNNING

    def terminate(self, new_status):
        if self._started and new_status == Status.INVALID:
            self.driver.stop()
        self._started = False


class NavigationExec(CommandDrivenAction):
    """navigate() / dock() — delegated to Nav2."""

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, handles={"navigate", "dock"}, name=name or "NavigationExec")


class ArmExec(CommandDrivenAction):
    """perform_action() — delegated to the arm.

    The grasp/place subtree that belongs inside here is not designed yet; `driver` is the
    seam it will plug into without this branch changing.
    """

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, handles={"perform_action"}, name=name or "ArmExec")


class UnwiredDriver:
    """Stand-in for a driver that was never supplied.

    Reports the command as FAILED and logs it, rather than either reporting "running"
    forever (a hung robot with no clue why) or raising (an exception here would unwind
    through the tick and out of rclpy.spin(), killing the whole mission node — taking
    PATROL, RETURNING and the ERROR handling down with one unwired command).

    Failing the command instead lets CommandDrivenAction clear active_command, the
    dispatch Selector fall through, and CommandTimeout carry the robot to ERROR through
    the ordinary path: stopped and diagnosable, not dead.

    Not raising at build time either — a robot deployed without libi_perception must
    still get a working mission tree.
    """

    def __init__(self, what: str):
        self.what = what
        self._reported = False

    def _report_once(self):
        if self._reported:
            return              # the tree ticks at 20 Hz; one line is enough
        self._reported = True
        logging.getLogger(__name__).error(
            "%s was dispatched but no driver is wired for it — failing the command. "
            "Pass one through registry.build_branches(drivers={'follow': ...}).",
            self.what,
        )

    def start(self):
        self._report_once()

    def poll(self):
        self._report_once()
        return "failure"

    def stop(self):
        pass        # tearing down something never started is a no-op, not an error


class FollowExec(CommandDrivenAction):
    """follow_admin — delegated to libi_perception.

    The follower's internals (PID tracking action, recovery BT, and the switch between
    them) live entirely in libi_perception and are opaque here. This leaf only drives the
    injected driver's start()/poll()/stop(), so the follow logic can be retuned or
    restructured without libi_modes noticing.
    """

    def __init__(self, driver=None, name: str | None = None):
        super().__init__(driver or UnwiredDriver("follow_admin"),
                         handles={"follow_admin"}, name=name or "FollowExec")
