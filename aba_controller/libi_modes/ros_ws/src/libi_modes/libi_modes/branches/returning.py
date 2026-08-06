import math
import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.return_steps import (
    AlreadyDocked, BackCamOn, create_return_steps, wrap_angle,
)
from libi_modes.common.set_next_mode import SetNextMode
from libi_modes.common.watchdog import exit_watchdog


def create(params: dict, *, entrance_driver, rotate_driver, nav_release_driver,
           dock_driver, nudge_driver, back_cam_driver,
           entrance_xy, entrance_yaw, dock_xy=None, clock=time.monotonic) -> py_trees.behaviour.Behaviour:
    """ReturningBranch — 충전소로 돌아간다. 부팅 직후 가장 먼저 도는 브랜치이기도 하다.

    ## 단계로 쪼갠 이유

    예전에는 `ReturnNavigation` 한 leaf 가 팔 홈복귀·주행·도킹을 통째로 했다. 어디서
    실패했는지 화면에 안 보이고, 정밀 정렬(ArUco)을 붙일 자리도 없었다.

        1 GoToParkingEntrance   주차장 입구로 주행
        2 FaceApproachYaw       접근 자세로 회전 (절대각 — 충전 단자가 뒤로 온다)
        3 ReleaseNav            nav2 목표를 끊는다 (바퀴를 외부에 넘기기 전)
        4 DockApproach          정밀 도킹 6cm 까지        ← **`dock_sensor` 가 정한다**
        5 DockNudge             정속 개루프로 후진 (라이다면 0)
        6 DockSettle            안정화 대기 후 도킹으로 침

    각 단계는 `AbsorbFailure` 로 감싼다. `Parallel` 은 자식 하나가 FAILURE 를 내면
    정책과 무관하게 즉시 실패하므로, 감싸지 않으면 형제 `FaultDetected` 가 그 fault 를
    ERROR 전이로 바꿀 tick 조차 없이 브랜치가 죽는다.

    ## [2026-07-30] `GoToParking` · `TurnAround` 를 없앴다

    2단계가 이제 "주차장을 바라본다"가 아니라 **접근 자세로 돌린다**(절대각). 그러면
    충전 단자가 뒤를 향하므로 180° 를 따로 돌 필요가 없고, 주차장 정점까지 nav2 로 가는
    구간은 4단계의 정밀 도킹(ArUco 또는 라이다, `dock_sensor` 가 정한다)이 대신한다.
    AMCL 오차가 충전 단자 폭보다 큰 구간을 nav2 로 밀어 넣지 않는 것이 이 재편의 요점이다.

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
        rotate_driver=rotate_driver,
        nav_release_driver=nav_release_driver,
        dock_driver=dock_driver,
        nudge_driver=nudge_driver,
        entrance_xy=entrance_xy,
        dock_xy=dock_xy,
        # ②의 목표각은 **접근 정점 yaw + 180°** 다. 정점 yaw 가 충전소를 바라보므로
        # 반 바퀴 돌면 **뒷캠이 충전소를 본다** — 그래야 ④가 마커를 잡는다.
        # params 에 `approach_yaw_rad` 를 명시하면 그 값이 이긴다(현장 보정용).
        approach_yaw=(r["approach_yaw_rad"] if r.get("approach_yaw_rad") is not None
                      else wrap_angle(entrance_yaw + math.pi)),
        tolerance=w["arrive_tolerance_m"],
        resend_sec=w["arrive_resend_sec"],
        timeout_sec=w["arrive_timeout_sec"],
        yaw_tolerance_rad=r.get("yaw_tolerance_rad", 0.15),
        retry_max=r["dock_retry_max"],
        settle_sec=r.get("settle_sec", 1.0),
        now_fn=clock,
        recovery_retry_max=w.get("recovery_retry_max", 3),
        recovery_stall_sec=w.get("recovery_stall_sec", 0),
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
                    # ⚠️ **맨 앞이어야 한다.** Parallel 은 자식을 순서대로 tick 하므로,
                    #    뒤에 두면 첫 tick 에 ReturnSteps 가 먼저 돌아 뒷캠 선택이 한 박자
                    #    늦는다. 절대 끝나지 않는 leaf 라 SuccessOnOne 을 건드리지 않는다.
                    BackCamOn(back_cam_driver),
                    py_trees.composites.Sequence(
                        name="ReturnSteps",
                        memory=True,
                        children=[
                            # 충전소에 놓인 채 부팅하면 6단계를 건너뛴다 — 안 그러면
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
