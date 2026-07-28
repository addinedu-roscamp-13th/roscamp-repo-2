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


def test_stop_not_called_while_patrol_keeps_running(built, seed):
    """멀쩡히 도는 중에 stop 을 부르면 순회가 매 tick 끊긴다."""
    bt, drivers, _ = built
    seed(**DRIVING, **{Keys.CURRENT_MODE: "PATROL"})
    for _ in range(5):
        bt.tick()
    assert not drivers["patrol"].stopped
