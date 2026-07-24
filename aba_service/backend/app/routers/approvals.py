"""사서 승인 — 회원의 **대여 신청**을 승인/반려한다.

## 왜 승인이 필요한가
대여는 책이 관외로 나간다. 되돌리려면 회원을 다시 불러야 하므로 사서가 한 번 본다.
열람(자리로 받기)은 책이 관내에 남으니 승인 없이 바로 나간다 — 그래서 여기에는
`kind='borrow'` 만 올라온다.

## 주문은 승인 시점에 나간다
회원이 신청한 순간에는 `cb_delivery_requests` 행만 생기고 FMS 는 **아무것도 모른다**.
사서가 승인을 눌러야 `delivery.dispatch_to_fms()` 가 주문을 내고 task_id 가 채워진다.
FMS 가 못 받으면 503 을 돌려주고 행은 **승인 대기 그대로** 둔다(fail-closed) — 승인만
찍히고 로봇은 안 움직이는 상태가 제일 나쁘다.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import fms_client
from ..database import get_db
from ..models import AdminUser, Book, DeliveryRequest, Member
from ..security import get_current_admin
from .delivery import APPROVED, PENDING, REJECTED, dispatch_to_fms
from .ops_extra import TERMINAL as _FMS_TERMINAL_STATUSES

router = APIRouter(prefix="/api/admin/ops", tags=["ops-approvals"])


class RejectRequest(BaseModel):
    reason: str = ""


def _row_out(row: DeliveryRequest, book: Book | None, member: Member | None) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "approval": row.approval,
        "book_id": row.book_id,
        "book_title": book.title_kr if book else "(삭제된 도서)",
        "book_in_stock": bool(book.in_stock) if book else False,
        "member_id": row.member_id,
        "member_username": member.username if member else "(탈퇴 회원)",
        "member_name": (member.full_name if member else None),
        "pickup": row.pickup,
        "dropoff": row.dropoff,
        "fms_task_id": row.fms_task_id,
        "reject_reason": row.reject_reason,
        "decided_at": row.decided_at,
        "decided_by": row.decided_by,
        "created_at": row.created_at,
    }


def _load(db: Session, rows: list[DeliveryRequest]) -> list[dict]:
    return [
        _row_out(r, db.get(Book, r.book_id), db.get(Member, r.member_id)) for r in rows
    ]


@router.get("/approvals")
def list_approvals(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """승인 대기 목록 + 최근 처리 내역. FMS 를 부르지 않는다(우리 DB 만 본다)."""
    pending = db.scalars(
        select(DeliveryRequest)
        .where(DeliveryRequest.approval == PENDING)
        .order_by(DeliveryRequest.created_at.asc())
        .limit(limit)
    ).all()
    recent = db.scalars(
        select(DeliveryRequest)
        .where(DeliveryRequest.approval.in_((APPROVED, REJECTED)))
        .where(DeliveryRequest.decided_at.is_not(None))
        .order_by(DeliveryRequest.decided_at.desc())
        .limit(limit)
    ).all()
    return {"pending": _load(db, list(pending)), "recent": _load(db, list(recent))}


def _get_pending(db: Session, req_id: int) -> DeliveryRequest:
    row = db.get(DeliveryRequest, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다")
    if row.approval != PENDING:
        raise HTTPException(
            status_code=409, detail=f"이미 처리된 요청입니다 ({row.approval})"
        )
    return row


@router.post("/approvals/{req_id}/approve")
def approve(
    req_id: int,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """승인 — 이때 FMS 주문이 생기고 로봇이 움직인다."""
    row = _get_pending(db, req_id)
    # 이 요청 자체를 먼저 원자적으로 "내가 처리한다"고 찜한다 — read-then-write 로 두면
    # 동시에 들어온 두 번의 approve 호출이 둘 다 위 `_get_pending` 의 PENDING 체크를
    # 통과한 뒤 둘 다 dispatch 까지 갈 수 있다. rowcount == 0 이면 그 사이 누가 먼저
    # 이 요청을 처리(승인/반려)한 것이다.
    claimed = db.execute(
        update(DeliveryRequest)
        .where(DeliveryRequest.id == row.id, DeliveryRequest.approval == PENDING)
        .values(approval=APPROVED)
    )
    if claimed.rowcount == 0:
        raise HTTPException(status_code=409, detail="이미 처리된 요청입니다")
    # `SELECT ... FOR UPDATE` 로 잠가서 읽는다 — 이 승인이 dispatch 를 결정하는 바로 그
    # 순간의 커밋된 값을 봐야 한다. 여기서는 in_stock 을 직접 내리지 않는다(그건
    # circulation.borrow() 의 일이다 — 실물 인도 확정 시점에만 내려간다). 그래서
    # borrow() 처럼 조건-그대로-UPDATE 로 반영할 대상이 없고, 값이 그대로인 no-op
    # UPDATE 는 MariaDB 기본 드라이버(pymysql)가 "matched" 가 아니라 "changed" 행만
    # rowcount 로 세기 때문에 항상 0으로 나와 오탐 409를 낸다 — 그래서 락 읽기를 쓴다.
    # (SQLite 는 FOR UPDATE 를 지원하지 않고 조용히 무시하므로 테스트 인프라는 그대로 재사용된다.)
    book = db.execute(
        select(Book).where(Book.id == row.book_id).with_for_update()
    ).scalar_one_or_none()
    if book is None:
        # 위에서 이미 approval=APPROVED 로 찜해 뒀다 — 여기서 실패하면 그 찜을 되돌린다.
        db.rollback()
        raise HTTPException(status_code=409, detail="도서가 삭제되어 승인할 수 없습니다")
    if book.unavailable:
        # 신청 이후에 훼손/분실로 확인된 경우 — 로봇이 그 책을 찾으러 가면 안 된다.
        db.rollback()
        raise HTTPException(
            status_code=409, detail="그 사이 훼손/분실 처리된 도서입니다. 반려하세요."
        )
    if not book.in_stock:
        # 신청 이후에 누가 빌려간 경우 — 로봇이 없는 책을 찾으러 가면 안 된다.
        db.rollback()
        raise HTTPException(
            status_code=409, detail="그 사이 대여된 도서입니다. 반려하고 예약을 안내하세요."
        )

    member = db.get(Member, row.member_id)
    # FMS 가 못 받으면 여기서 503 이 올라가고 행은 PENDING 그대로 남는다(fail-closed).
    # 위 book 행 락이 이 호출(네트워크 왕복) 동안에도 계속 잡혀 있다 — 다만
    # fms_client.TIMEOUT_SEC(현재 8초)로 상한이 걸려 있어 MariaDB 기본
    # innodb_lock_wait_timeout(~50초)보다 충분히 짧다. 의도된, 상한이 있는 트레이드오프.
    try:
        row.fms_task_id = dispatch_to_fms(book, row.dropoff, member.username if member else "")
    except HTTPException:
        # 위 approval=APPROVED 찜도 함께 되돌려야 승인 대기 그대로 남는다(fail-closed).
        db.rollback()
        raise
    row.decided_at = datetime.now()
    row.decided_by = admin.username
    row.reject_reason = None
    db.commit()
    db.refresh(row)
    return _row_out(row, book, member)


@router.post("/approvals/{req_id}/reject", status_code=status.HTTP_200_OK)
def reject(
    req_id: int,
    body: RejectRequest,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """반려 — FMS 주문은 생기지 않고 회원 화면에 반려로 보인다."""
    row = _get_pending(db, req_id)
    row.approval = REJECTED
    row.decided_at = datetime.now()
    row.decided_by = admin.username
    row.reject_reason = (body.reason or "").strip()[:255] or "사서가 반려했습니다"
    db.commit()
    db.refresh(row)
    return _row_out(row, db.get(Book, row.book_id), db.get(Member, row.member_id))


def _fms_task_terminated(task_id: str) -> bool:
    """FMS 작업이 종료 상태(`ops_extra.TERMINAL`)인지 본다.

    조회 자체가 실패하면(FMS 다운) 안전하게 "아직 진행 중"으로 본다(fail-closed) —
    로봇이 계속 도는데 이력만 지워지는 상황보다는 삭제를 한 번 더 거부하는 쪽이 낫다.

    FMS 조회는 성공했는데 목록에 그 task_id가 없으면(FMS는 활성 큐만 들고 있어
    지난 작업은 원래 안 보인다) 이미 끝났다는 뜻이라 종료로 본다.
    """
    ok, orders = fms_client.list_orders()
    if not ok:
        return False
    matching = [o for o in orders if o.get("id") == task_id]
    if not matching:
        return True
    return all(o.get("status") in _FMS_TERMINAL_STATUSES for o in matching)


@router.delete("/approvals/{req_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_approval_history(
    req_id: int,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """대여승인 이력 삭제 — 진짜 hard delete(이 표는 부활 버그가 없다, `ops.py` 작업로그와 다르다).

    이 표는 단순 로그가 아니라 member/book/승인결정/진행중 FMS task 를 잇는 링크다.
    `REJECTED`(주문이 애초에 없었다)는 언제나 지울 수 있지만, `APPROVED`는 연결된 FMS
    작업이 종료 상태일 때만 지운다 — 아니면 로봇은 계속 움직이는데 회원/사서 화면에서만
    이력이 사라지는 상황이 생긴다.
    """
    row = db.get(DeliveryRequest, req_id)
    if row is None:
        raise HTTPException(status_code=404, detail="요청을 찾을 수 없습니다")
    deletable = row.approval == REJECTED or (
        row.approval == APPROVED and _fms_task_terminated(row.fms_task_id)
    )
    if not deletable:
        raise HTTPException(status_code=409, detail="진행 중인 요청은 삭제할 수 없습니다")
    db.delete(row)
    db.commit()
