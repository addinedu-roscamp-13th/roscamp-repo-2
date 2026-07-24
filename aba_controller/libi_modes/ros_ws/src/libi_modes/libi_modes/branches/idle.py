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

    IDLE is reached two different ways:
      - from CHARGING: docked, battery rising  -> the >=charged check may fire
      - from stop_request: undocked, battery falling -> only resume_request gets it out

    The low check keeps require_docked=False so a docked robot won't try to "return" to the
    charger it is already sitting on.

    The charged (>=80% -> PATROL) check is NOT dock-gated for now. Docking isn't defined yet
    (no reliable is_docked signal), so gating on it would leave the robot stuck in IDLE and
    never auto-patrol. Trade-off: an operator-stopped, undocked robot at >=80% will now
    auto-patrol on its own.
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
                    # ponytail: dock gate dropped until docking is defined — re-add
                    # require_docked=True when a reliable is_docked signal exists.
                    BatteryCheck(">=", charged, "PATROL"),
                ],
            ),
            RequestTransition(),
        ],
    )
