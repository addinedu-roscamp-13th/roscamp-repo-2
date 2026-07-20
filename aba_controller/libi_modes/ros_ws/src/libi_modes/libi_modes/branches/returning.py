import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.return_navigation import ReturnNavigation
from libi_modes.common.set_next_mode import SetNextMode
from libi_modes.common.watchdog import exit_watchdog


def create(params: dict, arm_driver, dock_driver) -> py_trees.behaviour.Behaviour:
    """ReturningBranch — get back to the charger. Also the first branch to run at boot,
    since startup enters RETURNING (the robot cannot know where it is or how its arm is
    posed until it has homed and docked).

    Deliberately NO CommandListener. A robot here is below 15%; honouring a stop request
    would leave it stranded away from the charger to flatten. Only docking (success) or a
    fault gets out.
    """
    retry_max = params["returning"]["dock_retry_max"]
    return py_trees.composites.Sequence(
        name="ReturningBranch",
        memory=False,
        children=[
            IsMode("RETURNING"),
            py_trees.composites.Parallel(
                name="ReturnAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Sequence(
                        name="OneReturnAttempt",
                        memory=True,
                        children=[
                            ReturnNavigation(arm_driver, dock_driver, retry_max),
                            SetNextMode("CHARGING"),
                        ],
                    ),
                    exit_watchdog("ReturningExitConditions", [FaultDetected()]),
                ],
            ),
            RequestTransition(),
        ],
    )
