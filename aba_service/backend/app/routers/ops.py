"""사서 운영 API — 대시보드 · 로봇 모니터링 · 작업 지시 · 도서/서가 관리 · 통계.

## 왜 여기서 FMS 를 대신 부르나
로봇 데이터(위치·상태·작업 큐)는 FMS(:9001)가 들고 있고 **FMS 관리자 인증**이 걸려 있다.
사서 브라우저가 직접 FMS 를 부르게 하면 자격증명을 내려보내야 하므로, 도서관 백엔드가
서비스 계정으로 대신 호출한다(`fms_client`). 사서는 이쪽 인증만 통과하면 된다.

FMS 가 죽어 있어도 **화면은 떠야 한다** — 로봇 정보만 비고 도서/회원 기능은 계속 쓴다.
그래서 FMS 호출 실패는 예외가 아니라 `linked: false` 로 응답에 표시한다.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import fms_client
from ..database import get_db
from ..models import AdminUser, Book, DeliveryRequest, Loan, Member, Reservation
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/ops", tags=["ops"])

#: 분류(sort) 주문의 대상 — 개별 도서가 아니라 반납함에 쌓인 것을 한 번에 옮긴다.
SORT_CARGO = "반납 도서 일괄"

#: 작업 지시 종류 — 로봇이 수행할 수 있는 일. **화면의 입력 폼도 이 표에서 나온다.**
#:
#: 예전에는 8종이 전부 같은 폼이었고 `kind` 는 라벨일 뿐이라, 「복귀」를 골라도 책을
#: 집으러 가고 「순찰」을 골라도 목적지 한 곳으로 갔다. 종류마다 성격이 다르므로
#: **필요한 입력**과 **보내는 방식**을 함께 적는다.
#:
#: `mode` 가 둘이다:
#:   - `order` : FMS 주문(task)을 만든다. `order_kind` 가 다리 구성을 정한다
#:               (`delivery`=주행→집기→주행→놓기 4다리, `navigate`=주행 1다리).
#:   - `state` : 주문이 아니라 **로봇 모드 변경**이다. 모드가 바뀌면 해당 BT 브랜치가
#:               알아서 돈다 — 있지도 않은 책을 집으러 가지 않는다.
#:
#: `fields` 는 화면이 그릴 입력칸이다:
#:   book(도서 목록에서 선택) · cargo(짐 이름 자유 입력) · pickup(출발지) · dropoff(목적지)
TASK_KINDS = [
    {
        "key": "transfer",
        "label": "이송",
        "desc": "서가 → 지정 위치로 도서 운반",
        "mode": "order",
        "order_kind": "delivery",
        "fields": ["book", "pickup", "dropoff"],
        "hint": "책을 고르면 출발지가 그 책의 서가로 자동 설정됩니다.",
    },
    {
        "key": "shelve",
        "label": "진열",
        "desc": "수거된 도서를 서가에 꽂기",
        "mode": "order",
        "order_kind": "delivery",
        "fields": ["book", "pickup"],
        # 목적지는 사서가 고르지 않는다 — 책이 정한다(도서의 zone).
        "auto_dropoff": "book_zone",
        "hint": "목적지는 그 도서의 서가로 자동 지정됩니다.",
    },
    {
        "key": "sort",
        "label": "분류",
        "desc": "반납 도서를 분류대에서 정리",
        "mode": "order",
        "order_kind": "delivery",
        "fields": ["pickup", "dropoff"],
        # 개별 도서가 아니라 반납함에 쌓인 것을 한 번에 옮긴다.
        "fixed_book": SORT_CARGO,
        "hint": "대상은 «반납 도서 일괄» 로 고정입니다. 반납함 → 분류대.",
    },
    {
        "key": "tidy",
        "label": "정리",
        "desc": "서가 정돈 점검",
        "mode": "order",
        "order_kind": "navigate",
        "fields": ["dropoff"],
        "hint": "점검할 서가로 다녀옵니다. 집고 놓는 동작이 없어 주행 1다리입니다.",
    },
    {
        "key": "porter",
        "label": "짐꾼",
        "desc": "짐을 실어 지정 위치로",
        "mode": "order",
        "order_kind": "delivery",
        "fields": ["cargo", "pickup", "dropoff"],
        "hint": "도서 목록에 없는 물건입니다. 짐 이름을 직접 적어주세요.",
    },
    {
        "key": "dispatch",
        "label": "파견",
        "desc": "지정 위치로 이동 대기",
        "mode": "order",
        "order_kind": "navigate",
        "fields": ["dropoff"],
        "hint": "지정 위치까지 가서 대기합니다. 주행 1다리입니다.",
    },
    {
        "key": "return",
        "label": "복귀",
        "desc": "충전 스테이션으로 복귀",
        "mode": "state",
        "target_state": "RETURNING",
        "fields": [],
        "hint": "작업이 아니라 모드 변경입니다. 로봇을 지정해 주세요 — 복귀·도킹은 로봇이 합니다.",
    },
    {
        "key": "patrol",
        "label": "순찰",
        "desc": "지정 경로 순회",
        "mode": "state",
        "target_state": "PATROL",
        "fields": [],
        "hint": "작업이 아니라 모드 변경입니다. 순찰 경로는 로봇의 PatrolBranch 가 정합니다.",
    },
]

KIND_BY_KEY = {k["key"]: k for k in TASK_KINDS}


# ── 대시보드 ────────────────────────────────────────────────────────────────


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """운영 한눈 요약 — 도서/회원/대출은 우리 DB, 로봇/작업은 FMS."""
    now = datetime.now()
    total_books = db.scalar(select(func.count(Book.id))) or 0
    out_books = db.scalar(select(func.count(Book.id)).where(Book.in_stock.is_(False))) or 0
    members = db.scalar(select(func.count(Member.id))) or 0
    active_loans = (
        db.scalar(select(func.count(Loan.id)).where(Loan.status == "borrowed")) or 0
    )
    overdue = (
        db.scalar(
            select(func.count(Loan.id)).where(
                Loan.status == "borrowed", Loan.due_at < now
            )
        )
        or 0
    )
    due_soon = (
        db.scalar(
            select(func.count(Loan.id)).where(
                Loan.status == "borrowed",
                Loan.due_at >= now,
                Loan.due_at <= now + timedelta(days=3),
            )
        )
        or 0
    )
    waiting_res = (
        db.scalar(
            select(func.count(Reservation.id)).where(Reservation.status == "waiting")
        )
        or 0
    )
    ready_res = (
        db.scalar(
            select(func.count(Reservation.id)).where(Reservation.status == "ready")
        )
        or 0
    )

    ok, snap = fms_client.fleet_snapshot()
    robots = snap.get("robots", []) if ok else []
    ok_orders, orders = fms_client.list_orders()

    def count(status: str) -> int:
        return sum(1 for o in orders if o.get("status") == status)

    return {
        "library": {
            "books": total_books,
            "books_out": out_books,
            "members": members,
            "active_loans": active_loans,
            "overdue": overdue,
            "due_soon": due_soon,
            "reservations_waiting": waiting_res,
            "reservations_ready": ready_res,
        },
        "fleet": {
            "linked": ok,
            "robots": len(robots),
            "idle": sum(1 for r in robots if r.get("state") == "IDLE"),
            "patrol": sum(1 for r in robots if r.get("state") == "PATROL"),
            "working": sum(1 for r in robots if r.get("state") == "WORKING"),
            "stale": sum(1 for r in robots if r.get("stale")),
        },
        "tasks": {
            "linked": ok_orders,
            "pending": count("PENDING"),
            "executing": count("EXECUTING") + count("ASSIGNED"),
            "completed": count("COMPLETED"),
            "failed": count("FAILED"),
        },
    }


# ── 실시간 모니터링 ──────────────────────────────────────────────────────────


@router.get("/robots")
def robots(_: AdminUser = Depends(get_current_admin)):
    """로봇별 상태·배터리·현재 작업·위치. FMS 가 죽어도 linked:false 로 응답한다."""
    ok, snap = fms_client.fleet_snapshot()
    return {
        "linked": ok,
        "robots": snap.get("robots", []) if ok else [],
        "plugins": snap.get("plugins", {}) if ok else {},
    }


@router.get("/tasks")
def tasks(_: AdminUser = Depends(get_current_admin)):
    """작업 큐 + 이력 + 진행률."""
    ok, orders = fms_client.list_orders()
    return {"linked": ok, "orders": orders, "kinds": TASK_KINDS}


class OrderRequest(BaseModel):
    """작업 지시. **종류마다 필요한 칸이 다르다** — 검증은 `TASK_KINDS` 표가 한다.

    필요 없는 칸은 안 보내도 되고(기본값 빈 문자열), 자동으로 채우는 칸(진열의 목적지)은
    보내도 무시된다.
    """

    kind: str = "transfer"
    book: str = ""
    #: 도서 목록에 없는 물건(짐꾼). `book` 과 같은 자리에 들어가지만 서가 자동채움이 없다.
    cargo: str = ""
    pickup: str = ""
    dropoff: str = ""
    #: 빈 값이면 자동(경매/휴리스틱) 배차. 모드 변경(복귀·순찰)에서는 **필수**다.
    robot: str = ""
    priority: int = 0


def _resolve_order(spec: dict, body: OrderRequest, db: Session) -> tuple[str, str, str]:
    """종류 정의에 따라 (대상, 출발지, 목적지)를 확정한다.

    "필요 없는 필드는 안 받고, 없는 필드는 채워 넣는다" — 진열의 목적지는 도서의 서가에서
    나오고, 분류의 대상은 «반납 도서 일괄» 로 고정이다.
    """
    fields = spec["fields"]

    # 대상 — 도서 / 짐 / 고정 문구 중 하나.
    if "book" in fields:
        target = body.book.strip()
        if not target:
            raise HTTPException(status_code=400, detail="도서를 선택해 주세요")
    elif "cargo" in fields:
        target = body.cargo.strip() or body.book.strip()
        if not target:
            raise HTTPException(status_code=400, detail="짐 이름을 입력해 주세요")
    else:
        target = spec.get("fixed_book", "")

    pickup = body.pickup.strip() if "pickup" in fields else ""
    if "pickup" in fields and not pickup:
        raise HTTPException(status_code=400, detail="출발지를 선택해 주세요")

    if spec.get("auto_dropoff") == "book_zone":
        # 목적지는 책이 정한다 — 제목이 카탈로그에 있어야 서가를 알 수 있다.
        hit = db.scalars(select(Book).where(Book.title_kr == target).limit(1)).first()
        if hit is None or not hit.zone:
            raise HTTPException(
                status_code=400,
                detail=f"«{target}» 의 서가를 찾을 수 없어 진열 목적지를 정할 수 없습니다",
            )
        dropoff = hit.zone
    else:
        dropoff = body.dropoff.strip()
        if not dropoff:
            raise HTTPException(status_code=400, detail="목적지를 선택해 주세요")

    return target, pickup, dropoff


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    body: OrderRequest,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """작업 지시 — 종류에 따라 **주문**이거나 **로봇 모드 변경**이다.

    로봇 지정(특정)/미지정(자동 배차) 둘 다 지원한다. 단 「복귀」·「순찰」은 대상 로봇이
    있어야 하므로 지정이 필수다.
    """
    spec = KIND_BY_KEY.get(body.kind)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 작업 종류: {body.kind}")

    # ── 모드 변경(복귀·순찰) — task 가 아니다 ──────────────────────────────
    if spec["mode"] == "state":
        if not body.robot:
            raise HTTPException(
                status_code=400, detail=f"「{spec['label']}」 는 로봇을 지정해야 합니다"
            )
        ok, res = fms_client.request_transition(body.robot, spec["target_state"])
        if not ok:
            # FMS 를 못 불렀다. 로봇이 "그 전이는 안 된다"고 한 경우는 아래로 내려간다.
            raise HTTPException(
                status_code=503, detail=f"모드 변경 실패 — {res.get('reason', '')}"
            )
        return {
            "mode": "state",
            "robot": body.robot,
            "state": spec["target_state"],
            "accepted": bool(res.get("accepted")),
            "current_state": res.get("current_state", ""),
            "reason": res.get("reason", ""),
        }

    # ── 주문(task) ────────────────────────────────────────────────────────
    target, pickup, dropoff = _resolve_order(spec, body, db)
    ok, value = fms_client.submit_order(
        kind=spec["order_kind"],
        book=target,
        pickup=pickup,
        dropoff=dropoff,
        requester=f"사서:{body.kind}",
        priority=body.priority,
    )
    if not ok:
        raise HTTPException(status_code=503, detail=f"작업 지시 실패 — {value}")

    assigned = None
    if body.robot:
        ok2, res = fms_client.assign_order(value, body.robot)
        assigned = body.robot if ok2 else None
        if not ok2:
            return {
                "mode": "order",
                "task_id": value,
                "assigned": None,
                "warn": f"배차 실패: {res[:120]}",
            }
    return {
        "mode": "order",
        "task_id": value,
        "assigned": assigned,
        "dropoff": dropoff,
    }


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, _: AdminUser = Depends(get_current_admin)):
    ok, res = fms_client.cancel_order(task_id)
    if not ok:
        raise HTTPException(status_code=503, detail=f"취소 실패 — {res[:160]}")
    return {"ok": True}


# ── 도서 관리 ────────────────────────────────────────────────────────────────


class BookIn(BaseModel):
    title_kr: str
    title_en: str = ""
    title_zh: str = ""
    title_vi: str = ""
    author: str
    category: str
    cover: str = "📘"
    color: str = "from-slate-200 to-slate-300"
    zone: str
    shelf: str = ""
    in_stock: bool = True
    summary_kr: str = ""


def _book_out(b: Book) -> dict:
    return {
        "id": b.id,
        "title_kr": b.title_kr,
        "author": b.author,
        "category": b.category,
        "cover": b.cover,
        "zone": b.zone,
        "shelf": b.shelf,
        "in_stock": bool(b.in_stock),
    }


@router.get("/books")
def list_books(
    q: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    stmt = select(Book)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Book.title_kr.like(like), Book.author.like(like)))
    if category:
        stmt = stmt.where(Book.category == category)
    return [_book_out(b) for b in db.scalars(stmt.order_by(Book.id).limit(300)).all()]


@router.post("/books", status_code=status.HTTP_201_CREATED)
def create_book(
    body: BookIn, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    b = Book(
        title_kr=body.title_kr,
        title_en=body.title_en or body.title_kr,
        title_zh=body.title_zh or body.title_kr,
        title_vi=body.title_vi or body.title_kr,
        author=body.author,
        category=body.category,
        cover=body.cover,
        color=body.color,
        zone=body.zone,
        shelf=body.shelf,
        in_stock=body.in_stock,
        summary_kr=body.summary_kr,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return _book_out(b)


@router.put("/books/{book_id}")
def update_book(
    book_id: int,
    body: BookIn,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    b = db.get(Book, book_id)
    if b is None:
        raise HTTPException(status_code=404, detail="도서를 찾을 수 없습니다")
    for field in ("title_kr", "author", "category", "cover", "zone", "shelf", "in_stock"):
        setattr(b, field, getattr(body, field))
    db.commit()
    db.refresh(b)
    return _book_out(b)


@router.delete("/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(
    book_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    b = db.get(Book, book_id)
    if b is None:
        raise HTTPException(status_code=404, detail="도서를 찾을 수 없습니다")
    # 대출 중인 도서를 지우면 이력이 끊긴다 — 막는다.
    active = db.scalar(
        select(func.count(Loan.id)).where(Loan.book_id == book_id, Loan.status == "borrowed")
    )
    if active:
        raise HTTPException(status_code=409, detail="대출 중인 도서는 삭제할 수 없습니다")
    db.delete(b)
    db.commit()


# ── 서가 관리 ────────────────────────────────────────────────────────────────


@router.get("/shelves")
def shelves(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """서가(zone)별 보유 도서 수 + 분야 구성. 배치 지도는 프론트가 waypoint 로 그린다."""
    rows = db.execute(
        select(Book.zone, Book.category, func.count(Book.id)).group_by(
            Book.zone, Book.category
        )
    ).all()
    by_zone: dict[str, dict] = {}
    for zone, category, n in rows:
        z = by_zone.setdefault(zone, {"zone": zone, "total": 0, "categories": {}})
        z["total"] += n
        z["categories"][category] = n
    return sorted(by_zone.values(), key=lambda z: z["zone"])


# ── 통합 검색 ────────────────────────────────────────────────────────────────


@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """도서 · 위치(서가) · 회원을 한 번에."""
    like = f"%{q.strip()}%"
    books = db.scalars(
        select(Book)
        .where(or_(Book.title_kr.like(like), Book.author.like(like)))
        .limit(20)
    ).all()
    members = db.scalars(
        select(Member)
        .where(or_(Member.username.like(like), Member.full_name.like(like)))
        .limit(20)
    ).all()
    zones = db.scalars(select(Book.zone).where(Book.zone.like(like)).distinct()).all()
    return {
        "books": [_book_out(b) for b in books],
        "members": [
            {"id": m.id, "username": m.username, "full_name": m.full_name}
            for m in members
        ],
        "zones": list(zones),
    }


# ── 통계 ────────────────────────────────────────────────────────────────────


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """대출 통계 + 작업 통계."""
    by_cat = db.execute(
        select(Book.category, func.count(Loan.id))
        .join(Loan, Loan.book_id == Book.id)
        .group_by(Book.category)
    ).all()
    top_books = db.execute(
        select(Book.title_kr, func.count(Loan.id).label("n"))
        .join(Loan, Loan.book_id == Book.id)
        .group_by(Book.id)
        .order_by(func.count(Loan.id).desc())
        .limit(10)
    ).all()
    top_members = db.execute(
        select(Member.username, func.count(Loan.id).label("n"))
        .join(Loan, Loan.member_id == Member.id)
        .group_by(Member.id)
        .order_by(func.count(Loan.id).desc())
        .limit(10)
    ).all()
    req_by_kind = db.execute(
        select(DeliveryRequest.kind, func.count(DeliveryRequest.id)).group_by(
            DeliveryRequest.kind
        )
    ).all()

    ok, orders = fms_client.list_orders()
    task_stats: dict[str, int] = {}
    for o in orders if ok else []:
        task_stats[o.get("status", "?")] = task_stats.get(o.get("status", "?"), 0) + 1

    return {
        "loans_by_category": [{"category": c, "count": n} for c, n in by_cat],
        "top_books": [{"title": t, "count": n} for t, n in top_books],
        "top_members": [{"username": u, "count": n} for u, n in top_members],
        "requests_by_kind": [{"kind": k, "count": n} for k, n in req_by_kind],
        "tasks_by_status": [{"status": k, "count": v} for k, v in task_stats.items()],
        "fleet_linked": ok,
    }
