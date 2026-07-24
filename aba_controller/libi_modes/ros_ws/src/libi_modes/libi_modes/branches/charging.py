import py_trees

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """ChargingBranch — dock and charge until BATTERY_READY.

    Below the threshold with no fault the Selector fails, so the branch fails and nothing
    happens. That failure IS the waiting behaviour — no explicit wait node is needed.
    """
    ready = params["battery"]["ready"]
    return py_trees.composites.Sequence(
        name="ChargingBranch",
        memory=False,
        children=[
            IsMode("CHARGING"),
            py_trees.composites.Selector(
                name="ChargingExitConditions",
                memory=False,
                children=[
                    FaultDetected(),
                    BatteryCheck(">=", ready, "IDLE"),
                ],
            ),
            RequestTransition(),
        ],
    )
