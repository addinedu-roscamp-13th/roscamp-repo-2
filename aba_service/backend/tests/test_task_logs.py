"""작업로그(`cb_task_logs`) 삭제 — 부활 버그 재현 + 수정.

## 왜 이 테스트가 있나
`GET /api/admin/ops/logs` 는 호출할 때마다 `_sync_logs()` 로 FMS 의 종료 작업을 task_id
기준으로 재수입한다(멱등 삽입). 단순 하드 DELETE 를 추가하면, 지운 행의 task_id 가 여전히
FMS 응답(`list_orders`)에 남아있는 한 다음 조회에서 "없는 task_id" 로 오인되어 그대로
부활한다. 이 파일의 핵심은 DELETE 후 다시 GET 했을 때 목록에 없는지(재수입 안 되는지)다 —
단순히 DELETE 응답만 보면 이 버그를 못 잡는다.
"""

from app.models import TaskLog
from app.routers import ops_extra

LOGS = "/api/admin/ops/logs"


class FakeOpsExtra:
    """`ops_extra` 가 보는 FMS 대역 — 종료된 주문 목록만 필요하다."""

    def __init__(self) -> None:
        self.orders: list[dict] = []

    def list_orders(self) -> tuple[bool, list[dict]]:
        return True, self.orders


def make_terminal_order(task_id="t-1", status="COMPLETED") -> dict:
    return {
        "id": task_id,
        "status": status,
        "requester": "사서:transfer",
        "robot": "libi-1",
        "leg_idx": 4,
        "leg_count": 4,
        "reason": None,
    }


def fake_ops(monkeypatch) -> FakeOpsExtra:
    fake = FakeOpsExtra()
    monkeypatch.setattr(ops_extra.fms_client, "list_orders", fake.list_orders)
    return fake


def test_종료작업이_sync로_적재되어_목록에_보인다(client, admin_auth, monkeypatch):
    fake = fake_ops(monkeypatch)
    fake.orders.append(make_terminal_order())

    res = client.get(LOGS, headers=admin_auth)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["task_id"] == "t-1"


def test_삭제한_로그는_다시_조회해도_부활하지_않는다(client, admin_auth, db_session, monkeypatch):
    """핵심 assertion: DELETE 이후 두 번째 GET 에서 목록에 없어야 한다.

    FMS 는 여전히 같은 종료 task(`t-1`)를 돌려준다 — 실제 운영에서도 FMS 가 그 task_id 를
    치우지 않는 한 계속 내려오므로, task_id 존재 여부만으로 재수입을 막는 방식이 hidden 삭제와
    충돌하지 않는지가 관건이다.
    """
    fake = fake_ops(monkeypatch)
    fake.orders.append(make_terminal_order())

    first = client.get(LOGS, headers=admin_auth)
    assert first.status_code == 200
    log_id = first.json()[0]["id"]

    delete_res = client.delete(f"{LOGS}/{log_id}", headers=admin_auth)
    assert delete_res.status_code == 200, delete_res.text

    second = client.get(LOGS, headers=admin_auth)
    assert second.status_code == 200
    assert second.json() == []  # 부활하지 않음 — 이게 없으면 버그를 못 잡는다

    # 감사 목적 보존 — DB 에는 hidden=True 로 행이 남아있어야 한다(하드 삭제 아님).
    row = db_session.get(TaskLog, log_id)
    assert row is not None
    assert row.hidden is True


def test_없는_로그를_삭제하면_404(client, admin_auth):
    res = client.delete(f"{LOGS}/999999", headers=admin_auth)
    assert res.status_code == 404


ALERTS = "/api/admin/ops/alerts"


def test_삭제한_로그는_알림에도_안_보인다(client, admin_auth, monkeypatch):
    """`/logs` 뿐 아니라 `/alerts` 도 hidden 을 걸러야 한다 — 방금 지운 작업 로그가

    알림 목록에 그대로 남아 있으면 사서가 이미 정리한 걸 또 처리하려 든다.
    """
    fake = fake_ops(monkeypatch)
    fake.orders.append(make_terminal_order())

    first = client.get(LOGS, headers=admin_auth)
    log_id = first.json()[0]["id"]

    client.delete(f"{LOGS}/{log_id}", headers=admin_auth)

    res = client.get(ALERTS, headers=admin_auth)
    assert res.status_code == 200, res.text
    assert res.json()["tasks"] == []
