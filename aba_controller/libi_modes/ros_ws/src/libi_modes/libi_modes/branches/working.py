import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.command_listener import CommandListener
from libi_modes.common.command_timeout import CommandTimeout
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.is_mode import IsMode
from libi_modes.common.person_block import PersonBlockGuard, PersonBlockPolicy
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.watchdog import exit_watchdog
from libi_modes.common.working_actions import (ArmExec, BackupExec, FollowExec,
                                               GuideExec, NavigationExec,
                                               ShelfDockExec)

_COMMAND_MAP = {
    "task_done": "PATROL",
    "task_failed": "PATROL",
    "stop_request": "IDLE",
}


def create(params: dict, nav_driver, arm_driver, follow_driver=None,
           guide_driver=None, guide_stop_driver=None, guide_watch_driver=None,
           junctions=None, guide_result_fn=None, *, undock_gate, clock=time.monotonic,
           person_stop_driver=None, block_fn=None,
           shelf_dock_driver=None, backup_driver=None,
           camera_driver=None) -> py_trees.behaviour.Behaviour:
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
    recovery_retry_max = params["working"].get("recovery_retry_max", 3)
    recovery_stall_sec = params["working"].get("recovery_stall_sec", 0)
    guide_coast = params["working"]["guide_coast_sec"]
    guide_wait = params["working"]["guide_wait_sec"]
    # 0 이면 꺼진다. 실측 전에는 꺼 두는 것이 기본이다 — 근거 없는 임계로 멈추면
    # "왜 안 가지" 를 찾느라 시간을 버린다.
    guide_far = params["working"].get("guide_far_area_min", 0)
    guide_near = params["working"].get("guide_near_area_max", 0)
    junction_hold = params["working"].get("guide_junction_hold_sec", 0)
    # 0 이면 꺼진다 — 실측 전 기본. person_block.py 머리말 참고.
    person_stop_size = params["working"].get("person_stop_size", 0)
    person_sustain = params["working"].get("person_sustain_sec", 10.0)
    person_grace = params["working"].get("person_resume_grace_sec", 1.0)
    nav_exec = NavigationExec(nav_driver, arrive_tolerance, arrive_resend,
                              arrive_timeout, now_fn=clock,
                              recovery_retry_max=recovery_retry_max,
                              recovery_stall_sec=recovery_stall_sec)
    return py_trees.composites.Sequence(
        name="WorkingBranch",
        memory=False,
        children=[
            IsMode("WORKING"),
            # 도킹 자세에서 빠져나온다 — **주행을 내기 전에.** 벽에서 9cm 안쪽은
            # costmap 이 통행불가(253)라 nav2 가 시작 격자에서 경로를 못 만든다.
            # 도킹 상태가 아니면(평소) 즉시 통과하고 아무 일도 안 한다.
            #   근거·수치: common/undock.py 머리말
            undock_gate,
            py_trees.composites.Parallel(
                name="ExecuteAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    py_trees.composites.Selector(
                        name="CommandDispatch",
                        memory=False,
                        children=[
                            nav_exec,
                            # 길잡이. handles={"guide"} 라 NavigationExec("navigate")과
                            # 겹치지 않는다 — 겹치면 앞의 것이 먼저 집어가 여기가 죽는다.
                            GuideExec(guide_driver or nav_driver, arrive_tolerance, arrive_resend,
                                      arrive_timeout, guide_coast, guide_wait,
                                      stop_driver=guide_stop_driver,
                                      watch_driver=guide_watch_driver,
                                      far_area_min=guide_far, near_area_max=guide_near,
                                      junctions=junctions,
                                      junction_hold_sec=junction_hold,
                                      result_fn=guide_result_fn, now_fn=clock),
                            ArmExec(arm_driver),
                            # 서가 정밀 도킹·복귀. handles 가 서로 겹치지 않아야 한다 —
                            # 겹치면 앞의 것이 먼저 집어가 뒤가 영영 안 돈다.
                            ShelfDockExec(shelf_dock_driver),
                            BackupExec(backup_driver),
                            FollowExec(follow_driver),
                            py_trees.behaviours.Running(name="AwaitingCommand"),
                        ],
                    ),
                    exit_watchdog("WorkingExitConditions", [
                        FaultDetected(),
                        CommandTimeout(timeout, clock=clock),
                        CommandListener(_COMMAND_MAP),
                    ]),
                    # 주행 중 사람 감시. **명령 Selector 안이 아니라 나란히** 둔다 —
                    # Selector 에 넣으면 첫 매치 규칙 때문에 navigate 를 가로챈다.
                    # 이 leaf 는 항상 RUNNING 이라 SuccessOnOne 병렬을 끝내지 않는다.
                    # person_stop_size 가 0 이면 아예 안 붙인다(실측 전 기본).
                    *([PersonBlockGuard(
                        PersonBlockPolicy(person_stop_size, person_sustain, person_grace),
                        stop_driver=person_stop_driver, block_fn=block_fn,
                        nav_leaf=nav_exec, camera_driver=camera_driver,
                        now_fn=clock)] if person_stop_size > 0 else []),
                ],
            ),
            RequestTransition(),
        ],
    )
