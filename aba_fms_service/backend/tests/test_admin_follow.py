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
    assert "이미 추종 중" in body["reason"]


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
