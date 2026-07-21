

"""fleet_dispatch_bridge — orchestrator 와 fleet_node 사이 배선의 순수 로직.

ROS 없이 도는 부분만 여기서 본다(고아 task 판정). 실제 dispatch/release 는 fleet_node 가
필요해 sim 으로 검증한다.
"""
from __future__ import annotations

# ── 고아 task 화해 ───────────────────────────────────────────────────────────
#
# orchestrator 의 주문은 메모리에만 있다. 백엔드를 재기동하면 주문은 전부 사라지는데
# fleet_node 는 살아 있어 `orchestrator:t10` 을 계속 붙잡는다 → 그 로봇은 busy 로 고정돼
# 영원히 배차를 못 받고, 패널엔 "IDLE 1대 / 배차 가능 0대" 라는 말이 안 되는 화면이 뜬다.

from app.fleet_dispatch_bridge import TASK_PREFIX, find_orphans


def test_orphan_is_task_fleet_holds_but_orchestrator_forgot():
    robots = [{"name": "Pinkysim", "busy": True, "task_id": f"{TASK_PREFIX}t10"}]
    assert find_orphans(robots, live_ids=set()) == [("Pinkysim", f"{TASK_PREFIX}t10")]


def test_live_task_is_not_orphan():
    robots = [{"name": "Pinkysim", "busy": True, "task_id": f"{TASK_PREFIX}t10"}]
    assert find_orphans(robots, live_ids={"t10"}) == []


def test_patrol_task_is_never_touched():
    """fleet_node 자체 순회를 우리가 지우면 안 된다 — 우리 일이 아니다."""
    robots = [{"name": "Pinkysim", "busy": True, "task_id": "P-Pinkysim"}]
    assert find_orphans(robots, live_ids=set()) == []


def test_foreign_task_is_never_touched():
    """다른 주체(콘솔 수동 배차 등)가 낸 일도 건드리지 않는다."""
    robots = [{"name": "Pinkysim", "busy": True, "task_id": "T-7"}]
    assert find_orphans(robots, live_ids=set()) == []


def test_idle_robot_is_not_orphan():
    robots = [{"name": "Pinkysim", "busy": False, "task_id": ""}]
    assert find_orphans(robots, live_ids=set()) == []
