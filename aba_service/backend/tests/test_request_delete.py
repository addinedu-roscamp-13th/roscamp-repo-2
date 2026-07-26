"""요청 이력 삭제 — 남의 기록은 못 지우고, 승인 대기 중인 건은 못 지운다."""

from app.models import DeliveryRequest, Member
from app.member_security import create_member_token
from app.security import hash_password
from tests.conftest import make_book


def _request_row(db_session, member, book, *, approval="APPROVED", task_id="t-1"):
    row = DeliveryRequest(
        member_id=member.id,
        book_id=book.id,
        kind="read",
        pickup=book.zone,
        dropoff="1번테이블",
        fms_task_id=task_id,
        approval=approval,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_delete_own_finished_request(client, db_session, member, member_auth, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book)

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 204
    assert db_session.get(DeliveryRequest, row.id) is None


def test_delete_rejected_request(client, db_session, member, member_auth, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book, approval="REJECTED", task_id="")

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 204


def test_cannot_delete_pending_approval(client, db_session, member, member_auth, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book, approval="PENDING_APPROVAL", task_id="")

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 409
    assert db_session.get(DeliveryRequest, row.id) is not None


def test_cannot_delete_other_members_request(client, db_session, member, member_auth, fms):
    other = Member(
        username="other", full_name="남", hashed_password=hash_password("pw"), is_active=True
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    book = make_book(db_session)
    row = _request_row(db_session, other, book)

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 404
    assert db_session.get(DeliveryRequest, row.id) is not None


def test_delete_missing_request_is_404(client, member_auth, fms):
    res = client.delete("/api/member/requests/99999", headers=member_auth)

    assert res.status_code == 404


def test_delete_requires_auth(client, db_session, member, fms):
    book = make_book(db_session)
    row = _request_row(db_session, member, book)

    res = client.delete(f"/api/member/requests/{row.id}")

    assert res.status_code == 401


def test_cannot_delete_approved_request_still_active_in_fms(
    client, db_session, member, member_auth, fms
):
    book = make_book(db_session)
    row = _request_row(db_session, member, book, task_id="t-1")
    fms.active_orders = [{"id": "t-1", "status": "MOVING"}]

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 409
    assert db_session.get(DeliveryRequest, row.id) is not None


def test_can_delete_approved_request_once_fms_marks_it_completed(
    client, db_session, member, member_auth, fms
):
    book = make_book(db_session)
    row = _request_row(db_session, member, book, task_id="t-1")
    fms.active_orders = [{"id": "t-1", "status": "COMPLETED"}]

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 204


def test_can_delete_approved_request_fms_no_longer_knows_about(
    client, db_session, member, member_auth, fms
):
    """FMS 가 이미 정리해서 목록에서 빠진 주문 — 완료됐다고 보고 지울 수 있다."""
    book = make_book(db_session)
    row = _request_row(db_session, member, book, task_id="t-1")
    fms.active_orders = []

    res = client.delete(f"/api/member/requests/{row.id}", headers=member_auth)

    assert res.status_code == 204
