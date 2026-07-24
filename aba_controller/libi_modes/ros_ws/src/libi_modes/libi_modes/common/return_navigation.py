import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys

#: How long to wait for `is_docked` after the dock command was accepted, before treating
#: the attempt as failed. Without this the leaf waits forever: `send_nav_goal` returns
#: "accepted" immediately, so a robot that never reaches the charger looks identical to
#: one still on its way. Timing out feeds the ordinary retry path, and exhausting retries
#: raises a fault — visibly ERROR instead of silently parked in RETURNING.
DOCK_CONFIRM_TIMEOUT_SEC = 90.0


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
    `blackboard.is_docked` to be true — a real dock-confirmation signal (ArUco marker on
    the real robot, arrival at the charger vertex in sim via `sim_dock_confirm.py`).

    Docking therefore has two independent gates, and the leaf must remember the first one
    is closed. `FleetCmdDriver.poll()` hands a result out exactly once (it pops from its
    result map), so a leaf that re-polls every tick loses the acceptance the moment it
    arrives before `is_docked` — every later poll says "running" and the branch deadlocks
    in RETURNING forever. That is not hypothetical: it is what actually happened, because
    the dock command is accepted within a second of boot while AMCL takes far longer to
    converge and publish a pose the dock confirmer can judge. So acceptance is latched in
    `_dock_accepted` and polled only until it is set.
    """

    def __init__(self, arm_driver, dock_driver, retry_max: int, name: str | None = None,
                 now_fn=time.monotonic):
        super().__init__(name=name or "ReturnNavigation")
        self.arm_driver = arm_driver
        self.dock_driver = dock_driver
        self.retry_max = retry_max
        self._now = now_fn
        self._homed = False
        self._dock_started = False
        self._dock_accepted = False
        self._accepted_at = 0.0

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

        # Gate 1 — was the dock command accepted? Ask only until it is; the answer is
        # handed out once and must not be thrown away while gate 2 is still closed.
        if not self._dock_accepted:
            result = self.dock_driver.poll()
            if result == "failure":
                return self._attempt_failed()
            if result != "success":
                return Status.RUNNING
            self._dock_accepted = True
            self._accepted_at = self._now()

        # Gate 2 — did the robot actually arrive and dock?
        if bb.get(self.blackboard, Keys.IS_DOCKED):
            return Status.SUCCESS
        if self._now() - self._accepted_at >= DOCK_CONFIRM_TIMEOUT_SEC:
            return self._attempt_failed()   # accepted but never arrived — retry, don't hang
        return Status.RUNNING

    def _attempt_failed(self) -> Status:
        """Count one failed dock attempt; raise a fault once the retries are used up.

        Always RUNNING — see the class docstring on why FAILURE would tear the branch down
        before the sibling watchdog could route the fault to ERROR.
        """
        retries = bb.get(self.blackboard, Keys.DOCK_RETRY_COUNT, default=0) + 1
        self.blackboard.set(Keys.DOCK_RETRY_COUNT, retries)
        self._dock_started = False        # next tick starts a fresh dock attempt
        self._dock_accepted = False
        if retries >= self.retry_max:
            self.blackboard.set(Keys.FAULT, True)
        return Status.RUNNING

    def terminate(self, new_status):
        if new_status == Status.INVALID:
            self.dock_driver.stop()
        self._homed = False
        self._dock_started = False
        self._dock_accepted = False
