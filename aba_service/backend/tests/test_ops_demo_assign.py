"""작업 지시 — 실물 로봇이 0 대일 때 시연용 배차를 화면에 덧그리는지.

로봇이 없으면 FMS 자동 배차 주체도 없어 주문이 「대기」에 영원히 남는다. 시연에서는
「배차됨 · libi-1」로 보여야 한다. 실물이 한 대라도 잡히면 이 갈래를 안 탄다는 것도 함께
검증한다 — 안 그러면 운영 화면이 있지도 않은 배차를 보여 준다.
"""

import pytest

from app.models import DemoRobotState
from app.routers import ops

TASKS = "/api/admin/ops/tasks"


@pytest.fixture(autouse=True)
def _clean_demo_assign():
    """모듈 전역이라 시험끼리 샌다 — 매번 비우고 시작한다."""
    ops._DEMO_ASSIGN.clear()
    yield
    ops._DEMO_ASSIGN.clear()


def _no_real_fleet(monkeypatch):
    monkeypatch.setattr(ops.fms_client, "fleet_snapshot", lambda: (True, {"robots": []}))
    monkeypatch.setattr(
        ops.fms_client, "submit_order", lambda **kw: (True, "t1")
    )


def test_로봇_없으면_수거는_데모_로봇에_배차되어_보인다(
    client, admin_auth, db_session, monkeypatch
):
    _no_real_fleet(monkeypatch)
    db_session.add(DemoRobotState(robot="libi-1", state="PATROL"))
    db_session.add(DemoRobotState(robot="libi-2", state="WORKING"))  # 일하는 중 → 건너뛴다
    db_session.commit()

    res = client.post(TASKS, json={"kind": "collect"}, headers=admin_auth)
    assert res.status_code == 201, res.text
    assert res.json()["assigned"] == "libi-1"

    monkeypatch.setattr(
        ops.fms_client,
        "list_orders",
        lambda: (True, [{"id": "t1", "status": "PENDING", "robot": None}]),
    )
    row = client.get(TASKS, headers=admin_auth).json()["orders"][0]
    assert (row["status"], row["robot"]) == ("ASSIGNED", "libi-1")


def test_실물_로봇이_있으면_가짜_배차를_안_붙인다(
    client, admin_auth, db_session, monkeypatch
):
    _no_real_fleet(monkeypatch)
    monkeypatch.setattr(
        ops.fms_client, "fleet_snapshot", lambda: (True, {"robots": [{"name": "pinky3"}]})
    )
    db_session.add(DemoRobotState(robot="libi-1", state="PATROL"))
    db_session.commit()

    res = client.post(TASKS, json={"kind": "collect"}, headers=admin_auth)
    assert res.json()["assigned"] is None

    monkeypatch.setattr(
        ops.fms_client,
        "list_orders",
        lambda: (True, [{"id": "t1", "status": "PENDING", "robot": None}]),
    )
    row = client.get(TASKS, headers=admin_auth).json()["orders"][0]
    assert (row["status"], row["robot"]) == ("PENDING", None)
