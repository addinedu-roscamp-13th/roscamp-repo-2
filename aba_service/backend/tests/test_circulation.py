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


def test_훼손분실_처리된_책은_대출할_수_없다(client, admin_auth, member, book, db_session):
    book.unavailable = True
    db_session.commit()

    res = client.post(
        BORROW, json={"member_id": member.id, "book_id": book.id}, headers=admin_auth
    )
    assert res.status_code == 409
    assert "훼손" in res.json()["detail"]
    assert db_session.query(Loan).count() == 0


def test_도서명_검색은_띄어쓰기_차이를_무시한다(client, admin_auth, book):
    """book fixture 제목은 "어린 왕자" — 띄어쓰기 뺀 "어린왕자"로 검색해도 찾아야 한다."""
    res = client.get(
        "/api/admin/circulation/available-books",
        params={"q": "어린왕자"},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text
    titles = [b["title"] for b in res.json()]
    assert book.title_kr in titles


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


def test_PATCH에_비밀번호를_보내면_재설정된다(client, admin_auth, member, db_session):
    from app.security import verify_password

    old_hash = member.hashed_password
    res = client.patch(
        f"{MEMBERS}/{member.id}",
        json={"password": "새비번1234"},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text

    db_session.expire_all()
    updated = db_session.get(Member, member.id)
    assert updated.hashed_password != old_hash
    assert verify_password("새비번1234", updated.hashed_password)


def test_PATCH로_is_active_토글할_수_있다(client, admin_auth, member, db_session):
    res = client.patch(
        f"{MEMBERS}/{member.id}",
        json={"is_active": False},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is False

    db_session.expire_all()
    assert db_session.get(Member, member.id).is_active is False

    res = client.patch(
        f"{MEMBERS}/{member.id}",
        json={"is_active": True},
        headers=admin_auth,
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is True


def test_없는_회원_수정하면_404(client, admin_auth):
    res = client.patch(f"{MEMBERS}/9999", json={"full_name": "x"}, headers=admin_auth)
    assert res.status_code == 404


def test_대출중인_회원은_삭제할_수_없다(client, admin_auth, member, book, db_session):
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
    assert res.json()["detail"] == "처리 중인 대출/요청/예약이 있어 삭제할 수 없습니다"


def test_승인대기_요청이_있는_회원은_삭제할_수_없다(
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
    assert res.json()["detail"] == "처리 중인 대출/요청/예약이 있어 삭제할 수 없습니다"


def test_예약대기중인_회원은_삭제할_수_없다(client, admin_auth, member, book, db_session):
    db_session.add(
        Reservation(member_id=member.id, book_id=book.id, status="waiting")
    )
    db_session.commit()

    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 409
    assert res.json()["detail"] == "처리 중인 대출/요청/예약이 있어 삭제할 수 없습니다"


def test_아무것도_없으면_삭제_성공(client, admin_auth, member, db_session):
    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 204, res.text

    db_session.expire_all()
    assert db_session.get(Member, member.id) is None


def test_과거_대출이력_있어도_삭제된다(client, admin_auth, member, book, db_session):
    """반납 완료된(=진행 중이 아닌) 대출 이력은 가드에 안 걸린다 — 실 DB(MariaDB)에서는
    Loan.member_id 가 ondelete="CASCADE"라 삭제 시 이 이력도 함께 지워진다(SQLite 테스트
    환경은 FK CASCADE를 강제하지 않아 여기서는 회원 삭제 자체만 확인한다)."""
    db_session.add(
        Loan(
            member_id=member.id,
            book_id=book.id,
            status="returned",
            due_at=datetime.now() + timedelta(days=14),
            returned_at=datetime.now(),
        )
    )
    db_session.commit()

    res = client.delete(f"{MEMBERS}/{member.id}", headers=admin_auth)
    assert res.status_code == 204, res.text

    db_session.expire_all()
    assert db_session.get(Member, member.id) is None


def test_없는_회원_삭제하면_404(client, admin_auth):
    res = client.delete(f"{MEMBERS}/9999", headers=admin_auth)
    assert res.status_code == 404
