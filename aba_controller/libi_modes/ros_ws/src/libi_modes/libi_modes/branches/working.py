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
           guide_driver=None, guide_stop_driver=None, guide_watch_driver=None,
           junctions=None, clock=time.monotonic) -> py_trees.behaviour.Behaviour:
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
    # 0 이면 꺼진다. 실측 전에는 꺼 두는 것이 기본이다 — 근거 없는 임계로 멈추면
    # "왜 안 가지" 를 찾느라 시간을 버린다.
    guide_far = params["working"].get("guide_far_area_min", 0)
    guide_near = params["working"].get("guide_near_area_max", 0)
    junction_hold = params["working"].get("guide_junction_hold_sec", 0)
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
                                      stop_driver=guide_stop_driver,
                                      watch_driver=guide_watch_driver,
                                      far_area_min=guide_far, near_area_max=guide_near,
                                      junctions=junctions,
                                      junction_hold_sec=junction_hold, now_fn=clock),
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
