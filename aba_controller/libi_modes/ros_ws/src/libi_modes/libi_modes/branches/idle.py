import py_trees

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition

_COMMAND_MAP = {
    "task_assigned": "WORKING",
    "security_patrol_request": "SECURITY_PATROL",
    "resume_request": "PATROL",
}


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """IdleBranch — waiting, either docked after charging or stopped by an operator.

    Both dock guards are load-bearing, because IDLE is reached two different ways:
      - from CHARGING: docked, battery rising  -> the >=charged check may fire
      - from stop_request: undocked, battery falling -> only resume_request gets it out

    Without require_docked=True on the charged check, a stopped robot could never reach
    80% and would sit in IDLE forever. Without require_docked=False on the low check, a
    docked robot would try to "return" to the charger it is already sitting on.
    """
    low = params["battery"]["low"]
    charged = params["battery"]["charged"]
    return py_trees.composites.Sequence(
        name="IdleBranch",
        memory=False,
        children=[
            IsMode("IDLE"),
            py_trees.composites.Selector(
                name="IdleExitConditions",
                memory=False,
                children=[
                    FaultDetected(),
                    BatteryCheck("<=", low, "RETURNING", require_docked=False),
                    CommandListener(_COMMAND_MAP),
                    BatteryCheck(">=", charged, "PATROL", require_docked=True),
                ],
            ),
            RequestTransition(),
        ],
    )
