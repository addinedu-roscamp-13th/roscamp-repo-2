"""세션 수명 — 역할별 카메라, id 대조 종료, lease 만료."""
from libi_perception.session import SessionManager, target_session_id


def test_camera_by_role():
    m = SessionManager(lease_sec=60)
    m.start("a", "follow", now=0.0)
    assert m.camera_for() == "front"
    m.stop("a")
    m.start("b", "guide", now=1.0)
    assert m.camera_for() == "back"


def test_watch_takes_camera_from_args():
    """등록 화면은 앞캠이다 — 이용자가 패널 앞에 서 있기 때문이다."""
    m = SessionManager(lease_sec=60)
    m.start("w", "watch", now=0.0, camera="front")
    assert m.camera_for() == "front"


def test_watch_without_camera_is_none():
    """역할에 정위치 캠이 없고 인자도 없으면 켜지 않는다."""
    m = SessionManager(lease_sec=60)
    m.start("w", "watch", now=0.0)
    assert m.camera_for() == "none"


def test_no_session_is_none():
    assert SessionManager(lease_sec=60).camera_for() == "none"


def test_stop_requires_matching_id():
    """패널의 watch 종료가 관리자 추종을 끊으면 안 된다."""
    m = SessionManager(lease_sec=60)
    m.start("follow-1", "follow", now=0.0)
    assert m.stop("watch-9") is False
    assert m.camera_for() == "front"
    assert m.stop("follow-1") is True
    assert m.camera_for() == "none"


def test_stop_on_empty_manager_is_false():
    assert SessionManager(lease_sec=60).stop("anything") is False


def test_lease_expiry_closes_session():
    """패널이 죽어 stop 이 안 와도 스스로 닫힌다."""
    m = SessionManager(lease_sec=10)
    m.start("w", "watch", now=0.0, camera="back")
    assert m.expired(now=11.0) is True
    assert m.sweep(now=11.0) is True
    assert m.camera_for() == "none"


def test_lease_zero_never_expires():
    m = SessionManager(lease_sec=0)
    m.start("w", "watch", now=0.0, camera="back")
    assert m.expired(now=10_000.0) is False


def test_new_session_replaces_old():
    m = SessionManager(lease_sec=60)
    m.start("a", "follow", now=0.0)
    m.start("b", "guide", now=1.0)
    assert m.camera_for() == "back"
    assert m.stop("a") is False           # 이미 대체됐다


def test_only_follow_drives():
    """watch·guide 는 눈만 된다 — 제어 루프를 켜면 nav2 와 /cmd_vel 을 다툰다."""
    m = SessionManager(lease_sec=60)
    m.start("a", "follow", now=0.0)
    assert m.driving is True
    m.start("b", "guide", now=0.0)
    assert m.driving is False
    m.start("c", "watch", now=0.0, camera="front")
    assert m.driving is False


def test_override_camera_keeps_the_role():
    """회복 BT 가 탐색 중 반대 캠을 본다. 역할은 그대로다."""
    m = SessionManager(lease_sec=60)
    m.start("a", "follow", now=0.0)
    m.override_camera("back")
    assert m.camera_for() == "back"
    assert m.role == "follow"
    m.override_camera("front")          # 되돌리는 것도 같은 함수다
    assert m.camera_for() == "front"


def test_restarting_the_same_session_renews_the_lease():
    """패널은 같은 watch 를 주기적으로 재발행해 살아 있음을 알린다."""
    m = SessionManager(lease_sec=10)
    m.start("w", "watch", now=0.0, camera="back")
    m.start("w", "watch", now=8.0, camera="back")
    assert m.expired(now=15.0) is False


def test_override_without_session_is_ignored():
    m = SessionManager(lease_sec=60)
    m.override_camera("back")
    assert m.camera_for() == "none"


# ── stop id 규약 ─────────────────────────────────────────────────────────────

def test_stop_prefix_is_stripped():
    """FleetCmdDriver.stop() 은 원 id 가 아니라 `stop-<원래id>` 를 보낸다.
    그대로 비교하면 BT 가 연 세션이 영영 안 닫힌다."""
    assert target_session_id("stop-abc123") == "abc123"


def test_plain_id_passes_through():
    assert target_session_id("abc123") == "abc123"


def test_explicit_session_id_wins():
    assert target_session_id("stop-abc123", {"session_id": "panel-7"}) == "panel-7"


def test_missing_id_is_empty_string():
    assert target_session_id(None) == ""
