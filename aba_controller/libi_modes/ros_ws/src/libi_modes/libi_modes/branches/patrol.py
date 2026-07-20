import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import PatrolNavigation
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.watchdog import exit_watchdog

_COMMAND_MAP = {
    "task_assigned": "WORKING",
    "ui_touch": "INTERACTING",
    "stop_request": "IDLE",
}


def create(params: dict, driver) -> py_trees.behaviour.Behaviour:
    """PatrolBranch — roam the library waiting for work.

    SuccessOnOne means the moment the watchdog Selector decides on an exit, the Parallel
    returns and PatrolNavigation is terminated (motors stopped) before the transition.
    """
    low = params["battery"]["low"]
    return py_trees.composites.Sequence(
        name="PatrolBranch",
        memory=False,
        children=[
            IsMode("PATROL"),
            py_trees.composites.Parallel(
                name="PatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    PatrolNavigation(driver),
                    exit_watchdog("PatrolExitConditions", [
                        FaultDetected(),
                        BatteryCheck("<=", low, "RETURNING"),
                        CommandListener(_COMMAND_MAP),
                    ]),
                ],
            ),
            RequestTransition(),
        ],
    )
