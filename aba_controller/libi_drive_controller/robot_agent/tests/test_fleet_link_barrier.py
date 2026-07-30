"""정지는 큐를 우회한다 — 그래서 큐에 남은 주행 명령이 정지 **뒤에** 실행될 수 있다.

    goal / mission_start  →  _cmd_queue 에 적재
    mission_stop          →  콜백에서 즉시 cancel_nav()   (큐 우회, fleet_link.py 의 인라인 분기)
    worker                →  남아 있던 goal 을 꺼내 실행  → **로봇이 다시 출발한다**

`mission.stop_mission()` 은 미션 스레드와 nav2 목표만 끊고 큐는 건드리지 않는다.
큐를 비우는 것으로도 부족하다 — 워커가 이미 꺼내 든 명령은 큐에 없다. 그래서 적재 시점의
세대를 명령에 실어 두고 **실행 직전에** 확인해 버린다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture
def link():
    from app.core import fleet_link
    return fleet_link


def _tagged(link, action):
    """`on_cmd` 가 적재할 때 하는 것과 같은 태깅."""
    return {"id": "c1", "action": action, "args": {}, "_gen": link.current_generation()}


def test_queued_goal_is_dropped_after_a_stop(link):
    cmd = _tagged(link, "goal")
    assert not link.superseded_by_stop(cmd)      # 정지 전에는 유효하다
    link.bump_generation()                       # mission_stop 도착
    assert link.superseded_by_stop(cmd)          # 이제 실행되면 안 된다


def test_goal_queued_after_the_stop_survives(link):
    """정지 **뒤에** 온 새 주행 명령은 살아야 한다 — 배리어가 미래까지 막으면 로봇이 죽는다."""
    link.bump_generation()
    cmd = _tagged(link, "goal")
    assert not link.superseded_by_stop(cmd)


def test_non_motion_commands_are_never_dropped(link):
    """정지 한 번이 관계없는 명령까지 삼키면 안 된다."""
    cmds = [_tagged(link, a) for a in ("loc_set", "slam_save_map", "waypoint_save", "stop")]
    link.bump_generation()
    for cmd in cmds:
        assert not link.superseded_by_stop(cmd), cmd["action"]


def test_untagged_command_is_left_alone(link):
    """세대가 안 붙은 경로(직접 호출·인라인 정지)는 판정 대상이 아니다."""
    link.bump_generation()
    assert not link.superseded_by_stop({"id": "x", "action": "goal", "args": {}})


def test_every_motion_action_is_covered(link):
    """⚠️ 바퀴를 출발시키는 액션이 새로 생기면 이 집합에도 넣어야 한다.
    안 넣으면 그 액션만 정지를 뚫고 나간다 — `_dispatch` 의 주행 분기와 맞춰 둔다."""
    assert link.MOTION_START_ACTIONS == {
        "goal", "goto", "home", "mission_start", "schedule_start", "waypoint_goto", "dock",
    }
