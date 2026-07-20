import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import SecurityPatrolNavigation
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.set_next_mode import SetNextMode
from libi_modes.common.watchdog import exit_watchdog

# Night operation: no task assignment, no visitor touch — only an operator stop.
_COMMAND_MAP = {"stop_request": "IDLE"}


def create(params: dict, driver) -> py_trees.behaviour.Behaviour:
    """SecurityPatrolBranch — one intrusion-detection lap, then back to IDLE.

    Same skeleton as PATROL with two differences: the nav leaf finishes after one lap
    (so SetNextMode sends it home), and the command map is deliberately narrow so a task
    assignment or a panel touch cannot pull the robot off night duty.
    """
    low = params["battery"]["low"]
    return py_trees.composites.Sequence(
        name="SecurityPatrolBranch",
        memory=False,
        children=[
            IsMode("SECURITY_PATROL"),
            py_trees.composites.Parallel(
                name="SecurityPatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Sequence(
                        name="OnePatrolLoop",
                        memory=True,
                        children=[
                            SecurityPatrolNavigation(driver),
                            SetNextMode("IDLE"),
                        ],
                    ),
                    exit_watchdog("SecurityPatrolExitConditions", [
                        FaultDetected(),
                        BatteryCheck("<=", low, "RETURNING"),
                        CommandListener(_COMMAND_MAP),
                    ]),
                ],
            ),
            RequestTransition(),
        ],
    )
