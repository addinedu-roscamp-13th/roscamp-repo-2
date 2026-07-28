"""`stop` 은 세션만 닫는 게 아니라 **주행도 끊어야** 한다.

실측 신고(2026-07-28): "응대중인데 계속 바퀴가 움직임".

원인: BT 가 브랜치를 선점당하면 `DriverAction.terminate()` → `driver.stop()` →
`FleetCmdDriver.stop()` 이 `{"action":"stop"}` 을 보낸다. 그런데 `stop` 이
`BT_LAYER_ACTIONS` 에 있어 "수락만 하고 아무것도 안 함" 분기로 흘렀다.
세션은 follow_node 가 닫지만 그쪽은 `/cmd_vel` 만 다룬다 — `goal` 로 낸 nav2 목표는
아무도 안 끊었고, 로봇은 순회 정점으로 계속 갔다.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture
def link(monkeypatch):
    """`_dispatch` 와, 그것이 부르는 가짜 ros_bridge 를 함께 돌려준다.

    `_dispatch` 는 `from app.core import ... ros_bridge` 를 **함수 안에서** 하므로
    모듈 속성을 갈아끼우면 그대로 잡힌다.
    """
    from app.core import fleet_link

    calls = {"cancel": 0, "goal": []}
    fake = types.SimpleNamespace(
        active=True,
        is_active=lambda: fake.active,
        cancel_nav=lambda: calls.__setitem__("cancel", calls["cancel"] + 1),
        send_nav_goal=lambda x, y, yaw: (calls["goal"].append((x, y, yaw)), True)[1],
    )
    import app.core as core
    monkeypatch.setattr(core, "ros_bridge", fake, raising=False)
    monkeypatch.setitem(sys.modules, "app.core.ros_bridge", fake)
    return fleet_link, fake, calls


def test_stop_cancels_the_active_nav_goal(link):
    """이 파일의 존재 이유."""
    fleet_link, _fake, calls = link
    ok, status, data, _ = fleet_link._dispatch("stop", {})
    assert ok and status == 202
    assert calls["cancel"] == 1, "stop 이 왔는데 주행을 안 끊었다 — 바퀴가 계속 돈다"
    assert data["cancelled_nav"] is True


def test_stop_is_still_accepted_not_an_error(link):
    """실패로 답하면 FMS 가 다리 실패로 받아 주문이 FAILED 로 떨어진다."""
    fleet_link, _fake, _ = link
    ok, status, data, msg = fleet_link._dispatch("stop", {})
    assert ok is True and status == 202 and msg == ""
    assert data["handled_by"] == "bt", "세션 처리는 여전히 follow_node 몫이다"


def test_stop_without_ros_does_not_raise(link):
    """ROS 브리지가 없을 때 멈춤 명령이 예외로 죽으면 그 뒤 명령도 다 막힌다."""
    fleet_link, fake, calls = link
    fake.active = False
    ok, status, data, _ = fleet_link._dispatch("stop", {})
    assert ok is True and status == 202
    assert calls["cancel"] == 0
    assert data["cancelled_nav"] is False


def test_other_bt_actions_do_not_cancel_nav(link):
    """`navigate` 는 BT 로 갔다가 `goal` 로 되돌아온다. 여기서 끊으면 방금 낸 주행을 죽인다."""
    fleet_link, _fake, calls = link
    for action in ("navigate", "guide", "follow_admin", "guide_watch", "watch", "follow_stop"):
        fleet_link._dispatch(action, {})
    assert calls["cancel"] == 0, "stop 이 아닌 BT 액션이 주행을 끊었다"


def test_stop_is_listed_as_a_bt_layer_action(link):
    """목록에서 빠지면 폴스루가 '알 수 없는 action' 실패를 내고, BT 드라이버가
    같은 id 로 그걸 먼저 집어 세션이 시작 즉시 끝난다(follow_admin 이 그랬다)."""
    fleet_link, _, _ = link
    assert "stop" in fleet_link.BT_LAYER_ACTIONS


# ── 세션 명령에는 답하지 않는다 ────────────────────────────────────────────────
#
# 실측 2026-07-28. `follow_admin` 을 BT_LAYER_ACTIONS 에 넣어 "알 수 없는 action" 실패를
# 없앴는데도 추종이 안 됐다. 실패가 **성공**으로 바뀌었을 뿐, 결과가 나가는 건 그대로라
# `FleetCmdDriver.poll()` 이 그걸 완료로 읽고 세션이 시작 즉시 끝났기 때문이다.
# 그 결과 `_pending_id` 가 비어 뒤이은 `stop()` 이 아무것도 안 보냈고, follow_node 의
# 세션은 영영 안 닫혔다(`/libi/follow_bt_snapshot` 이 Following[TRACKING] 을 계속 발행).

@pytest.mark.parametrize("action", ["follow_admin", "guide_watch", "watch"])
def test_session_start_gets_no_result(link, action):
    """이 파일의 두 번째 존재 이유. 답하면 그게 곧 '세션 끝'으로 읽힌다."""
    fleet_link, _, _ = link
    ok, _status, _data, _msg = fleet_link._dispatch(action, {})
    assert ok is True, "접수 자체는 성공이다"
    assert fleet_link.should_publish_result(action, ok) is False, (
        f"{action} 에 결과를 내면 FleetCmdDriver 가 완료로 읽어 세션이 즉시 끝난다")


@pytest.mark.parametrize("action", ["navigate", "guide", "stop", "follow_stop", "goal"])
def test_non_session_actions_still_get_a_result(link, action):
    """답을 막는 범위가 넓어지면 FMS 가 다리 실패로 받아 주문이 FAILED 로 떨어진다."""
    fleet_link, _, _ = link
    assert fleet_link.should_publish_result(action, True) is True


def test_failed_session_start_is_still_reported(link):
    """접수가 실패했으면 세션이 없다 — 답을 막으면 부른 쪽이 3600초 매달린다."""
    fleet_link, _, _ = link
    assert fleet_link.should_publish_result("follow_admin", False) is True
