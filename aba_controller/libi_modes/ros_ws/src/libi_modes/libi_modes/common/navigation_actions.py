"""관제가 허가한 노드를 따라 도는 순회 주행.

## 왜 `mission_start` 가 아닌가

예전엔 `PatrolNavigation` 이 `mission_start` 를 보냈고, robot_agent 가
`mission.start_mission()` 으로 **로봇이 자기 waypoint 목록을 혼자 돌았다.**
그런데 `fleet_node` 도 자체 순회(`patrol_route_`)를 돌리며 노드를 하나씩 허가한다.

즉 **순회가 두 벌 동시에 같은 로봇을 몰고 있었다.** 로봇 쪽 목록은 교통 예약
(`traffic_->request_move`)을 아예 안 거치므로, 로봇이 2대가 되는 순간 순회끼리
충돌한다 — 그리고 교통 알고리즘을 CBS 로 바꿔도 그쪽은 따라가지 않는다.

그래서 순회도 배달과 **같은 길**로 몬다: fleet_node 가 허가한 노드가
`/fleet_cmd{navigate}` 로 내려오고, 이 리프가 `goal` 로 실행한다.
`goal` 한 종류로 배달·순회가 모두 처리된다.

## 배달과 다른 점은 하나뿐

도착했을 때 무엇을 뜻하는가:

    배달(NavigationExec)  도착 = 그 다리가 끝났다        → SUCCESS
    순회(PatrolNavigation) 도착 = 한 노드를 지났다        → RUNNING

그래서 주행 로직을 물려받고 그 두 훅만 갈아끼운다.
"""
from py_trees.common import Status

from libi_modes.common.working_actions import NavigationExec


class PatrolNavigation(NavigationExec):
    """관제가 허가한 노드를 따라 도는 순회. **스스로는 끝나지 않는다.**

    끝나는 건 형제인 감시 Selector 가 Parallel 을 이겨서 이 리프를 무효화할 때뿐이다
    (배터리 저하, 작업 배차, 고장). 그래서 도착도 대기도 RUNNING 이다.
    """

    def __init__(self, driver, arrive_tolerance: float, arrive_resend_sec: float,
                 arrive_timeout_sec: float, name: str | None = None, now_fn=None):
        kwargs = {} if now_fn is None else {"now_fn": now_fn}
        super().__init__(driver, arrive_tolerance, arrive_resend_sec, arrive_timeout_sec,
                         name=name or "PatrolNavigation", **kwargs)

    def _idle_status(self) -> Status:
        # 다음 노드 허가를 기다리는 중이다. FAILURE 를 내면 Parallel 이 무너져
        # 순회 브랜치가 매 tick 재진입하고, 그때마다 주행이 처음부터 다시 시작된다.
        return Status.RUNNING

    def _arrived_status(self) -> Status:
        return Status.RUNNING


# [2026-07-26] SecurityPatrolNavigation 삭제.
#
# 정의만 있고 인스턴스화하는 곳이 한 군데도 없었다(테스트 포함). 그런데 docstring 은
# "침입 처리(기록·저장·관리자 통보, SR-19)가 driver 안에 있다"고 적혀 있어서, 읽는 사람이
# 경비순찰에 이미 침입 대응이 구현돼 있다고 오해하게 만들었다. 실제 SECURITY_PATROL 브랜치는
# PatrolNavigation 을 쓰고(branches/security_patrol.py:38), 그 드라이버는 goal 을 쏘는
# FleetCmdDriver 일 뿐이다(main.py:132-137). 침입 대응은 아직 존재하지 않는다.
#
# 없는 기능을 있는 것처럼 말하는 주석은 죽은 코드보다 나쁘다. 그래서 지운다.
# 실제 구현 계획: docs 07-26-plan/02-security-patrol-사람감지-대응.md
