import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.ui_session_timer import UiSessionTimer
from libi_modes.common.watchdog import exit_watchdog

_COMMAND_MAP = {
    "ui_close": "PATROL",
    "task_assigned": "WORKING",
    "stop_request": "IDLE",
}


def create(params: dict, clock=time.monotonic) -> py_trees.behaviour.Behaviour:
    """InteractingBranch — a visitor is using the touch panel.

    Deliberately NO BatteryCheck. Walking away mid-conversation because the battery dipped
    is worse than finishing the interaction on a low charge; INTERACTING and WORKING are
    the two branches that ignore battery entirely.
    """
    timeout = params["interacting"]["ui_idle_timeout_sec"]
    return py_trees.composites.Sequence(
        name="InteractingBranch",
        memory=False,
        children=[
            IsMode("INTERACTING"),
            py_trees.composites.Parallel(
                name="SessionAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    UiSessionTimer(timeout, clock=clock),
                    exit_watchdog("InteractingExitConditions", [
                        FaultDetected(),
                        CommandListener(_COMMAND_MAP),
                    ]),
                ],
            ),
            RequestTransition(),
        ],
    )
