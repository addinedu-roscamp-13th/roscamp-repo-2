import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class ReturnNavigation(py_trees.behaviour.Behaviour):
    """Arm to home, then drive to the charger and dock, retrying up to `retry_max` times.

    The arm goes home BEFORE the base moves. Right after boot the arm's pose is unknown,
    and driving with it extended risks hitting a shelf.

    On exhausting retries this raises blackboard.fault and returns RUNNING — deliberately
    NOT FAILURE. This leaf runs inside a Parallel, and a py_trees Parallel aborts the
    moment any child fails, which would tear the branch down before the sibling
    FaultDetected could turn that fault into a transition. Staying RUNNING lets the
    watchdog observe the fault on this same tick and route to ERROR through the ordinary
    path, so "every branch owns its own fault check" holds with no special-case wiring.

    `dock_driver.poll() == "success"` only means the dock command was accepted and
    dispatched — `robot_agent.core.ros_bridge.send_nav_goal()` is documented "완료 대기
    없이" (fire-and-forget, no wait for arrival). It says nothing about whether the robot
    physically reached and docked at the charger. So SUCCESS here additionally requires
    `blackboard.is_docked` to be true — a real dock-confirmation signal (ArUco marker,
    line-dock contact switch, whatever the docking hardware ends up reporting).

    Today nothing publishes `is_docked` yet, so this gate never opens and RETURNING never
    auto-advances to CHARGING — that is intentional until a real confirmation signal
    exists, rather than trusting "command accepted" as if it meant "arrived".
    """

    def __init__(self, arm_driver, dock_driver, retry_max: int, name: str | None = None):
        super().__init__(name=name or "ReturnNavigation")
        self.arm_driver = arm_driver
        self.dock_driver = dock_driver
        self.retry_max = retry_max
        self._homed = False
        self._dock_started = False

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.DOCK_RETRY_COUNT, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.FAULT, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.IS_DOCKED, access=Access.READ)

    def initialise(self):
        if not self._homed:
            self.arm_driver.go_home()
            self._homed = True

    def update(self) -> Status:
        if not self._dock_started:
            self.dock_driver.start()
            self._dock_started = True
        result = self.dock_driver.poll()
        if result == "success":
            if not bb.get(self.blackboard, Keys.IS_DOCKED):
                return Status.RUNNING   # command accepted; still waiting on real confirmation
            return Status.SUCCESS
        if result == "failure":
            retries = bb.get(self.blackboard, Keys.DOCK_RETRY_COUNT, default=0) + 1
            self.blackboard.set(Keys.DOCK_RETRY_COUNT, retries)
            self._dock_started = False
            if retries >= self.retry_max:
                self.blackboard.set(Keys.FAULT, True)
                return Status.RUNNING     # fault raised; the watchdog takes it from here
            return Status.RUNNING         # retry — next tick starts the dock attempt again
        return Status.RUNNING

    def terminate(self, new_status):
        if new_status == Status.INVALID:
            self.dock_driver.stop()
        self._homed = False
        self._dock_started = False
