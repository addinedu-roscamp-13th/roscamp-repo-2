import time

import py_trees
from py_trees.common import ParallelPolicy

from libi_modes.common.battery_check import BatteryCheck
from libi_modes.common.command_listener import CommandListener
from libi_modes.common.fault_detected import FaultDetected
from libi_modes.common.intruder_chase import CameraSelectRenew, ChasePolicy, IntruderChase
from libi_modes.common.is_mode import IsMode
from libi_modes.common.navigation_actions import PatrolNavigation
from libi_modes.common.request_transition import RequestTransition
from libi_modes.common.watchdog import exit_watchdog

# Night operation: no task assignment, no visitor touch — only an operator stop.
#
# ⚠️ `security_patrol_complete` 라는 명령은 **없다.** 전이표(registry.py)에 그렇게
# 적혀 있었지만 이 맵에도, 레포 어디에도 그 문자열을 보내는 곳이 없었다. 야간 순찰은
# 설계상 **스스로 끝나지 않으므로**(아래 독스트링) 그 명령이 있어야 할 이유도 없다.
# 표만 거짓이었다 — 관제 화면이 있지도 않은 전이를 안내하고 있었다(2026-07-28 수정).
_COMMAND_MAP = {"stop_request": "IDLE"}


def _chase_policy(params: dict) -> ChasePolicy:
    """설정이 없어도 기본값으로 돈다.

    ⚠️ `params["security_patrol"]` 로 읽으면 **params.yaml 을 안 고친 로봇에서
       `KeyError` 로 `fsm_node` 가 통째로 죽는다**(관제엔 `state=None`, 로봇 무응답).
       배포 순서가 어긋나도 살아 있어야 한다.
    """
    sp = params.get("security_patrol") or {}
    return ChasePolicy(
        trigger_size=float(sp.get("intruder_size", 100.0)),
        sustain_sec=float(sp.get("intruder_sustain", 1.5)),
        lose_sec=float(sp.get("intruder_lose_sec", 5.0)),
        max_chase_sec=float(sp.get("max_chase_sec", 60.0)),
        release_grace_sec=float(sp.get("release_grace_sec", 1.0)),
        failure_backoff_sec=float(sp.get("failure_backoff_sec", 10.0)),
    )


def create(params: dict, driver, *, undock_gate, clock=time.monotonic,
           person_stop_driver=None, block_fn=None,
           camera_driver=None, follow_driver=None) -> py_trees.behaviour.Behaviour:
    """SecurityPatrolBranch — keep patrolling for night security (does NOT end after one lap).

    Same skeleton and same execution path as PATROL (PatrolNavigation over fleet_node-granted
    nodes, so it never self-completes). Two differences only: the state gate is SECURITY_PATROL,
    and the command map is deliberately narrow (stop only) so a task assignment or a panel touch
    cannot pull the robot off night duty. It leaves SECURITY_PATROL only on stop / fault / low
    battery — that is what makes the robot hold the state through the night (auto-PATROL lives in
    IDLE, which the robot never reaches while it keeps patrolling).

    ⚠️ 야간 정책: 사람이 보이면 순찰을 멈추기만 하던 자리를 **추종**이 대신한다
    (`IntruderChase`). 사람 정지 잎과 추종 잎을 같이 두면 둘 다 `/cmd_vel` 을 두고
    싸우므로, 야간은 정지 대신 추종 하나만 둔다 — `person_stop_driver`/`block_fn` 은
    하위 호환을 위해 시그니처에는 남아 있지만 이 브랜치는 더 이상 정지 감시자를 세우지
    않는다. `person_stop_driver` 는 `IntruderChase` 가 추종을 열기 전 nav2 목표를
    비우는 데 그대로 쓰인다.
    """
    low = params["battery"]["low"]
    # 순회 주행과 같은 도착 판정 파라미터를 쓴다 — 같은 뜻을 두 값으로 두면 반드시 어긋난다.
    work = params["working"]
    nav = PatrolNavigation(driver, work["arrive_tolerance_m"],
                           work["arrive_resend_sec"], work["arrive_timeout_sec"],
                           recovery_retry_max=work.get("recovery_retry_max", 3),
                           recovery_stall_sec=work.get("recovery_stall_sec", 0))
    return py_trees.composites.Sequence(
        name="SecurityPatrolBranch",
        memory=False,
        children=[
            IsMode("SECURITY_PATROL"),
            # 도킹 자세에서 빠져나온다 — **주행을 내기 전에.** 벽에서 9cm 안쪽은
            # costmap 이 통행불가(253)라 nav2 가 시작 격자에서 경로를 못 만든다.
            # 도킹 상태가 아니면(평소) 즉시 통과하고 아무 일도 안 한다.
            #   근거·수치: common/undock.py 머리말
            undock_gate,
            py_trees.composites.Parallel(
                name="SecurityPatrolAndWatch",
                policy=ParallelPolicy.SuccessOnOne(),
                children=[
                    # 침입자가 보이면 IntruderChase 가 RUNNING 을 물어 순찰을 선점한다.
                    # 안 보이면 FAILURE 를 내려 PatrolNavigation 이 돈다(memory=False 라
                    # 매 tick 우선순위를 다시 판정한다).
                    py_trees.composites.Selector(
                        name="PatrolOrChase",
                        memory=False,
                        children=[
                            IntruderChase(_chase_policy(params),
                                          follow_driver=follow_driver,
                                          nav_stop_driver=person_stop_driver,
                                          now_fn=clock),
                            nav,
                            # nav 는 arrive_timeout_sec 초과·driver 실패로 FAILURE 를 낼
                            # 수 있다(NavigationExec._give_up). 이 Selector 는 Parallel
                            # 의 직속 자식이라 그 FAILURE 가 그대로 exit_watchdog·
                            # CameraSelectRenew 를 무효화한다(test_tree.py:
                            # test_watchdogs_inside_parallels_end_with_running). 다음
                            # tick 에 nav 가 처음부터 다시 시도하는 동안 형제를 살려 둔다.
                            py_trees.behaviours.Running(name="SecurityPatrolFallback"),
                        ],
                    ),
                    exit_watchdog("SecurityPatrolExitConditions", [
                        FaultDetected(),
                        BatteryCheck("<=", low, "RETURNING"),
                        CommandListener(_COMMAND_MAP),
                    ]),
                    # ⚠️ 야간에는 `PersonBlockGuard` 를 안 세운다 — 위 `PatrolOrChase`
                    # 가 그 자리를 대신한다(사람이 보이면 정지 대신 추종). 그래서
                    # PersonBlockGuard 가 부수적으로 해 주던 **앞캠을 켜 두는 통로**가
                    # 같이 사라진다(person_block.py:241). 그 20줄만 떼어 살린다 — 없으면
                    # 프레임이 아예 안 와서 야간 기능 전체가 조용히 죽는다.
                    CameraSelectRenew(camera_driver=camera_driver, now_fn=clock),
                ],
            ),
            RequestTransition(),
        ],
    )
