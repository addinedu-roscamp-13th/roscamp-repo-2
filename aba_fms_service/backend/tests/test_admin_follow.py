"""Admin-follow request policy. State comes from the fsm_link cache, so these tests seed
that cache rather than a private one — same source of truth the endpoint reads.

The grant store IS private to this router, and deliberately so: a following robot still
reports IDLE/PATROL over FSM (the follow loop runs ai_service↔robot direct, outside the BT),
so nothing else in FMS can answer "is this robot following". See the module docstring."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import fsm_link
from app.routers import admin_follow


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(admin_follow.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_cache():
    """Both stores must be cleared — a grant left behind makes the NEXT test's request
    fail with '이미 추종 중', which reads as a policy bug rather than test bleed."""
    def _clear():
        with fsm_link._lock:
            fsm_link._cache.clear()
        with admin_follow._grants_lock:
            admin_follow._grants.clear()

    _clear()
    yield
    _clear()


class FakeLink:
    """Stands in for the mission-PC FSM link, which needs ROS2 and a live bridge.

    Records what was asked for so tests can assert the robot is actually driven to
    WORKING — approving without moving the robot is the bug this whole path exists to
    avoid. Set `result` to None to simulate the bridge being down."""

    def __init__(self, result=None):
        self.result = result if result is not None else {"accepted": True, "current_state": "", "reason": ""}
        self.calls = []

    def __call__(self, robot_id, target_state, force=False, timeout=None):
        self.calls.append((robot_id, target_state))
        return self.result

    @property
    def targets(self):
        return [target for _, target in self.calls]


@pytest.fixture(autouse=True)
def link(monkeypatch):
    """autouse — 실제 링크를 그대로 두면 브릿지가 없어 모든 승인이 거부되고, 정책 테스트가
    통째로 무의미해진다. 링크 장애를 보고 싶은 테스트는 fake.result = None 으로 바꾼다."""
    fake = FakeLink()
    monkeypatch.setattr(fsm_link, "request_transition", fake)
    return fake


def _seed(robot_id, state, fresh=True):
    entry = fsm_link._empty_entry()
    entry["current_state"] = state
    entry["_last_ros_at"] = time.time() if fresh else 0.0
    with fsm_link._lock:
        fsm_link._cache[robot_id] = entry


def _post(client, robot_id="pinky1"):
    return client.post("/api/robot/admin-follow/request", json={"robot_id": robot_id}).json()


def _release(client, robot_id="pinky1"):
    return client.post("/api/robot/admin-follow/release", json={"robot_id": robot_id}).json()


def _status(client):
    return client.get("/api/robot/admin-follow/status").json()


# ── 승인 정책 ────────────────────────────────────────────────────────────────

def test_accepts_from_idle(client):
    _seed("pinky1", "IDLE")
    body = _post(client)
    assert body["accepted"] is True
    assert body["command"] == "follow_admin"


def test_accepts_from_patrol(client):
    _seed("pinky1", "PATROL")
    assert _post(client)["accepted"] is True


def test_rejects_when_robot_in_error(client):
    _seed("pinky1", "ERROR")
    body = _post(client)
    assert body["accepted"] is False
    assert "ERROR" in body["reason"]


def test_rejects_while_already_working(client):
    """A robot mid-task must not be hijacked into following."""
    _seed("pinky1", "WORKING")
    body = _post(client)
    assert body["accepted"] is False
    assert body["command"] is None


def test_rejects_unknown_robot(client):
    body = _post(client, "nope")
    assert body["accepted"] is False
    assert "nope" in body["reason"]


def test_rejects_when_state_feed_is_stale(client):
    """Accepting on a stale cache would dispatch a follow at a robot that may be gone."""
    _seed("pinky1", "IDLE", fresh=False)
    body = _post(client)
    assert body["accepted"] is False
    assert body["reason"]


def test_rejects_when_state_unknown(client):
    _seed("pinky1", None)
    assert _post(client)["accepted"] is False


def test_rejected_request_leaves_no_grant(client):
    """A rejection must not look like an active follow to 관제."""
    _seed("pinky1", "ERROR")
    _post(client)
    assert _status(client)["following"] == []


# ── 승인 기록(grant) ─────────────────────────────────────────────────────────

def test_status_is_empty_before_any_request(client):
    assert _status(client)["following"] == []


def test_accepted_request_shows_up_in_status(client):
    """FSM 은 계속 IDLE 이므로, 이 기록이 없으면 관제는 추종 중인 걸 알 방법이 없다."""
    _seed("pinky1", "IDLE")
    _post(client)
    following = _status(client)["following"]
    assert [g["robot_id"] for g in following] == ["pinky1"]
    assert following[0]["granted_at"] > 0
    assert following[0]["state_stale"] is False


def test_second_request_while_following_is_rejected(client):
    """FSM 상태만으로는 못 거른다 — 추종 중에도 IDLE 로 보이기 때문."""
    _seed("pinky1", "IDLE")
    assert _post(client)["accepted"] is True
    body = _post(client)
    assert body["accepted"] is False
    # GUI 도 로컬 상태로 같은 상황을 막고, 이 문구를 그대로 화면에 띄운다. 둘이 같으면
    # 어느 쪽이 막은 건지 화면만 보고 알 수 없다 — 실제로 그것 때문에 원인을 못 찾았다.
    assert "관제" in body["reason"]


def test_grants_are_tracked_per_robot(client):
    _seed("pinky1", "IDLE")
    _seed("pinky2", "PATROL")
    _post(client, "pinky1")
    _post(client, "pinky2")
    assert {g["robot_id"] for g in _status(client)["following"]} == {"pinky1", "pinky2"}


def test_release_clears_the_grant(client):
    _seed("pinky1", "IDLE")
    _post(client)
    assert _release(client)["released"] is True
    assert _status(client)["following"] == []


def test_release_without_a_grant_is_not_an_error(client):
    """GUI 는 FMS 응답과 무관하게 로컬 추종을 멈춘다. 여기서 막으면 기록만 더 어긋난다."""
    body = _release(client)
    assert body["released"] is False
    assert body["reason"]


def test_can_follow_again_after_release(client):
    _seed("pinky1", "IDLE")
    _post(client)
    _release(client)
    assert _post(client)["accepted"] is True


def test_release_only_affects_the_named_robot(client):
    _seed("pinky1", "IDLE")
    _seed("pinky2", "IDLE")
    _post(client, "pinky1")
    _post(client, "pinky2")
    _release(client, "pinky1")
    assert [g["robot_id"] for g in _status(client)["following"]] == ["pinky2"]


def test_status_flags_a_grant_whose_robot_went_silent(client):
    """추종 승인은 살아있는데 로봇 상태가 끊긴 경우 — 관제가 구분할 수 있어야 한다."""
    _seed("pinky1", "IDLE")
    _post(client)
    _seed("pinky1", "IDLE", fresh=False)      # 같은 로봇, 수신만 끊김
    following = _status(client)["following"]
    assert following[0]["robot_id"] == "pinky1"
    assert following[0]["state_stale"] is True


def test_status_flags_a_grant_whose_robot_vanished(client):
    """캐시에서 아예 사라진 경우도 stale 로 본다 (snapshot None)."""
    _seed("pinky1", "IDLE")
    _post(client)
    with fsm_link._lock:
        fsm_link._cache.clear()
    assert _status(client)["following"][0]["state_stale"] is True


# ── WORKING 전이 ─────────────────────────────────────────────────────────────

def test_approval_moves_the_robot_to_working(client, link):
    """승인만 하고 로봇을 안 옮기면 관제가 유휴로 보고 다른 태스크를 배차한다."""
    _seed("pinky1", "IDLE")
    assert _post(client)["accepted"] is True
    assert link.calls == [("pinky1", "WORKING")]


def test_rejected_request_does_not_touch_the_robot_state(client, link):
    _seed("pinky1", "ERROR")
    _post(client)
    assert link.calls == []


def test_request_fails_when_the_fsm_link_is_down(client, link):
    """브릿지가 없으면 로봇은 IDLE 로 남는다 — 승인만 내주면 기록과 실제가 어긋난다."""
    link.result = None
    _seed("pinky1", "IDLE")
    body = _post(client)
    assert body["accepted"] is False
    assert "링크" in body["reason"]


def test_failed_transition_rolls_back_the_grant(client, link):
    """승인을 물렀으면 기록도 남으면 안 된다 — 남으면 재시도가 '이미 추종 중'으로 막힌다."""
    link.result = None
    _seed("pinky1", "IDLE")
    _post(client)
    assert _status(client)["following"] == []


def test_can_retry_after_a_link_failure(client, link):
    link.result = None
    _seed("pinky1", "IDLE")
    assert _post(client)["accepted"] is False
    link.result = {"accepted": True, "current_state": "", "reason": ""}
    assert _post(client)["accepted"] is True


def test_rejected_transition_surfaces_the_robot_reason(client, link):
    link.result = {"accepted": False, "current_state": "IDLE", "reason": "로봇이 거부함"}
    _seed("pinky1", "IDLE")
    assert _post(client)["reason"] == "로봇이 거부함"


def test_release_returns_a_working_robot_to_idle(client, link):
    _seed("pinky1", "IDLE")
    _post(client)
    _seed("pinky1", "WORKING")          # 승인 후 로봇이 실제로 WORKING 이 된 상태
    assert _release(client)["released"] is True
    assert link.targets == ["WORKING", "IDLE"]


@pytest.mark.parametrize("state", ["ERROR", "RETURNING", "CHARGING"])
def test_release_leaves_a_robot_that_left_working_on_its_own(client, link, state):
    """에러로 빠졌거나 배터리 때문에 알아서 복귀·충전 중인 로봇을 끌어내면 안 된다."""
    _seed("pinky1", "IDLE")
    _post(client)
    _seed("pinky1", state)
    assert _release(client)["released"] is True
    assert link.targets == ["WORKING"], f"{state} 상태에는 복귀 전이를 걸지 않는다"


def test_release_returns_the_robot_even_if_the_cache_still_says_idle(client, link):
    """캐시는 로봇->브릿지 지연만큼 뒤처진다. 승인 직후 바로 해제하면 아직 IDLE 로 보이는데,
    'WORKING 일 때만 되돌린다'로 판단하면 복귀를 건너뛰어 로봇이 WORKING 에 갇힌다."""
    _seed("pinky1", "IDLE")
    _post(client)                        # WORKING 으로 옮겼지만 캐시는 아직 IDLE
    _release(client)
    assert link.targets == ["WORKING", "IDLE"]


def test_release_still_succeeds_when_the_return_transition_fails(client, link):
    """기록을 남겨두면 다시 추종을 시작할 수 없게 된다 — 상태가 안 돌아간 것보다 나쁘다."""
    _seed("pinky1", "IDLE")
    _post(client)
    _seed("pinky1", "WORKING")
    link.result = None
    body = _release(client)
    assert body["released"] is True
    assert "복귀 실패" in body["reason"]
    assert _status(client)["following"] == []
