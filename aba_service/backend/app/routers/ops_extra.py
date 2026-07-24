"""사서 운영 — 작업 알림 · 로그 · 정리/분류 리포트 · 야간 보안.

## 왜 작업 로그를 우리 DB에 쌓나
FMS orchestrator 는 **진행 중인 큐**만 들고 있고, 끝난 작업을 치우면 사라진다. 운영 돌아보기
(성공률·재시도·리포트)를 하려면 보관이 필요해서 `cb_task_logs` 에 스냅샷을 남긴다.
`/sync` 를 부르면 FMS 의 현재 종료 작업들을 가져와 없는 것만 적재한다(멱등).
"""

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import fms_client
from ..database import get_db
from ..models import AdminUser, IntrusionEvent, OpsSetting, TaskLog
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/ops", tags=["ops-extra"])

SECURITY_KEY = "security_mode"
NIGHT_START_KEY = "security_night_start"  # "HH:MM" — 야간 진입
NIGHT_END_KEY = "security_night_end"  # "HH:MM" — 주간 복귀
BOUNDARY_KEY = "security_last_boundary"  # 마지막으로 자동 적용한 경계 시각(ISO)
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
    stmt = select(TaskLog).where(TaskLog.hidden.is_(False))
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


@router.delete("/logs/{log_id}")
def delete_log(
    log_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    """작업로그 삭제 — hard delete 아님. `_sync_logs()`가 task_id 존재만으로 "이미 있음"을

    판단하므로, 행을 진짜로 지우면 FMS 가 여전히 같은 종료 task 를 돌려주는 한 다음 조회에서
    그대로 부활한다(`app/routers/ops_extra.py::_sync_logs`). `hidden=True` 로만 표시해
    목록 조회(`GET /logs`)에서 걸러내고, 감사를 위해 행 자체는 남긴다.
    """
    row = db.get(TaskLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="로그를 찾을 수 없습니다")
    row.hidden = True
    db.commit()
    return {"ok": True}


@router.get("/alerts")
def alerts(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """작업 알림 — 방금 끝난 작업(완료/실패)과 미확인 침입."""
    _sync_logs(db)
    since = datetime.now() - timedelta(hours=12)
    recent = db.scalars(
        select(TaskLog)
        .where(TaskLog.recorded_at >= since, TaskLog.hidden.is_(False))
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


# ── 야간 보안 ────────────────────────────────────────────────────────────────


class ModeRequest(BaseModel):
    mode: str  # day | night


class ScheduleRequest(BaseModel):
    night_start: str | None = None  # "HH:MM" — 야간 진입 시각
    night_end: str | None = None  # "HH:MM" — 주간 복귀 시각. 끄려면 둘 다 None.


class IntrusionReport(BaseModel):
    source: str
    zone: str | None = None
    note: str | None = None
    clip_path: str | None = None


def _get_mode(db: Session) -> str:
    row = db.get(OpsSetting, SECURITY_KEY)
    return row.value if row else "day"


def _get_setting(db: Session, key: str) -> str | None:
    row = db.get(OpsSetting, key)
    return row.value if row else None


def _put_setting(db: Session, key: str, value: str | None) -> None:
    """value 가 None/빈문자열이면 삭제, 아니면 upsert."""
    row = db.get(OpsSetting, key)
    if value:
        if row is None:
            db.add(OpsSetting(key=key, value=value))
        else:
            row.value = value
    elif row is not None:
        db.delete(row)


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _latest_boundary(
    now: datetime, night_start: time, night_end: time
) -> tuple[datetime, str]:
    """night_start/night_end 로 매일 반복되는 두 경계 중 now 이전(포함) 가장 최근 것.

    night_end 가 night_start 보다 이르면(예: 22:00~06:00) 자정을 넘는 야간으로 보고
    다음날로 넘겨 계산한다.
    """
    candidates: list[tuple[datetime, str]] = []
    for day_offset in (-1, 0):
        anchor = (now + timedelta(days=day_offset)).date()
        ns = datetime.combine(anchor, night_start)
        ne = datetime.combine(anchor, night_end)
        if ne <= ns:
            ne += timedelta(days=1)
        candidates.append((ns, "night"))
        candidates.append((ne, "day"))
    past = [c for c in candidates if c[0] <= now]
    return max(past, key=lambda c: c[0])


def _apply_schedule(db: Session, now: datetime | None = None) -> None:
    """설정된 시각이 있으면, 지나온 경계를 딱 한 번만 자동 반영한다.

    ponytail: 백그라운드 스케줄러 없이 이 화면이 조회(폴링)될 때 계산한다 — 관리자 화면이
    한동안 열려 있지 않으면 실제 전환은 다음 조회 때 반영된다(도서관 운영엔 충분, 화면과
    무관하게 정시에 돌아야 하면 APScheduler/systemd timer로 승격). 같은 경계 안에서는
    사서의 수동 전환을 덮어쓰지 않는다 — 경계를 새로 지날 때만 자동 적용.
    """
    start = _get_setting(db, NIGHT_START_KEY)
    end = _get_setting(db, NIGHT_END_KEY)
    if not start or not end:
        return  # 스케줄 미설정 — 수동 토글만 사용
    try:
        night_start, night_end = _parse_hhmm(start), _parse_hhmm(end)
    except ValueError:
        return

    boundary_at, desired_mode = _latest_boundary(now or datetime.now(), night_start, night_end)
    fingerprint = boundary_at.isoformat()
    if _get_setting(db, BOUNDARY_KEY) == fingerprint:
        return  # 이 경계는 이미 적용함

    _put_setting(db, SECURITY_KEY, desired_mode)
    _put_setting(db, BOUNDARY_KEY, fingerprint)
    db.commit()


@router.get("/security")
def security(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    _apply_schedule(db)
    events = db.scalars(
        select(IntrusionEvent).order_by(IntrusionEvent.detected_at.desc()).limit(50)
    ).all()
    return {
        "mode": _get_mode(db),
        "night_start": _get_setting(db, NIGHT_START_KEY),
        "night_end": _get_setting(db, NIGHT_END_KEY),
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
    _put_setting(db, SECURITY_KEY, body.mode)
    db.commit()
    return {"mode": body.mode}


@router.post("/security/schedule")
def set_schedule(
    body: ScheduleRequest,
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """야간 자동 전환 시각 저장. 둘 다 비우면 스케줄을 끄고 수동 토글만 남는다."""
    if bool(body.night_start) != bool(body.night_end):
        raise HTTPException(
            status_code=400, detail="시작·종료 시각을 둘 다 입력하거나 둘 다 비워주세요"
        )
    for v in (body.night_start, body.night_end):
        if v:
            try:
                _parse_hhmm(v)
            except ValueError:
                raise HTTPException(status_code=400, detail="시각은 HH:MM 형식이어야 합니다")
    if body.night_start and body.night_start == body.night_end:
        # 같으면 _latest_boundary 의 두 경계가 같은 시각으로 겹쳐 지문(fingerprint)이 서로를
        # 덮어써 자동 전환이 먹통이 된다 — 애초에 저장을 막는다.
        raise HTTPException(status_code=400, detail="시작·종료 시각은 달라야 합니다")

    _put_setting(db, NIGHT_START_KEY, body.night_start)
    _put_setting(db, NIGHT_END_KEY, body.night_end)
    # 스케줄이 바뀌면 예전 경계 기록은 무의미하다 — 다음 조회에서 즉시 재계산되게 지운다.
    _put_setting(db, BOUNDARY_KEY, None)
    db.commit()
    return {"night_start": body.night_start, "night_end": body.night_end}


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


@router.delete("/security/events/{event_id}")
def delete_intrusion_event(
    event_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)
):
    """침입이벤트 하드 삭제. `clip_path` 가 가리키는 파일 자체는 지우지 않는다(범위 밖)."""
    e = db.get(IntrusionEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    db.delete(e)
    db.commit()
    return {"ok": True}


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
