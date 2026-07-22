import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.min_dwell import MinDwell
from libi_modes.common.navigation_actions import SecurityPatrolNavigation
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.set_next_mode import SetNextMode
from libi_modes.common.watchdog import exit_watchdog

# Night operation: no task assignment, no visitor touch — only an operator stop.
_COMMAND_MAP = {"stop_request": "IDLE"}


def create(params: dict, driver) -> py_trees.behaviour.Behaviour:
    """SecurityPatrolBranch — one intrusion-detection lap, then back to IDLE.

    Same skeleton as PATROL with two differences: the nav leaf finishes after one lap
    (so SetNextMode sends it home), and the command map is deliberately narrow so a task
    assignment or a panel touch cannot pull the robot off night duty.

    ## MinDwell 이 랩과 SetNextMode 사이에 있는 이유

    드라이버가 곧바로 `success` 를 돌려주면(순찰 경로가 비었거나 스텁이면) 랩이 **한 tick
    만에** 끝나 그대로 IDLE 로 나간다. 관제에서 SECURITY_PATROL 을 눌러도 화면상 아무
    일도 안 일어난다.

    진짜 랩이 몇 분 걸리는 경우에는 이 leaf 가 아무것도 안 한다 — 상태 진입 시각부터
    재기 때문에 이미 조건이 충족돼 그냥 통과한다. **비정상적으로 빨리 끝났을 때만** 문다.
    """
    low = params["battery"]["low"]
    min_dwell = params.get("min_dwell_sec", 0.0)
    return py_trees.composites.Sequence(
        name="SecurityPatrolBranch",
        memory=False,
        children=[
            IsMode("SECURITY_PATROL"),
            py_trees.composites.Parallel(
                name="SecurityPatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Sequence(
                        name="OnePatrolLoop",
                        memory=True,
                        children=[
                            SecurityPatrolNavigation(driver),
                            MinDwell(min_dwell),
                            SetNextMode("IDLE"),
                        ],
                    ),
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
