import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.return_steps import AlreadyDocked, create_return_steps
from libi_modes.common.set_next_mode import SetNextMode
from libi_modes.common.watchdog import exit_watchdog


def create(params: dict, *, entrance_driver, dock_driver, rotate_driver,
           entrance_xy, parking_xy, clock=time.monotonic) -> py_trees.behaviour.Behaviour:
    """ReturningBranch — 충전소로 돌아간다. 부팅 직후 가장 먼저 도는 브랜치이기도 하다.

    ## 5단계로 쪼갠 이유

    예전에는 `ReturnNavigation` 한 leaf 가 팔 홈복귀·주행·도킹을 통째로 했다. 어디서
    실패했는지 화면에 안 보이고, 정밀 정렬(ArUco)을 붙일 자리도 없었다.

        1 GoToParkingEntrance   주차장 입구로 주행
        2 FaceParking           주차장 쪽으로 회전       ← 앞캠 ArUco 교체 지점
        3 GoToParking           주차장으로 주행
        4 TurnAround            180° 회전 (충전 단자가 뒤에 있다)
        5 AlignDock             정렬                     ← 뒷캠 ArUco 교체 지점

    각 단계는 `AbsorbFailure` 로 감싼다. `Parallel` 은 자식 하나가 FAILURE 를 내면
    정책과 무관하게 즉시 실패하므로, 감싸지 않으면 형제 `FaultDetected` 가 그 fault 를
    ERROR 전이로 바꿀 tick 조차 없이 브랜치가 죽는다.

    ## 로봇팔 홈 복귀는 없앴다 (사용자 결정, 2026-07-27)

    이 로봇에는 팔이 없다. **팔이 달린 로봇을 복귀시키기 전에 이 결정을 재검토해야
    한다** — 팔이 펼쳐진 채 주행하면 서가에 부딪힌다.

    ## CommandListener 를 두지 않는 이유 (기존 그대로)

    복귀 중인 로봇은 배터리가 15% 미만이다. `stop_request` 로 세우면 충전소에 도달하지
    못하고 방전된다. 도킹 성공이나 fault 로만 나간다.
    """
    r = params["returning"]
    w = params["working"]
    steps = create_return_steps(
        entrance_driver=entrance_driver,
        dock_driver=dock_driver,
        rotate_driver=rotate_driver,
        entrance_xy=entrance_xy,
        parking_xy=parking_xy,
        tolerance=w["arrive_tolerance_m"],
        resend_sec=w["arrive_resend_sec"],
        timeout_sec=w["arrive_timeout_sec"],
        yaw_tolerance_rad=r.get("yaw_tolerance_rad", 0.15),
        retry_max=r["dock_retry_max"],
        dock_confirm_sec=r.get("dock_confirm_sec", 90.0),
        now_fn=clock,
    )
    return py_trees.composites.Sequence(
        name="ReturningBranch",
        memory=False,
        children=[
            IsMode("RETURNING"),
            py_trees.composites.Parallel(
                name="ReturnAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Sequence(
                        name="ReturnSteps",
                        memory=True,
                        children=[
                            # 충전소에 놓인 채 부팅하면 5단계를 건너뛴다 — 안 그러면
                            # 입구까지 나갔다가 되돌아온다(부팅 상태가 RETURNING 이다).
                            py_trees.composites.Selector(
                                name="ReturnOrSkip",
                                memory=False,
                                children=[
                                    AlreadyDocked(),
                                    py_trees.composites.Sequence(
                                        name="ReturnDriveSteps", memory=True,
                                        children=steps),
                                ],
                            ),
                            SetNextMode("CHARGING"),
                        ],
                    ),
                    exit_watchdog("ReturningExitConditions", [FaultDetected()]),
                ],
            ),
            RequestTransition(),
        ],
    )
