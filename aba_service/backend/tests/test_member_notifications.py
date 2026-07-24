"""회원 도착 알림 — 내 주문에서 일어난 일만, 놓치지 않게.

## 왜 요청 현황(`/requests`)으로는 안 되나

거기서 오는 건 "지금 EXECUTING 이다" 같은 **상태**다. 책이 자리에 도착한 순간과
도착한 지 10분 지난 순간이 화면에서 똑같이 보인다 — 알림을 띄울 근거가 없다.

## 서버가 걸러야 하는 이유

FMS 사건에는 **모든 회원의 주문**이 섞여 있다. 걸러 주지 않으면 남의 배달 알림이
그대로 보인다.
"""
from app.models import DeliveryRequest

NOTIF = "/api/member/request/notifications"


def _event(seq, task_id, kind="leg_done", text="테이블-1번-좌 도착"):
    return {"seq": seq, "ts": 1784.0 + seq, "kind": kind, "text": text,
            "task_id": task_id, "robot": "Pinkysim", "leg_idx": 2, "leg_count": 4}


def _my_request(db_session, member, book, task_id="t-1"):
    row = DeliveryRequest(
        member_id=member.id, book_id=book.id, kind="read",
        pickup="안내데스크", dropoff="테이블-1번-좌", fms_task_id=task_id,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_내_주문의_도착이_알림으로_온다(client, member_auth, member, book, fms, db_session):
    _my_request(db_session, member, book, "t-1")
    fms.events = [_event(1, "t-1")]

    res = client.get(NOTIF, headers=member_auth)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["text"] == "테이블-1번-좌 도착"
    assert body[0]["book_title"] == book.title_kr, "어떤 책인지 알아야 알림이 쓸모 있다"


def test_남의_주문은_안_보인다(client, member_auth, member, book, fms, db_session):
    """FMS 사건에는 모든 회원 주문이 섞여 있다 — 안 거르면 남의 배달이 보인다."""
    _my_request(db_session, member, book, "t-1")
    fms.events = [_event(1, "t-1"), _event(2, "t-99", text="남의 책 도착")]

    body = client.get(NOTIF, headers=member_auth).json()
    assert [e["task_id"] for e in body] == ["t-1"]


def test_since_로_놓친_것부터_받는다(client, member_auth, member, book, fms, db_session):
    """사건은 상태와 달리 놓치면 끝이다 — 화면이 잠깐 닫혀 있어도 되받을 수 있어야 한다."""
    _my_request(db_session, member, book, "t-1")
    fms.events = [_event(1, "t-1", text="복도-5 도착"),
                  _event(2, "t-1", text="테이블-1번-좌 도착"),
                  _event(3, "t-1", kind="task_done", text="배달 완료")]

    body = client.get(f"{NOTIF}?since=1", headers=member_auth).json()
    assert [e["text"] for e in body] == ["테이블-1번-좌 도착", "배달 완료"]


def test_승인_대기_중인_요청은_알림이_없다(client, member_auth, member, book, fms, db_session):
    """아직 주문이 안 나갔으면(fms_task_id 없음) 붙일 사건 자체가 없다."""
    row = DeliveryRequest(
        member_id=member.id, book_id=book.id, kind="borrow",
        pickup="안내데스크", dropoff="테이블-1번-좌", fms_task_id="",
    )
    db_session.add(row)
    db_session.commit()
    fms.events = [_event(1, "t-1")]

    assert client.get(NOTIF, headers=member_auth).json() == []


def test_FMS_가_죽어도_화면은_뜬다(client, member_auth, member, book, fms, db_session):
    """알림이 없다고 도서 목록까지 막을 이유는 없다."""
    _my_request(db_session, member, book, "t-1")
    fms.ok = False

    res = client.get(NOTIF, headers=member_auth)
    assert res.status_code == 200 and res.json() == []


def test_로그인_없이는_못_본다(client):
    assert client.get(NOTIF).status_code in (401, 403)
