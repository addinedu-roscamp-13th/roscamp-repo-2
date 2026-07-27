import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.command_timeout import CommandTimeout
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.watchdog import exit_watchdog
from libi_modes.common.working_actions import ArmExec, FollowExec, GuideExec, NavigationExec

_COMMAND_MAP = {
    "task_done": "PATROL",
    "task_failed": "PATROL",
    "stop_request": "IDLE",
}


def create(params: dict, nav_driver, arm_driver, follow_driver=None,
           guide_driver=None, guide_stop_driver=None,
           clock=time.monotonic) -> py_trees.behaviour.Behaviour:
    """WorkingBranch — execute whatever command the task adapter has dispatched.

    Deliberately NO BatteryCheck: the fleet manager accounts for battery when it assigns
    the task, so abandoning a book mid-delivery on a low reading would strand the payload.
    CommandTimeout is what keeps that from becoming a trap.

    Running("AwaitingCommand") must stay LAST in the dispatch Selector — it always succeeds
    at claiming the tick, so any handler after it would be unreachable.
    """
    timeout = params["working"]["command_timeout_sec"]
    arrive_tolerance = params["working"]["arrive_tolerance_m"]
    arrive_resend = params["working"]["arrive_resend_sec"]
    arrive_timeout = params["working"]["arrive_timeout_sec"]
    guide_grace = params["working"]["guide_lost_grace_sec"]
    guide_timeout = params["working"]["guide_lost_timeout_sec"]
    return py_trees.composites.Sequence(
        name="WorkingBranch",
        memory=False,
        children=[
            IsMode("WORKING"),
            py_trees.composites.Parallel(
                name="ExecuteAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Selector(
                        name="CommandDispatch",
                        memory=False,
                        children=[
                            NavigationExec(nav_driver, arrive_tolerance, arrive_resend,
                                           arrive_timeout, now_fn=clock),
                            # 길잡이. handles={"guide"} 라 NavigationExec("navigate")과
                            # 겹치지 않는다 — 겹치면 앞의 것이 먼저 집어가 여기가 죽는다.
                            GuideExec(guide_driver or nav_driver, arrive_tolerance, arrive_resend,
                                      arrive_timeout, guide_grace, guide_timeout,
                                      stop_driver=guide_stop_driver, now_fn=clock),
                            ArmExec(arm_driver),
                            FollowExec(follow_driver),
                            py_trees.behaviours.Running(name="AwaitingCommand"),
                        ],
                    ),
                    exit_watchdog("WorkingExitConditions", [
                        FaultDetected(),
                        CommandTimeout(timeout, clock=clock),
                        CommandListener(_COMMAND_MAP),
                    ]),
                ],
            ),
            RequestTransition(),
        ],
    )
