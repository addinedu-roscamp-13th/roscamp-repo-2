"""/api/admin/ops/robots — FMS 로봇이 하나도 없을 때 DemoRobotState 로 대체하는지.

/dashboard 가 이미 쓰는 것과 같은 fallback 을 여기도 추가한다(실시간 모니터링
페이지가 데모 환경에서 빈 화면으로 보이지 않게). 진짜 텔레메트리가 하나라도
있으면 이 분기를 절대 타지 않는다는 것도 함께 검증한다.
"""

from app.models import DemoRobotState
from app.routers import ops

ROBOTS = "/api/admin/ops/robots"


def test_fms_로봇_없으면_데모_상태로_대체(client, admin_auth, db_session, monkeypatch):
    monkeypatch.setattr(
        ops.fms_client, "fleet_snapshot", lambda: (True, {"robots": [], "plugins": {}})
    )
    db_session.add(DemoRobotState(robot="pinky1", state="PATROL"))
    db_session.add(DemoRobotState(robot="arm1", state="ERROR"))
    db_session.commit()

    res = client.get(ROBOTS, headers=admin_auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["linked"] is True
    by_name = {r["name"]: r for r in body["robots"]}
    assert set(by_name) == {"pinky1", "arm1"}
    assert by_name["pinky1"]["state"] == "PATROL"
    assert by_name["pinky1"]["busy"] is False
    assert by_name["arm1"]["state"] == "ERROR"
    assert by_name["arm1"]["x"] is None
    assert by_name["arm1"]["task_id"] == ""


def test_fms_연결_끊겨도_로봇_없으면_데모로_채우되_linked는_false(
    client, admin_auth, db_session, monkeypatch
):
    monkeypatch.setattr(ops.fms_client, "fleet_snapshot", lambda: (False, {}))
    db_session.add(DemoRobotState(robot="pinky1", state="PATROL"))
    db_session.commit()

    res = client.get(ROBOTS, headers=admin_auth)
    body = res.json()
    assert body["linked"] is False
    assert [r["name"] for r in body["robots"]] == ["pinky1"]


def test_fms_실제_로봇_있으면_데모_안_섞는다(client, admin_auth, db_session, monkeypatch):
    real = [
        {
            "name": "pinky2",
            "x": 1.0,
            "y": 2.0,
            "state": "WORKING",
            "battery": 80,
            "busy": True,
            "stale": False,
            "task_id": "t-1",
            "task_state": "EXECUTING",
            "progress": 0.4,
            "goal_vertex": 3,
        }
    ]
    monkeypatch.setattr(
        ops.fms_client, "fleet_snapshot", lambda: (True, {"robots": real, "plugins": {}})
    )
    db_session.add(DemoRobotState(robot="pinky1", state="PATROL"))
    db_session.commit()

    res = client.get(ROBOTS, headers=admin_auth)
    body = res.json()
    assert [r["name"] for r in body["robots"]] == ["pinky2"]


def test_로그인_없이_조회_불가(client):
    res = client.get(ROBOTS)
    assert res.status_code == 401
