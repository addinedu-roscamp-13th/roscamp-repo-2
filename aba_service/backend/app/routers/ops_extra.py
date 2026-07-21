"""사서 운영 — 작업 알림 · 로그 · 정리/분류 리포트 · 야간 보안.

## 왜 작업 로그를 우리 DB에 쌓나
FMS orchestrator 는 **진행 중인 큐**만 들고 있고, 끝난 작업을 치우면 사라진다. 운영 돌아보기
(성공률·재시도·리포트)를 하려면 보관이 필요해서 `cb_task_logs` 에 스냅샷을 남긴다.
`/sync` 를 부르면 FMS 의 현재 종료 작업들을 가져와 없는 것만 적재한다(멱등).
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import fms_client
from ..database import get_db
from ..models import AdminUser, IntrusionEvent, OpsSetting, TaskLog
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/ops", tags=["ops-extra"])

SECURITY_KEY = "security_mode"
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


# ── 작업 로그 / 알림 ─────────────────────────────────────────────────────────


def _sync_logs(db: Session) -> int:
    """FMS 의 종료된 작업을 로그로 적재. 이미 있는 task_id 는 건너뛴다(멱등)."""
    ok, orders = fms_client.list_orders()
    if not ok:
        return 0
    known = {
        r for (r,) in db.execute(select(TaskLog.task_id)).all()
    }
    added = 0
    for o in orders:
        if o.get("status") not in TERMINAL or o.get("id") in known:
            continue
        db.add(
            TaskLog(
                task_id=o.get("id", ""),
                kind=(o.get("requester") or "").split(":")[-1] or o.get("task_type", ""),
                robot=o.get("robot"),
                status=o.get("status", ""),
                leg_idx=int(o.get("leg_idx") or 0),
                leg_count=int(o.get("leg_count") or 0),
                reason=(o.get("reason") or "")[:255] or None,
            )
        )
        added += 1
    if added:
        db.commit()
    return added


@router.post("/logs/sync")
def sync_logs(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return {"added": _sync_logs(db)}


@router.get("/logs")
def logs(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """작업 결과 로그. 조회할 때마다 FMS 를 한 번 훑어 새 종료 작업을 적재한다."""
    _sync_logs(db)
    stmt = select(TaskLog)
    if status_filter:
        stmt = stmt.where(TaskLog.status == status_filter)
    rows = db.scalars(stmt.order_by(TaskLog.recorded_at.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "task_id": r.task_id,
            "kind": r.kind,
            "robot": r.robot,
            "status": r.status,
            "leg_idx": r.leg_idx,
            "leg_count": r.leg_count,
            "retries": r.retries,
            "reason": r.reason,
            "recorded_at": r.recorded_at,
        }
        for r in rows
    ]


@router.get("/alerts")
def alerts(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """작업 알림 — 방금 끝난 작업(완료/실패)과 미확인 침입."""
    _sync_logs(db)
    since = datetime.now() - timedelta(hours=12)
    recent = db.scalars(
        select(TaskLog)
        .where(TaskLog.recorded_at >= since)
        .order_by(TaskLog.recorded_at.desc())
        .limit(30)
    ).all()
    intr = db.scalars(
        select(IntrusionEvent)
        .where(IntrusionEvent.acknowledged.is_(False))
        .order_by(IntrusionEvent.detected_at.desc())
        .limit(20)
    ).all()
    return {
        "tasks": [
            {
                "task_id": r.task_id,
                "kind": r.kind,
                "robot": r.robot,
                "status": r.status,
                "reason": r.reason,
                "at": r.recorded_at,
            }
            for r in recent
        ],
        "intrusions": [
            {
                "id": e.id,
                "source": e.source,
                "zone": e.zone,
                "note": e.note,
                "at": e.detected_at,
                "clip_path": e.clip_path,
            }
            for e in intr
        ],
    }


# ── 정리 · 분류 리포트 ───────────────────────────────────────────────────────


@router.get("/reports")
def reports(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """작업 종류별 수행 결과 — 잘 수행했는지(성공률), 분류/정리 결과."""
    _sync_logs(db)
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(TaskLog.kind, TaskLog.status, func.count(TaskLog.id))
        .where(TaskLog.recorded_at >= since)
        .group_by(TaskLog.kind, TaskLog.status)
    ).all()

    by_kind: dict[str, dict] = {}
    for kind, st, n in rows:
        k = by_kind.setdefault(
            kind or "?", {"kind": kind or "?", "completed": 0, "failed": 0, "cancelled": 0}
        )
        if st == "COMPLETED":
            k["completed"] += n
        elif st == "FAILED":
            k["failed"] += n
        else:
            k["cancelled"] += n

    out = []
    for k in by_kind.values():
        done = k["completed"] + k["failed"]
        k["total"] = k["completed"] + k["failed"] + k["cancelled"]
        # 성공률은 취소를 빼고 계산한다 — 사서가 취소한 것을 실패로 치면 로봇을 억울하게 만든다.
        k["success_rate"] = round(k["completed"] / done * 100) if done else None
        out.append(k)

    failures = db.scalars(
        select(TaskLog)
        .where(TaskLog.recorded_at >= since, TaskLog.status == "FAILED")
        .order_by(TaskLog.recorded_at.desc())
        .limit(20)
    ).all()

    return {
        "days": days,
        "by_kind": sorted(out, key=lambda x: -x["total"]),
        "recent_failures": [
            {
                "task_id": f.task_id,
                "kind": f.kind,
                "robot": f.robot,
                "reason": f.reason,
                "at": f.recorded_at,
            }
            for f in failures
        ],
    }


# ── 야간 보안 ────────────────────────────────────────────────────────────────


class ModeRequest(BaseModel):
    mode: str  # day | night


class IntrusionReport(BaseModel):
    source: str
    zone: str | None = None
    note: str | None = None
    clip_path: str | None = None


def _get_mode(db: Session) -> str:
    row = db.get(OpsSetting, SECURITY_KEY)
    return row.value if row else "day"


@router.get("/security")
def security(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    events = db.scalars(
        select(IntrusionEvent).order_by(IntrusionEvent.detected_at.desc()).limit(50)
    ).all()
    return {
        "mode": _get_mode(db),
        "events": [
            {
                "id": e.id,
                "detected_at": e.detected_at,
                "source": e.source,
                "zone": e.zone,
                "note": e.note,
                "clip_path": e.clip_path,
                "acknowledged": bool(e.acknowledged),
            }
            for e in events
        ],
    }


@router.post("/security/mode")
def set_mode(
    body: ModeRequest,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    if body.mode not in ("day", "night"):
        raise HTTPException(status_code=400, detail="mode 는 day 또는 night")
    row = db.get(OpsSetting, SECURITY_KEY)
    if row is None:
        db.add(OpsSetting(key=SECURITY_KEY, value=body.mode))
    else:
        row.value = body.mode
    db.commit()
    return {"mode": body.mode}


@router.post("/security/events", status_code=status.HTTP_201_CREATED)
def report_intrusion(body: IntrusionReport, db: Session = Depends(get_db)):
    """침입 보고 — **로봇/AI 서비스가 부른다.** 사서 인증을 요구하지 않는다.

    감지 주체는 사람이 아니라 기계이므로 관리자 토큰을 들고 있을 수 없다. 대신 기록만
    남기고 아무 권한도 주지 않는다(읽기는 사서 인증 필요).
    """
    e = IntrusionEvent(
        source=body.source, zone=body.zone, note=body.note, clip_path=body.clip_path
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"id": e.id, "detected_at": e.detected_at}


@router.post("/security/events/{event_id}/ack")
def ack_intrusion(
    event_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    e = db.get(IntrusionEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    e.acknowledged = True
    db.commit()
    return {"ok": True}
