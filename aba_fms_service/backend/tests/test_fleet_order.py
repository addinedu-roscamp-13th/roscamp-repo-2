"""fleet_order 라우터 — 주문 접수·큐·배차·force-advance·취소를 HTTP 로 검증.

get_current_admin 을 오버라이드해 인증을 건너뛰고, orchestrator dispatch 는 cmd_id 를
기록하는 페이크로 바꾼다(로봇 없이 시퀀스 확인 = 디버그 목적)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import fleet_orchestrator_service as svc
from app.deps import get_current_admin
from app.routers import fleet_order


class _Dispatch:
    def __init__(self):
        self.calls = []
        self._n = 0

    def __call__(self, task_id, robot, leg):
        self._n += 1
        self.calls.append((task_id, robot, leg.type.value))
        return f"c{self._n}"


@pytest.fixture
def dispatch():
    return _Dispatch()


@pytest.fixture
def client(dispatch):
    svc.reset(dispatch)                       # 매 테스트 새 orchestrator + 페이크 dispatch
    app = FastAPI()
    app.include_router(fleet_order.router)
    app.dependency_overrides[get_current_admin] = lambda: object()   # 인증 우회
    return TestClient(app)


def _order(client, book="B1", pickup="7", dropoff="3"):
    return client.post("/api/fleet/order",
                       json={"book": book, "pickup": pickup, "dropoff": dropoff}).json()


def test_order_lifecycle_over_http(client, dispatch):
    # 접수 → 대기 큐에 뜸
    tid = _order(client)["task_id"]
    pending = client.get("/api/fleet/orders/pending").json()["pending"]
    assert [p["id"] for p in pending] == [tid]

    # 수동 배차 → EXECUTING, 첫 다리(navigate) dispatch
    r = client.post(f"/api/fleet/order/{tid}/assign", json={"robot": "pinky3"}).json()
    assert r["task"]["status"] == "EXECUTING"
    assert dispatch.calls == [(tid, "pinky3", "navigate")]

    # force-advance 4번 → COMPLETED (로봇 없이 시퀀스)
    for _ in range(4):
        t = client.get("/api/fleet/orders").json()["orders"][0]
        if t["status"] == "EXECUTING":
            client.post(f"/api/fleet/order/{tid}/advance")
    assert client.get("/api/fleet/orders").json()["orders"][0]["status"] == "COMPLETED"
    assert [c[2] for c in dispatch.calls] == [
        "navigate", "perform_action", "navigate", "perform_action"]


def test_result_endpoint_advances(client, dispatch):
    tid = _order(client)["task_id"]
    client.post(f"/api/fleet/order/{tid}/assign", json={"robot": "pinky3"})
    client.post("/api/fleet/order/result", json={"cmd_id": "c1", "ok": True})  # navigate 완료
    assert dispatch.calls[-1][2] == "perform_action"                          # pick 나감


def test_cancel_over_http(client):
    tid = _order(client)["task_id"]
    r = client.post(f"/api/fleet/order/{tid}/cancel").json()
    assert r["task"]["status"] == "CANCELLED"


def test_assign_unknown_order_404(client):
    r = client.post("/api/fleet/order/nope/assign", json={"robot": "pinky3"})
    assert r.status_code == 404


def test_advance_non_executing_409(client):
    tid = _order(client)["task_id"]              # PENDING
    r = client.post(f"/api/fleet/order/{tid}/advance")
    assert r.status_code == 409


def test_double_assign_409(client):
    tid = _order(client)["task_id"]
    client.post(f"/api/fleet/order/{tid}/assign", json={"robot": "pinky3"})
    r = client.post(f"/api/fleet/order/{tid}/assign", json={"robot": "pinky1"})
    assert r.status_code == 409


# ── 주문 종류별 다리 구성 (R14) ──────────────────────────────────────────────
# 「정리」·「파견」은 집고 놓을 물건이 없다. 배달 4다리로 내보내면 있지도 않은 책을
# 집으러 간다 — 여기서 다리 개수를 못박는다.


def test_navigate_order_has_one_leg(client, dispatch):
    r = client.post("/api/fleet/order",
                    json={"kind": "navigate", "dropoff": "문학-1"})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    task = client.get("/api/fleet/orders").json()["orders"][0]
    assert task["task_type"] == "navigate"
    assert task["leg_count"] == 1

    client.post(f"/api/fleet/order/{tid}/assign", json={"robot": "pinky3"})
    assert [c[2] for c in dispatch.calls] == ["navigate"]

    # 한 다리를 마치면 곧바로 완료 — 집기/놓기를 기다리지 않는다.
    client.post(f"/api/fleet/order/{tid}/advance")
    assert client.get("/api/fleet/orders").json()["orders"][0]["status"] == "COMPLETED"


def test_navigate_order_needs_no_book_or_pickup(client):
    assert client.post("/api/fleet/order",
                       json={"kind": "navigate", "dropoff": "주차장"}).status_code == 200


def test_delivery_is_still_the_default_kind(client):
    tid = _order(client)["task_id"]
    task = client.get("/api/fleet/orders").json()["orders"][0]
    assert task["id"] == tid
    assert task["task_type"] == "delivery"
    assert task["leg_count"] == 4


def test_delivery_without_book_is_still_422(client):
    r = client.post("/api/fleet/order", json={"pickup": "7", "dropoff": "3"})
    assert r.status_code == 422


def test_order_without_dropoff_is_rejected(client):
    r = client.post("/api/fleet/order", json={"kind": "navigate", "dropoff": ""})
    assert r.status_code == 422


def test_unknown_kind_is_rejected(client):
    r = client.post("/api/fleet/order",
                    json={"kind": "patrol", "dropoff": "복도-1"})
    assert r.status_code == 422
