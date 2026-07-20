import py_trees

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition

_COMMAND_MAP = {"recovered": "IDLE"}


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """ErrorBranch — stop and wait for an operator.

    The only branch WITHOUT FaultDetected: it is already ERROR, so a self-transition
    would be pointless.

    There is deliberately no battery path and no drive node. A robot whose fault cause is
    unknown must not resume autonomous motion, even to save itself from a flat battery —
    manual recovery goes through teleop, outside this tree.
    """
    return py_trees.composites.Sequence(
        name="ErrorBranch",
        memory=False,
        children=[
            IsMode("ERROR"),
            CommandListener(_COMMAND_MAP),
            RequestTransition(),
        ],
    )
