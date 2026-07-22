"""R13 — 검색/요청 분리 + 대여 승인 절차.

못박는 것(요구사항 R13-3 · R13-4):
1. 대여 신청은 **FMS 를 부르지 않는다** — 승인 대기 기록만 남는다.
2. 사서가 승인해야 그때 FMS 주문이 나가고 task_id 가 붙는다.
3. 반려하면 주문이 안 나가고 회원 화면에 반려로 보인다.
4. 대여 중인 책은 신청 단계에서 막힌다(409).
5. 열람(자리로 받기)은 **승인 없이** 지금처럼 바로 나간다.
"""

from app.models import DeliveryRequest
from tests.conftest import make_book

READ = "/api/member/request/read"
BORROW = "/api/member/request/borrow"
MY = "/api/member/requests"
APPROVALS = "/api/admin/ops/approvals"


# ── 열람: 승인 불필요 ────────────────────────────────────────────────────────


def test_열람_요청은_승인없이_바로_FMS_주문(client, member_auth, book, fms):
    res = client.post(
        READ, json={"book_id": book.id, "table": "테이블-1번-좌"}, headers=member_auth
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["approval"] == "APPROVED"
    assert body["fms_task_id"] == "t-1"
    assert fms.submit_count == 1
    assert fms.orders[0]["dropoff"] == "테이블-1번-좌"


def test_열람_자리는_화이트리스트_밖이면_거절(client, member_auth, book, fms):
    res = client.post(
        READ, json={"book_id": book.id, "table": "옥상"}, headers=member_auth
    )
    assert res.status_code == 400
    assert fms.submit_count == 0


def test_열람도_FMS가_못받으면_기록을_남기지_않는다(
    client, member_auth, book, fms, db_session
):
    fms.ok = False
    res = client.post(
        READ, json={"book_id": book.id, "table": "테이블-1번-좌"}, headers=member_auth
    )
    assert res.status_code == 503
    assert db_session.query(DeliveryRequest).count() == 0


# ── 대여: 승인 필요 ──────────────────────────────────────────────────────────


def test_대여_신청은_FMS를_부르지_않는다(client, member_auth, book, fms, db_session):
    res = client.post(BORROW, json={"book_id": book.id}, headers=member_auth)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["approval"] == "PENDING_APPROVAL"
    assert body["fms_task_id"] == ""
    # ★ 핵심: 관제에 주문이 생기면 안 된다.
    assert fms.submit_count == 0

    row = db_session.query(DeliveryRequest).one()
    assert row.approval == "PENDING_APPROVAL"
    assert row.fms_task_id == ""
    assert row.dropoff == "안네데스크"


def test_대여_신청은_FMS가_죽어_있어도_접수된다(client, member_auth, book, fms):
    """승인 대기는 아직 주문이 아니므로 FMS 상태와 무관해야 한다."""
    fms.ok = False
    res = client.post(BORROW, json={"book_id": book.id}, headers=member_auth)
    assert res.status_code == 201
    assert res.json()["approval"] == "PENDING_APPROVAL"


def test_대여중_도서는_신청_단계에서_막힌다(client, member_auth, db_session, fms):
    out = make_book(db_session, title="대출된 책", in_stock=False)
    res = client.post(BORROW, json={"book_id": out.id}, headers=member_auth)
    assert res.status_code == 409
    assert "예약" in res.json()["detail"]
    assert fms.submit_count == 0


def test_서가위치_없는_도서는_요청할_수_없다(client, member_auth, db_session, fms):
    nowhere = make_book(db_session, title="위치 미상", zone="")
    res = client.post(BORROW, json={"book_id": nowhere.id}, headers=member_auth)
    assert res.status_code == 409
    assert fms.submit_count == 0


def test_없는_도서는_404(client, member_auth, fms):
    res = client.post(BORROW, json={"book_id": 9999}, headers=member_auth)
    assert res.status_code == 404
    assert fms.submit_count == 0


def test_로그인_없이는_요청할_수_없다(client, book, fms):
    assert client.post(BORROW, json={"book_id": book.id}).status_code == 401
    assert fms.submit_count == 0


# ── 사서 승인/반려 ───────────────────────────────────────────────────────────


def _pending_id(client, member_auth, book) -> int:
    res = client.post(BORROW, json={"book_id": book.id}, headers=member_auth)
    assert res.status_code == 201
    return res.json()["id"]


def test_승인_대기_목록에_대여_신청이_보인다(client, member_auth, admin_auth, book, fms):
    req_id = _pending_id(client, member_auth, book)
    res = client.get(APPROVALS, headers=admin_auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [r["id"] for r in body["pending"]] == [req_id]
    assert body["pending"][0]["member_username"] == "hong"
    assert body["pending"][0]["book_title"] == "어린 왕자"
    assert body["recent"] == []


def test_열람_요청은_승인_대기_목록에_안_올라온다(
    client, member_auth, admin_auth, book, fms
):
    client.post(
        READ, json={"book_id": book.id, "table": "테이블-1번-좌"}, headers=member_auth
    )
    res = client.get(APPROVALS, headers=admin_auth)
    assert res.json()["pending"] == []


def test_승인하면_그때_FMS_주문이_생긴다(
    client, member_auth, admin_auth, book, fms, db_session
):
    req_id = _pending_id(client, member_auth, book)
    assert fms.submit_count == 0

    res = client.post(f"{APPROVALS}/{req_id}/approve", headers=admin_auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approval"] == "APPROVED"
    assert body["fms_task_id"] == "t-1"
    assert body["decided_by"] == "사서1"

    # ★ 핵심: 승인 시점에 정확히 한 번 나간다.
    assert fms.submit_count == 1
    order = fms.orders[0]
    assert order["book"] == "어린 왕자"
    assert order["pickup"] == "문학-1"
    assert order["dropoff"] == "안네데스크"
    assert order["requester"] == "hong"

    db_session.expire_all()
    assert db_session.get(DeliveryRequest, req_id).approval == "APPROVED"


def test_반려하면_주문이_안_생기고_사유가_남는다(
    client, member_auth, admin_auth, book, fms
):
    req_id = _pending_id(client, member_auth, book)
    res = client.post(
        f"{APPROVALS}/{req_id}/reject",
        json={"reason": "연체 도서가 있습니다"},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text
    assert res.json()["approval"] == "REJECTED"
    assert res.json()["reject_reason"] == "연체 도서가 있습니다"
    assert fms.submit_count == 0


def test_반려_사유를_비우면_기본_문구가_들어간다(client, member_auth, admin_auth, book, fms):
    req_id = _pending_id(client, member_auth, book)
    res = client.post(f"{APPROVALS}/{req_id}/reject", json={}, headers=admin_auth)
    assert res.json()["reject_reason"] == "사서가 반려했습니다"


def test_이미_처리한_요청은_다시_처리할_수_없다(client, member_auth, admin_auth, book, fms):
    req_id = _pending_id(client, member_auth, book)
    assert client.post(f"{APPROVALS}/{req_id}/approve", headers=admin_auth).status_code == 200
    again = client.post(f"{APPROVALS}/{req_id}/approve", headers=admin_auth)
    assert again.status_code == 409
    assert fms.submit_count == 1  # 두 번 나가지 않는다


def test_없는_요청을_승인하면_404(client, admin_auth, fms):
    assert client.post(f"{APPROVALS}/999/approve", headers=admin_auth).status_code == 404


def test_승인_중_FMS_실패면_승인_대기_그대로_남는다(
    client, member_auth, admin_auth, book, fms, db_session
):
    """fail-closed — 승인만 찍히고 로봇은 안 움직이는 상태가 제일 나쁘다."""
    req_id = _pending_id(client, member_auth, book)
    fms.ok = False
    res = client.post(f"{APPROVALS}/{req_id}/approve", headers=admin_auth)
    assert res.status_code == 503

    db_session.expire_all()
    row = db_session.get(DeliveryRequest, req_id)
    assert row.approval == "PENDING_APPROVAL"
    assert row.fms_task_id == ""


def test_신청_뒤_대여된_책은_승인할_수_없다(
    client, member_auth, admin_auth, book, fms, db_session
):
    req_id = _pending_id(client, member_auth, book)
    book.in_stock = False
    db_session.commit()

    res = client.post(f"{APPROVALS}/{req_id}/approve", headers=admin_auth)
    assert res.status_code == 409
    assert fms.submit_count == 0


def test_회원_토큰으로는_승인_API에_못_들어간다(client, member_auth, admin_auth, book, fms):
    _pending_id(client, member_auth, book)
    assert client.get(APPROVALS, headers=member_auth).status_code == 401
    assert client.get(APPROVALS).status_code == 401


# ── 회원 화면에 보이는 상태 ──────────────────────────────────────────────────


def test_회원_요청현황에_승인상태가_보인다(client, member_auth, admin_auth, book, fms):
    req_id = _pending_id(client, member_auth, book)

    rows = client.get(MY, headers=member_auth).json()
    assert [r["approval"] for r in rows] == ["PENDING_APPROVAL"]

    client.post(
        f"{APPROVALS}/{req_id}/reject", json={"reason": "재고 확인 중"}, headers=member_auth | admin_auth
    )
    rows = client.get(MY, headers=member_auth).json()
    assert rows[0]["approval"] == "REJECTED"
    assert rows[0]["reject_reason"] == "재고 확인 중"


def test_승인_대기만_있으면_FMS_스냅샷을_부르지_않는다(
    client, member_auth, book, fms, monkeypatch
):
    """주문이 없는데 관제를 찌를 이유가 없다 — FMS 가 죽어도 내 요청 목록은 떠야 한다."""
    _pending_id(client, member_auth, book)

    called = {"n": 0}

    def boom() -> tuple[bool, list[dict]]:
        called["n"] += 1
        return False, []

    from app.routers import delivery

    monkeypatch.setattr(delivery.fms_client, "list_orders", boom)
    rows = client.get(MY, headers=member_auth).json()
    assert called["n"] == 0
    assert rows[0]["approval"] == "PENDING_APPROVAL"
