"""사서 대출 확정 — 재고 체크의 원자성.

## 왜 이 테스트가 있나
`borrow()` 는 예전엔 `if not book.in_stock` 을 읽고 나중에 `book.in_stock = False` 를 썼다
(read-then-write). 두 세션이 동시에 같은 책을 대출하면 둘 다 통과할 수 있는 레이스였다.
지금은 단일 `UPDATE ... WHERE in_stock = True` 로 체크와 반영을 한 번에 한다 —
`rowcount == 0` 이면 이미 대출 중이라는 뜻이고, 그 경로가 SQLite 목업이 아니라
**실제 두 번째 UPDATE 결과**로 타는지를 여기서 확인한다.
"""

from datetime import datetime, timedelta

from app.models import DeliveryRequest, Loan, Member, Reservation

BORROW = "/api/admin/circulation/borrow"
MEMBERS = "/api/admin/circulation/members"


def test_대출_1회_성공(client, admin_auth, member, book):
    res = client.post(
        BORROW, json={"member_id": member.id, "book_id": book.id}, headers=admin_auth
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "borrowed"
    assert body["book_id"] == book.id
    assert body["member_id"] == member.id


def test_이미_대출중인_책은_다시_대출할_수_없다(client, admin_auth, member, book, db_session):
    """mock 없이 — 첫 대출이 실제 UPDATE 로 in_stock 을 내린 뒤, 두 번째 UPDATE 가 rowcount 0 을 맞는지 본다."""
    first = client.post(
        BORROW, json={"member_id": member.id, "book_id": book.id}, headers=admin_auth
    )
    assert first.status_code == 201, first.text

    second = client.post(
        BORROW, json={"member_id": member.id, "book_id": book.id}, headers=admin_auth
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "이미 대출 중인 도서입니다"

    # 두 번째 시도가 Loan 을 하나 더 만들지 않았는지까지 확인한다.
    assert db_session.query(Loan).count() == 1


# ── 회원관리 CRUD (사서용) ────────────────────────────────────────────────────


def test_회원_생성_성공(client, admin_auth):
    res = client.post(
        MEMBERS,
        json={"username": "kim", "full_name": "김철수", "password": "pw1234"},
        headers=admin_auth,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["username"] == "kim"
    assert body["full_name"] == "김철수"
    assert body["is_active"] is True
    assert body["active_loans"] == 0
    assert body["total_loans"] == 0


def test_회원_생성시_username_중복이면_409(client, admin_auth, member):
    res = client.post(
        MEMBERS,
        json={"username": member.username, "password": "pw1234"},
        headers=admin_auth,
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "이미 존재하는 아이디입니다"


def test_회원_수정_성공(client, admin_auth, member):
    res = client.patch(
        f"{MEMBERS}/{member.id}",
        json={"full_name": "새이름"},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["full_name"] == "새이름"


def test_PATCH에_is_active를_보내도_무시된다(client, admin_auth, member, db_session):
    """PATCH 는 이름만 바꾼다 — 비활성화는 가드가 있는 DELETE 전용, 재활성은 범위 밖(R13 계획).

    `UpdateMemberRequest` 에서 `is_active` 필드 자체를 없앴으므로 Pydantic 이 알 수 없는
    필드로 조용히 무시한다(extra="ignore" 가 기본값) — 몸값에 있어도 절대 반영되지 않는다.
    """
    res = client.patch(
        f"{MEMBERS}/{member.id}",
        json={"full_name": "새이름", "is_active": False},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text
    assert res.json()["full_name"] == "새이름"
    assert res.json()["is_active"] is True  # 그대로 — PATCH 로는 절대 안 내려간다

    db_session.expire_all()
    assert db_session.get(Member, member.id).is_active is True


def test_없는_회원_수정하면_404(client, admin_auth):
    res = client.patch(f"{MEMBERS}/9999", json={"full_name": "x"}, headers=admin_auth)
    assert res.status_code == 404


def test_대출중인_회원은_비활성화할_수_없다(client, admin_auth, member, book, db_session):
    db_session.add(
        Loan(
            member_id=member.id,
            book_id=book.id,
            status="borrowed",
            due_at=datetime.now() + timedelta(days=14),
        )
    )
    db_session.commit()

    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 409
    assert res.json()["detail"] == "처리 중인 대출/요청/예약이 있어 비활성화할 수 없습니다"


def test_승인대기_요청이_있는_회원은_비활성화할_수_없다(
    client, admin_auth, member, book, db_session
):
    db_session.add(
        DeliveryRequest(
            member_id=member.id,
            book_id=book.id,
            kind="borrow",
            pickup=book.zone,
            dropoff="안네데스크",
            approval="PENDING_APPROVAL",
            fms_task_id="",
        )
    )
    db_session.commit()

    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 409
    assert res.json()["detail"] == "처리 중인 대출/요청/예약이 있어 비활성화할 수 없습니다"


def test_예약대기중인_회원은_비활성화할_수_없다(client, admin_auth, member, book, db_session):
    db_session.add(
        Reservation(member_id=member.id, book_id=book.id, status="waiting")
    )
    db_session.commit()

    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 409
    assert res.json()["detail"] == "처리 중인 대출/요청/예약이 있어 비활성화할 수 없습니다"


def test_아무것도_없으면_비활성화_성공(client, admin_auth, member, db_session):
    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False

    db_session.expire_all()
    assert db_session.get(Member, member.id).is_active is False


def test_이미_비활성_회원_재비활성화는_200_idempotent(client, admin_auth, member, db_session):
    member.is_active = False
    db_session.commit()

    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False


def test_없는_회원_비활성화하면_404(client, admin_auth):
    res = client.delete(f"{MEMBERS}/9999", headers=admin_auth)
    assert res.status_code == 404
