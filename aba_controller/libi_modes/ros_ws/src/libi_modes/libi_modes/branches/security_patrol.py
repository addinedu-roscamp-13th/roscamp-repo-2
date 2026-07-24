import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import PatrolNavigation
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.watchdog import exit_watchdog

# Night operation: no task assignment, no visitor touch — only an operator stop.
_COMMAND_MAP = {"stop_request": "IDLE"}


def create(params: dict, driver) -> py_trees.behaviour.Behaviour:
    """SecurityPatrolBranch — keep patrolling for night security (does NOT end after one lap).

    Same skeleton and same execution path as PATROL (PatrolNavigation over fleet_node-granted
    nodes, so it never self-completes). Two differences only: the state gate is SECURITY_PATROL,
    and the command map is deliberately narrow (stop only) so a task assignment or a panel touch
    cannot pull the robot off night duty. It leaves SECURITY_PATROL only on stop / fault / low
    battery — that is what makes the robot hold the state through the night (auto-PATROL lives in
    IDLE, which the robot never reaches while it keeps patrolling).
    """
    low = params["battery"]["low"]
    # 순회 주행과 같은 도착 판정 파라미터를 쓴다 — 같은 뜻을 두 값으로 두면 반드시 어긋난다.
    work = params["working"]
    return py_trees.composites.Sequence(
        name="SecurityPatrolBranch",
        memory=False,
        children=[
            IsMode("SECURITY_PATROL"),
            py_trees.composites.Parallel(
                name="SecurityPatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    PatrolNavigation(driver, work["arrive_tolerance_m"],
                                     work["arrive_resend_sec"], work["arrive_timeout_sec"]),
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
