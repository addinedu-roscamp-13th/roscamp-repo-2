"""배차 경매(Auction) — 거리로 입찰하고 최저가가 낙찰된다.

되돌림 확인(2026-08-07): `pick_robot` 의 입찰 블록을 지우고 예전 규칙(순회 우선 →
배터리 높은 순)만 남기면 `test_가까운_로봇이_낙찰된다` 와
`test_먼_로봇이_배터리가_많아도_지지_않는다` 가 빨개진다.
`priced` 분기를 지우면 `test_전원_관문_탈락이면_폴백하지_않는다` 가 빨개진다.
"""

import pytest

from app import fleet_dispatch_bridge as bridge


# ── 가짜 navgraph ────────────────────────────────────────────────────────────
#
#   0 ──1.0── 1 ──1.0── 2 ──1.0── 3        (양방향으로 적는다)
#
# 목적지를 v3 으로 두면 v2 에 선 로봇이 1.0 m, v0 에 선 로봇이 3.0 m 로 입찰한다.
_COORDS = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
_LANES = [(0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2)]


@pytest.fixture(autouse=True)
def fake_graph(monkeypatch):
    adj: dict[int, list[tuple[int, float]]] = {}
    for a, b in _LANES:
        (ax, ay), (bx, by) = _COORDS[a], _COORDS[b]
        adj.setdefault(a, []).append((b, ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5))
    monkeypatch.setattr(bridge, "_lane_adj", adj)
    monkeypatch.setattr(bridge, "_vertex_coords", list(_COORDS))
    monkeypatch.setattr(bridge, "_reject_reason", lambda r: None)
    yield


def _robot(name, node, battery=100.0):
    return {"name": name, "held_nodes": [node], "battery": battery,
            "state": "PATROL", "busy": False}


# ── 입찰 ────────────────────────────────────────────────────────────────────
def test_가까운_로봇이_낙찰된다():
    robots = [_robot("멀리", 0), _robot("가까이", 2)]
    assert bridge.pick_robot(robots, goal_idx=3) == "가까이"


def test_먼_로봇이_배터리가_많아도_지지_않는다():
    """배터리는 입찰가가 아니다 — 통과한 로봇끼리는 거리로만 겨룬다."""
    robots = [_robot("멀리", 0, battery=100.0), _robot("가까이", 2, battery=30.0)]
    assert bridge.pick_robot(robots, goal_idx=3) == "가까이"


def test_경로가_없으면_입찰에서_빠진다():
    """v3 에서 출발하는 레인이 없는 방향은 고립된 것으로 본다."""
    robots = [_robot("고립", 3), _robot("정상", 2)]
    assert bridge.pick_robot(robots, goal_idx=0) == "정상"


# ── 완주 관문 ───────────────────────────────────────────────────────────────
def test_완주_관문에_걸리면_낙찰되지_않는다():
    """3.0 m × 1.0 %/m + 여유 15 % = 18 % 가 필요하다."""
    robots = [_robot("가까운데_방전직전", 0, battery=17.0), _robot("멀지만_충분", 2, battery=50.0)]
    assert bridge.pick_robot(robots, goal_idx=3) == "멀지만_충분"


def test_전원_관문_탈락이면_폴백하지_않는다():
    """폴백하면 배터리 부족한 로봇이 낙찰되어 fleet_node 가 거절한다."""
    robots = [_robot("a", 0, battery=5.0), _robot("b", 2, battery=5.0)]
    assert bridge.pick_robot(robots, goal_idx=3) is None


# ── 폴백 ────────────────────────────────────────────────────────────────────
def test_목표를_모르면_예전_규칙으로_고른다():
    robots = [{"name": "대기", "state": "IDLE", "battery": 90.0, "held_nodes": [0]},
              {"name": "순회", "state": "PATROL", "battery": 40.0, "held_nodes": [2]}]
    assert bridge.pick_robot(robots, goal_idx=None) == "순회"   # 순회 우선


def test_위치를_모르면_예전_규칙으로_고른다():
    """좌표도 점유 노드도 없으면 거리를 잴 수 없다 — priced 가 0 이라 폴백한다."""
    robots = [{"name": "배터리적음", "state": "PATROL", "battery": 40.0},
              {"name": "배터리많음", "state": "PATROL", "battery": 90.0}]
    assert bridge.pick_robot(robots, goal_idx=3) == "배터리많음"


# ── 목적지 뽑기 ─────────────────────────────────────────────────────────────
def test_첫_주행_다리의_목적지를_쓴다(monkeypatch):
    monkeypatch.setattr(bridge, "resolve_vertex", lambda n: {"서가": 2, "테이블": 3}[n])
    task = {"legs": [
        {"type": "navigate", "params": {"waypoint": "서가"}},
        {"type": "perform_action", "params": {"action": "pick"}},
        {"type": "navigate", "params": {"waypoint": "테이블"}},
    ]}
    assert bridge.first_goal_vertex(task) == 2


def test_주행_다리가_없으면_목적지가_없다():
    task = {"legs": [{"type": "perform_action", "params": {"action": "pick"}}]}
    assert bridge.first_goal_vertex(task) is None


# ── SSI 라운드 ───────────────────────────────────────────────────────────────
#
# 되돌림 확인: `_best_pair` 를 "첫 주문에 대해 가장 싼 로봇" 으로 바꾸면
# `test_전체_짝을_보고_가장_싼_것부터_낙찰한다` 가 빨개진다.
def test_전체_짝을_보고_가장_싼_것부터_낙찰한다():
    """주문을 고정해 두고 로봇만 고르면 뒤 주문이 먼 로봇을 떠안는다.

        A(→v0)  ·  B(→v3)          r0 은 v0, r3 은 v3 에 서 있다

    A 를 먼저 처리하면 A 는 r0(0.0m)을 집고 B 는 r3(0.0m)을 받아 합 0.0 — 이 배치는
    운 좋게 같지만, 순서를 A→B 로 **고정**하면 아래처럼 어긋나는 경우가 생긴다:
    A 의 최저가가 r3(3.0m) 이고 B 의 최저가도 r3(0.0m) 일 때, A 가 먼저 r3 을 집으면
    B 는 r0(3.0m) 을 떠안아 합 6.0 이 된다. 전체를 보면 B→r3, A→r0 으로 합 3.0 이다.
    """
    tasks = [{"id": "A", "priority": 0}, {"id": "B", "priority": 0}]
    robots = [_robot("r0", 0), _robot("r3", 3)]
    goals = {"A": 0, "B": 3}
    task, robot = bridge._best_pair(tasks, robots, goals)
    # 최저 입찰가는 0.0 이 둘(A-r0, B-r3) — 어느 쪽이든 자기 자리 로봇이 낙찰돼야 한다.
    assert (task["id"], robot["name"]) in {("A", "r0"), ("B", "r3")}


def test_먼_주문이_가까운_로봇을_가로채지_않는다():
    """B 만 r3 에서 갈 수 있으면, A 가 r3 을 먼저 집으면 안 된다."""
    tasks = [{"id": "A", "priority": 0}, {"id": "B", "priority": 0}]
    robots = [_robot("r3", 3)]
    goals = {"A": 0, "B": 2}          # A 는 3.0m, B 는 1.0m
    task, robot = bridge._best_pair(tasks, robots, goals)
    assert task["id"] == "B"          # 더 싼 짝이 먼저 낙찰된다


def test_입찰자가_없으면_짝이_없다():
    tasks = [{"id": "A", "priority": 0}]
    robots = [_robot("방전", 0, battery=1.0)]
    assert bridge._best_pair(tasks, robots, {"A": 3}) is None
