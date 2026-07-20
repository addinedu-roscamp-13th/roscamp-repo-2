import py_trees
from py_trees.common import Status


class DriverAction(py_trees.behaviour.Behaviour):
    """Base for leaves that delegate motion to an injected hardware client.

    `driver` must expose:
        start()                                   begin the action
        poll() -> "running" | "success" | "failure"
        stop()                                    abort a started action

    Injecting the driver keeps every action leaf testable without ROS2, Nav2 or hardware.

    terminate() calls driver.stop() whenever the action was started and did not finish on
    its own — this is what actually halts the motors when a Parallel's watchdog child wins
    (SuccessOnOne invalidates this child, so terminate arrives with INVALID).
    """

    def __init__(self, driver, name: str):
        super().__init__(name=name)
        self.driver = driver
        self._started = False

    def initialise(self):
        self._started = False

    def update(self) -> Status:
        if not self._started:
            self.driver.start()
            self._started = True
        result = self.driver.poll()
        if result == "success":
            return Status.SUCCESS
        if result == "failure":
            return Status.FAILURE
        return Status.RUNNING

    def terminate(self, new_status):
        if self._started and new_status != Status.SUCCESS:
            self.driver.stop()
        self._started = False
