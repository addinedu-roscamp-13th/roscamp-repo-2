"""상태가 바뀌면 **진행 중이던 주행이 멈춰야** 한다.

실측 신고(2026-07-28): "응대중인데 계속 바퀴가 움직임". 순회 중 방문객이 패널을
만져 INTERACTING 으로 넘어갔는데, 순회가 내보낸 주행 목표를 아무도 안 끊어서
화면은 '응대중'인데 로봇은 다음 순회 정점으로 계속 갔다.

여기서 보는 것은 **BT 층까지**다 — 선점된 액션 leaf 가 `driver.stop()` 을 부르는가.
그 stop 이 실제로 nav2 목표를 취소하는지는 실행 층(robot_agent)의 몫이고 별개다.
"""
import py_trees
import pytest
from py_trees.common import Status

from libi_modes import tree
from libi_modes.blackboard import Keys
from test.fakes import PARAMS, all_drivers, all_providers

# 순회도 배달과 같은 실행 경로다 — fleet_node 가 허가한 노드가 `navigate` 로 내려와야
# `PatrolNavigation` 이 주행을 낸다(navigation_actions.py 머리말). 목적지 없이 PATROL
# 이기만 하면 그 리프는 idle 로 FAILURE 다.
DRIVING = {Keys.ACTIVE_COMMAND: "navigate",
           Keys.NAV_TARGET: {"x": 1.0, "y": 0.0, "yaw": 0.0},
           Keys.ROBOT_POSE: {"x": 0.0, "y": 0.0}}


@pytest.fixture
def built():
    """(bt, drivers) — 트리를 세우고 드라이버를 들여다볼 수 있게 돌려준다."""
    drivers = all_drivers()
    root = tree.build_root(PARAMS, drivers, all_providers())
    bt = py_trees.trees.BehaviourTree(root=root)
    bt.setup(timeout=15)
    return bt, drivers, root


def _running_names(node, out=None):
    out = [] if out is None else out
    if node.status == Status.RUNNING:
        out.append(node.name)
    for c in getattr(node, "children", []):
        _running_names(c, out)
    return out


def test_patrol_starts_the_drive(built, seed):
    """전제 확인 — 이게 깨지면 아래 테스트가 다른 이유로 통과한다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started


def test_interacting_preempts_and_stops_patrol_drive(built, seed):
    """이 파일의 존재 이유. 선점만 하고 안 멈추면 로봇이 계속 굴러간다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started, "전제: 순회가 주행을 냈다"

    seed(**DRIVING, **{Keys.CURRENT_MODE: "INTERACTING"})   # 방문객이 패널을 만졌다
    bt.tick()

    assert drivers["patrol"].stopped, (
        "INTERACTING 이 순회를 선점했는데 driver.stop() 이 안 불렸다 — "
        "화면은 '응대중'인데 바퀴는 계속 돈다")


def test_interacting_branch_actually_runs(built, seed):
    """선점이 진짜로 일어났는지."""
    bt, _, root = built
    seed(**{Keys.CURRENT_MODE: "INTERACTING"})
    bt.tick()
    names = _running_names(root)
    assert "InteractingBranch" in names, f"RUNNING: {names}"


