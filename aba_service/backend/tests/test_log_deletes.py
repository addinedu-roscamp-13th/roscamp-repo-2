"""Task 4 — 로봇제어로그·침입이벤트 하드 삭제.

배경: `cb_robot_control_logs`/`cb_intrusion_events` 둘 다 FMS 재동기화 대상이 아닌
순수 로그라 Task 3(작업로그) 같은 soft-delete/부활방지 장치가 필요 없다 — 바로 hard DELETE.
"""

from app.models import RobotControlLog

ROBOT_LOGS = "/api/robot/history"
SECURITY_EVENTS = "/api/admin/ops/security/events"


def _make_control_log(db_session) -> int:
    row = RobotControlLog(
        user_message="정지해",
        robot_type="mobile",
        action="stop",
        status="success",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row.id


# ── 로봇제어로그 (백엔드 전용, UI 연결 없음) ──────────────────────────────────


def test_로봇제어로그_삭제_성공(client, admin_auth, db_session):
    log_id = _make_control_log(db_session)

    res = client.delete(f"{ROBOT_LOGS}/{log_id}", headers=admin_auth)
    assert res.status_code == 200, res.text

    rows = client.get(ROBOT_LOGS, headers=admin_auth).json()
    assert [r["id"] for r in rows] == []


def test_로봇제어로그_재삭제시_404(client, admin_auth, db_session):
    log_id = _make_control_log(db_session)
    client.delete(f"{ROBOT_LOGS}/{log_id}", headers=admin_auth)

    res = client.delete(f"{ROBOT_LOGS}/{log_id}", headers=admin_auth)
    assert res.status_code == 404


def test_없는_로봇제어로그_삭제하면_404(client, admin_auth):
    res = client.delete(f"{ROBOT_LOGS}/9999", headers=admin_auth)
    assert res.status_code == 404


def test_로그인_없이_로봇제어로그_삭제_불가(client, db_session):
    log_id = _make_control_log(db_session)
    res = client.delete(f"{ROBOT_LOGS}/{log_id}")
    assert res.status_code == 401


# ── 침입이벤트 (security.tsx 에서 표시, UI 연결은 Task 8/12) ──────────────────


def test_침입이벤트_삭제_성공(client, admin_auth):
    created = client.post(SECURITY_EVENTS, json={"source": "libi-1", "zone": "1층"})
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]

    res = client.delete(f"{SECURITY_EVENTS}/{event_id}", headers=admin_auth)
    assert res.status_code == 200, res.text

    rows = client.get("/api/admin/ops/security", headers=admin_auth).json()["events"]
    assert [e["id"] for e in rows] == []


def test_침입이벤트_재삭제시_404(client, admin_auth):
    created = client.post(SECURITY_EVENTS, json={"source": "libi-1"})
    event_id = created.json()["id"]
    client.delete(f"{SECURITY_EVENTS}/{event_id}", headers=admin_auth)

    res = client.delete(f"{SECURITY_EVENTS}/{event_id}", headers=admin_auth)
    assert res.status_code == 404


def test_없는_침입이벤트_삭제하면_404(client, admin_auth):
    res = client.delete(f"{SECURITY_EVENTS}/9999", headers=admin_auth)
    assert res.status_code == 404


def test_로그인_없이_침입이벤트_삭제_불가(client, admin_auth):
    created = client.post(SECURITY_EVENTS, json={"source": "libi-1"})
    event_id = created.json()["id"]
    res = client.delete(f"{SECURITY_EVENTS}/{event_id}")
    assert res.status_code == 401
