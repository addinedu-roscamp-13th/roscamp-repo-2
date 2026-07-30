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


def create(params: dict, driver, *, undock_gate) -> py_trees.behaviour.Behaviour:
    """PatrolBranch — roam the library waiting for work.

    SuccessOnOne means the moment the watchdog Selector decides on an exit, the Parallel
    returns and PatrolNavigation is terminated (motors stopped) before the transition.
    """
    low = params["battery"]["low"]
    # 순회 주행도 배달과 같은 판정을 쓴다 — 같은 뜻을 두 값으로 두면 반드시 어긋난다.
    work = params["working"]
    return py_trees.composites.Sequence(
        name="PatrolBranch",
        memory=False,
        children=[
            IsMode("PATROL"),
            # 도킹 자세에서 빠져나온다 — **주행을 내기 전에.** 벽에서 9cm 안쪽은
            # costmap 이 통행불가(253)라 nav2 가 시작 격자에서 경로를 못 만든다.
            # 도킹 상태가 아니면(평소) 즉시 통과하고 아무 일도 안 한다.
            #   근거·수치: common/undock.py 머리말
            undock_gate,
            py_trees.composites.Parallel(
                name="PatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    PatrolNavigation(driver, work["arrive_tolerance_m"],
                                     work["arrive_resend_sec"], work["arrive_timeout_sec"]),
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