def test_working_preempts_and_stops_patrol_drive(built, seed):
    """작업 배차도 같다 — 순회 목표가 살아 있으면 배달 목표와 다툰다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    bt.tick()
    assert drivers["patrol"].started

    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})
    bt.tick()
    assert drivers["patrol"].stopped


def test_leaving_working_stops_the_follow_session(built, seed):
    """추종 종료 버튼이 실제로 세션을 끄는가 — 화면에서 의심이 나온 자리다.

    패널 「해제」는 로봇을 직접 안 멈춘다. FMS 에 해제를 보고하고, FMS 가 로봇을 IDLE 로
    옮긴다(admin_follow.py RELEASE_STATE). 세션을 실제로 끊는 건 그 전이에 선점된
    `FollowExec.terminate(INVALID)` 뿐이다 — 여기가 끊기면 미션 BT 만 빠져나오고
    libi_perception 의 제어 루프는 계속 20Hz 로 `/cmd_vel` 을 민다.

    실측 2026-07-28: 관제 BT 화면에서 `FollowExec` 은 회색인데 그 밑 `Following[TRACKING]`
    이 파란색으로 남아 있었다.
    """
    bt, drivers, _ = built
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert drivers["follow"].started, "전제: 추종 세션이 열렸다"

    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()

    assert drivers["follow"].stopped, (
        "WORKING 을 벗어났는데 추종 driver.stop() 이 안 불렸다 — "
        "화면은 대기인데 제어 루프는 계속 돈다")


def test_a_new_command_stops_the_follow_session_it_replaces(built, seed):
    """명령이 바뀌어도 옛 세션은 닫혀야 한다 — 상태 전이만 챙기면 이게 새어 나간다.

    실측 2026-07-28, `/fleet_cmd` echo:

        follow_admin-1-...        ← 추종 시작
        (20초 뒤) navigate ...    ← 관제가 다음 주행을 배차
        follow_admin-2-...        ← 두 번째 추종
        ... `stop` 은 단 한 번도 없다

    `navigate` 가 `active_command` 를 덮으면 `FollowExec` 은 그저 FAILURE 를 돌려주고
    빠진다(`CommandDrivenAction.update` 의 첫 분기). 그건 상태 전이가 아니라 **명령
    교체**라 `terminate(INVALID)` 도 안 탄다 — 아무도 `driver.stop()` 을 안 부른다.
    libi_perception 의 제어 루프는 계속 20Hz 로 `/cmd_vel` 을 밀고,
    `/libi/follow_bt_snapshot` 은 `Following[TRACKING]` 을 영원히 내보낸다.
    """
    bt, drivers, _ = built
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert drivers["follow"].started, "전제: 추종 세션이 열렸다"

    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})   # 관제가 주행을 배차했다
    bt.tick()

    assert drivers["follow"].stopped, (
        "새 명령이 추종을 밀어냈는데 driver.stop() 이 안 불렸다 — "
        "옛 추종 루프가 nav2 와 같이 /cmd_vel 을 민다")


def test_follow_stops_even_when_nobody_claims_the_new_command(built, seed):
    """가로채는 사람이 없어도 옛 세션은 닫혀야 한다 — 실측 로그가 바로 이 경우다.

    앞 시험이 통과하는 건 `NavigationExec` 이 Selector 에서 앞에 있어 py_trees 가 뒤의
    `FollowExec` 을 `INVALID` 로 무효화해 주기 때문이다. 그 보호는 **누군가 가로챌 때만**
    작동한다. 좌표 없는 `navigate` 처럼 아무도 못 집는 명령이 오면 `FollowExec` 은 제
    "내 명령 아님" 분기로 조용히 빠지고, 세션은 그대로 남는다.
    """
    bt, drivers, _ = built
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()
    assert drivers["follow"].started

    # 좌표가 없다 — NavigationExec 도 못 집는다.
    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "navigate"})
    bt.tick()

    assert drivers["follow"].stopped, (
        "아무도 안 집는 명령이 왔는데 추종이 손만 놨다 — 제어 루프가 계속 돈다")


def test_a_new_command_stops_the_drive_it_replaces(built, seed):
    """주행도 같다 — 남으면 옛 nav2 목표가 새 명령과 다툰다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "WORKING"})
    bt.tick()
    assert drivers["nav"].started

    seed(**{Keys.CURRENT_MODE: "WORKING", Keys.ACTIVE_COMMAND: "follow_admin"})
    bt.tick()

    assert drivers["nav"].stopped


def test_stop_not_called_while_patrol_keeps_running(built, seed):
    """멀쩡히 도는 중에 stop 을 부르면 순회가 매 tick 끊긴다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    for _ in range(5):
        bt.tick()
    assert not drivers["patrol"].stopped
