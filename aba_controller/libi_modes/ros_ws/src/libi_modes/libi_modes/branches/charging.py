import py_trees

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.min_dwell import MinDwell
from libi_modes.common.request_transition import RequestTransition


def create(params: dict) -> py_trees.behaviour.Behaviour:
    """ChargingBranch — dock and charge until BATTERY_READY.

    Below the threshold with no fault the Selector fails, so the branch fails and nothing
    happens. That failure IS the waiting behaviour — no explicit wait node is needed.

    ## MinDwell 이 배터리 검사 앞에만 붙는 이유

    이미 충전된 로봇이 CHARGING 으로 들어오면 `BatteryCheck(>=40)` 이 **첫 tick 에** 통과해
    같은 tick 안에 IDLE 로 나간다. 상태 기계로는 맞지만 LED·패널·감사로그 어디에도 안
    남는다 — 관제에서 CHARGING 을 눌러도 아무 일도 안 일어난 것처럼 보인다.

    ⚠️ `FaultDetected` 는 **일부러 지연 밖에** 뒀다. 고장은 즉시 반응해야 한다.
       지연되는 건 "충전 다 됐으니 나간다" 하나뿐이다.

    ⚠️ `BatteryCheck` 가 `MinDwell` **앞**에 있어야 한다. 뒤에 두면 문턱 아래일 때도
       먼저 3초를 붙잡아, 위 문단의 "실패가 곧 기다림"이 3초간 RUNNING 으로 바뀐다.
       나갈 이유가 없을 때는 지연도 없어야 한다.
    """
    ready = params["battery"]["ready"]
    min_dwell = params.get("min_dwell_sec", 0.0)
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
                    py_trees.composites.Sequence(
                        name="ChargedAfterMinDwell",
                        memory=False,
                        children=[
                            BatteryCheck(">=", ready, "IDLE"),
                            MinDwell(min_dwell),
                        ],
                    ),
                ],
            ),
            RequestTransition(),
        ],
    )
