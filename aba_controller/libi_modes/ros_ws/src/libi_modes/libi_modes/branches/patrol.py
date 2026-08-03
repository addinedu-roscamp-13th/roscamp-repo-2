import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import PatrolNavigation
from libi_modes.common.person_block import PersonBlockGuard, PersonBlockPolicy
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.watchdog import exit_watchdog

_COMMAND_MAP = {
    "task_assigned": "WORKING",
    "ui_touch": "INTERACTING",
    "stop_request": "IDLE",
}


def create(params: dict, driver, *, undock_gate, clock=time.monotonic,
           person_stop_driver=None, block_fn=None,
           camera_driver=None) -> py_trees.behaviour.Behaviour:
    """PatrolBranch — roam the library waiting for work.

    SuccessOnOne means the moment the watchdog Selector decides on an exit, the Parallel
    returns and PatrolNavigation is terminated (motors stopped) before the transition.
    """
    low = params["battery"]["low"]
    # 순회 주행도 배달과 같은 판정을 쓴다 — 같은 뜻을 두 값으로 두면 반드시 어긋난다.
    work = params["working"]
    person_stop_size = work.get("person_stop_size", 0)
    person_sustain = work.get("person_sustain_sec", 10.0)
    person_grace = work.get("person_resume_grace_sec", 1.0)
    nav = PatrolNavigation(driver, work["arrive_tolerance_m"],
                           work["arrive_resend_sec"], work["arrive_timeout_sec"])
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
                    nav,
                    exit_watchdog("PatrolExitConditions", [
                        FaultDetected(),
                        BatteryCheck("<=", low, "RETURNING"),
                        CommandListener(_COMMAND_MAP),
                    ]),
                    # 순회 중 앞을 막는 사람 감시. WorkingBranch 와 **같은 leaf** 다.
                    #
                    # `require_command=None` — 이 브랜치는 순회밖에 안 하므로 브랜치 자체가
                    # 게이트다. WorkingBranch 는 한 브랜치가 navigate/guide/follow/arm 을 다
                    # 처리해서 `active_command` 로 갈라야 하지만 여기는 갈 곳이 없다.
                    # (순회 중 `ACTIVE_COMMAND` 는 아예 None 이라 그 게이트를 두면 영영 안 돈다.)
                    #
                    # `block_fn` 도 꽂는다 — 순회 홉도 배달과 **같은 통로**로 온다.
                    # `fleet_dispatch_bridge.on_path_request` 가 순회 로봇에도
                    # `navigate{x,y,node,is_destination}` 을 내려보내므로(fleet_node 의
                    # `patrol_` 모드가 idle 로봇을 순회 루프로 라우팅한다),
                    # `providers._committed_node` 는 **홉마다 새로 갱신된다.**
                    # 낡은 값이 아니라 지금 가려는 정점이다 — 막히면 알려야 CBS 가 다시 짠다.
                    *([PersonBlockGuard(
                        PersonBlockPolicy(person_stop_size, person_sustain, person_grace),
                        stop_driver=person_stop_driver, block_fn=block_fn,
                        nav_leaf=nav, camera_driver=camera_driver,
                        now_fn=clock, require_command=None)] if person_stop_size > 0 else []),
                ],
            ),
            RequestTransition(),
        ],
    )
