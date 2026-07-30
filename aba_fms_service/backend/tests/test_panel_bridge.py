"""패널 ROS2 통로의 봉투 계약.

여기서 지키려는 것은 **답이 반드시 돌아온다**는 것 하나다. 패널은 응답이 안 오면
타임아웃까지 "요청 중" 화면에 갇히고 사유도 안 보인다 — 그래서 알 수 없는 op 도,
핸들러가 터진 경우도 `ok:false` + 사유로 회신해야 한다.

`id` 는 패널이 상관에 쓰는 키다(FMS 는 판단하지 않고 그대로 돌려준다). 이게 빠지면
패널의 콜백이 영영 매칭되지 않는다.
"""
import pytest

from app import fsm_link, panel_bridge


@pytest.fixture(autouse=True)
def clean_cache():
    with fsm_link._lock:
        fsm_link._cache.clear()
    yield
    with fsm_link._lock:
        fsm_link._cache.clear()


def test_unknown_op_still_answers():
    res = panel_bridge.handle({"id": "abc", "op": "nope.whatever"})
    assert res["id"] == "abc"
    assert res["ok"] is False
    assert "nope.whatever" in res["reason"]


def test_bad_args_answer_instead_of_raising():
    """robot_id 가 빠진 요청 — 모델 생성이 터지지만 예외가 밖으로 나가면 안 된다."""
    res = panel_bridge.handle({"id": "xyz", "op": "panel.transition"})
    assert res["id"] == "xyz"
    assert res["ok"] is False
    assert res["reason"]


def test_panel_transition_rejects_disallowed_state():
    """실제 핸들러까지 도달하는지 확인. CHARGING 은 관제 소관이라 패널이 못 부른다.

    이 거절은 fsm_link 에 닿기 전에 끝나므로 ROS·브릿지 없이 돈다."""
    res = panel_bridge.handle({
        "id": "1", "op": "panel.transition",
        "robot_id": "pinky1", "target_state": "CHARGING",
    })
    assert res["ok"] is True          # 통로는 성공 — 안에서 거절된 것
    assert res["accepted"] is False
    assert "CHARGING" in res["reason"]


def test_envelope_wins_over_handler_body():
    """핸들러 응답에 ok/id 가 섞여 있어도 상관 키를 덮어쓰지 못한다."""
    res = panel_bridge.handle({
        "id": "keep-me", "op": "panel.transition",
        "robot_id": "pinky1", "target_state": "BOGUS",
    })
    assert res["id"] == "keep-me"


def test_ops_table_matches_http_routes():
    """op 이름과 HTTP 경로가 어긋나면 폴백과 주경로가 다른 일을 하게 된다."""
    ops = panel_bridge._ops()
    assert set(ops) == {
        "guide.request", "guide.release",
        "follow.request", "follow.release",
        "panel.transition",
    }
