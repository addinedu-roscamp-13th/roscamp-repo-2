"""사서 운영 — 작업 알림 · 로그 · 정리/분류 리포트 · 야간 보안.

## 왜 작업 로그를 우리 DB에 쌓나
FMS orchestrator 는 **진행 중인 큐**만 들고 있고, 끝난 작업을 치우면 사라진다. 운영 돌아보기
(성공률·재시도·리포트)를 하려면 보관이 필요해서 `cb_task_logs` 에 스냅샷을 남긴다.
`/sync` 를 부르면 FMS 의 현재 종료 작업들을 가져와 없는 것만 적재한다(멱등).
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import fms_client
from ..config import get_settings
from ..database import get_db
from ..models import AdminUser, IntrusionEvent, OpsSetting, TaskLog
from ..security import get_current_admin

router = APIRouter(prefix="/api/admin/ops", tags=["ops-extra"])

SECURITY_KEY = "security_mode"
NIGHT_START_KEY = "security_night_start"  # "HH:MM" — 야간 진입
NIGHT_END_KEY = "security_night_end"  # "HH:MM" — 주간 복귀
BOUNDARY_KEY = "security_last_boundary"  # 마지막으로 자동 적용한 경계 시각(ISO)
EVENT_SEQ_KEY = "tasklog_event_seq"  # 배정 사건을 어디까지 적재했는지 (fleet_events seq)
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
#: 로그에 남기는 상태 — 배정 시점 하나 + 결과 셋. EXECUTING 은 그 사이 상태라 굳이 안 남긴다.
TRACKED = TERMINAL | {"ASSIGNED"}
#: fleet_events 사건 종류 → 작업 로그 상태. 이 사건들만 실제 ts 로 로그에 반영한다.
_EVENT_STATUS = {"task_started": "ASSIGNED", "task_done": "COMPLETED", "task_failed": "FAILED"}


# ── 작업 로그 / 알림 ─────────────────────────────────────────────────────────


def _sync_logs(db: Session) -> int:
    """FMS 작업을 로그로 적재 — 배정(ASSIGNED) 시점 한 번, 종료(COMPLETED/FAILED/CANCELLED)
    시점에 그 행을 업데이트한다. 같은 task_id 로 두 번 적재하지 않는다(멱등)."""
    ok, orders = fms_client.list_orders()
    if not ok:
        return 0
    existing = {
        r.task_id: r for r in db.scalars(select(TaskLog)).all()
    }
    added = 0
    for o in orders:
        status = o.get("status")
        if status not in TRACKED:
            continue
        task_id = o.get("id", "")
        row = existing.get(task_id)
        if row is not None:
            if row.status != status:
                row.status = status
                row.leg_idx = int(o.get("leg_idx") or 0)
                row.leg_count = int(o.get("leg_count") or 0)
                row.reason = (o.get("reason") or "")[:255] or None
                if status in TERMINAL:
                    # 배정 시점에 적재한 행을 종료 시점으로 갱신 — 최근 알림이 실제
                    # 종료 시각 기준으로 뜨도록 기록 시각도 함께 옮긴다.
                    row.recorded_at = datetime.now()
                added += 1
            continue
        db.add(
            TaskLog(
                task_id=task_id,
                kind=(o.get("requester") or "").split(":")[-1] or o.get("task_type", ""),
                robot=o.get("robot"),
                status=status,
                leg_idx=int(o.get("leg_idx") or 0),
                leg_count=int(o.get("leg_count") or 0),
                reason=(o.get("reason") or "")[:255] or None,
            )
        )
        added += 1
    if added:
        db.commit()
    return added


_APP_TZ = ZoneInfo(get_settings().app_timezone)


def _event_dt(e: dict) -> datetime:
    """사건이 실제로 일어난 시각. fleet_events 의 `ts`(unix=UTC epoch)를 앱 표준
    naive-local(app_timezone) 로 **명시 환산**한다.

    ts 는 외부(fms)에서 온 UTC epoch 이라, 프로세스 tz pin(config._pin_process_timezone)에
    암묵 의존하지 않고 UTC→app_timezone 을 직접 변환한 뒤 tzinfo 를 벗겨 저장한다 — 그래야
    UTC 호스트든 KST 호스트든 같은 벽시계 값이 나온다(pin 이 곧 앱 계약이지만, 외부 epoch 은
    명시가 안전하다). 저장 형식은 이 표의 다른 행(func.now()/datetime.now())과 같은 naive-local.
    ts 가 없거나 이상하면 지금 시각으로 폴백한다 — 시각이 비는 것보단 낫다.
    """
    ts = e.get("ts")
    try:
        aware = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        aware = datetime.now(timezone.utc)
    return aware.astimezone(_APP_TZ).replace(tzinfo=None)


def _sync_events(db: Session) -> int:
    """배정(ASSIGNED) 순간을 **사건**으로 잡아 적재한다.

    왜 사건인가: orchestrator 는 `assign()` 한 호출 안에서 ASSIGNED→EXECUTING 을 원자적으로
    밟아, 상태 스냅샷(`list_orders`)을 아무리 폴링해도 ASSIGNED 를 절대 못 본다. 또 order
    스냅샷엔 **시각이 없어** 종료 시각을 실제로 알 수 없다. 사건에는 `ts`(실제 발생 시각)가
    있으므로, 배정(task_started)·종료(task_done/failed)를 사건으로 잡아 **진짜 시각**으로 남긴다.

    `_sync_logs`(order 폴링)와 같은 행을 다루지만, 사건이 먼저 종료 상태를 실제 ts 로 박아
    두면 `_sync_logs` 는 상태가 안 바뀌어 건너뛴다(덮어쓰지 않음). 사건 버퍼(fleet_events)는
    링이라 폴링 간격이 너무 벌어지면 일부 사건은 놓칠 수 있고, 그 땐 `_sync_logs` 가 폴링
    시각으로 종료를 남기는 폴백이 된다 — 알림 성격이라 감수한다.
    """
    since = int(_get_setting(db, EVENT_SEQ_KEY) or 0)
    ok, events, latest = fms_client.list_events(since, limit=200)
    if not ok:
        return 0
    rows = {r.task_id: r for r in db.scalars(select(TaskLog)).all()}
    changed = 0
    for e in events:
        status = _EVENT_STATUS.get(e.get("kind"))
        if not status:
            continue
        task_id = e.get("task_id", "")
        if not task_id:
            continue
        when = _event_dt(e)  # 실제 발생 시각(사건 ts). 폴링 시각 아님.
        # task_failed 사건 text 는 "실패: {사유}" — 접두 벗겨 사유만 남긴다.
        reason = None
        if status == "FAILED":
            text = e.get("text") or ""
            reason = text[len("실패: "):] if text.startswith("실패: ") else (text or None)
        row = rows.get(task_id)
        if row is None:
            row = TaskLog(
                task_id=task_id,
                kind=(e.get("requester") or "").split(":")[-1] or e.get("leg_kind", ""),
                robot=e.get("robot"),
                status=status,
                leg_idx=int(e.get("leg_idx") or 0),
                leg_count=int(e.get("leg_count") or 0),
                reason=reason,
                recorded_at=when,
            )
            db.add(row)
            rows[task_id] = row
            changed += 1
        elif status != "ASSIGNED" and row.status != status:
            # 배정→종료 갱신. 종료 실제 시각(when)으로 기록. task_started 재수신은 무시(위 조건).
            row.status = status
            row.leg_idx = int(e.get("leg_idx") or row.leg_idx)
            row.leg_count = int(e.get("leg_count") or row.leg_count)
            if reason:
                row.reason = reason
            row.recorded_at = when
            changed += 1
    if latest != since or changed:
        _put_setting(db, EVENT_SEQ_KEY, str(latest))
        db.commit()
    return changed


@router.post("/logs/sync")
def sync_logs(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    return {"added": _sync_events(db) + _sync_logs(db)}


@router.get("/logs")
def logs(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: AdminUser = Depends(get_current_admin),
):
    """작업 결과 로그. 조회할 때마다 배정 사건 + FMS 종료 작업을 적재한다."""
    _sync_events(db)  # 배정(ASSIGNED)은 사건으로만 잡힌다 — 종료 갱신보다 먼저
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


@router.post("/logs/reset")
def reset_logs(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """작업 로그 초기화 — 지금 보이는(hidden=False) 로그를 전부 숨긴다.

    per-row 삭제와 같은 **soft(hidden) 방식**이다. 하드 삭제하면 FMS 가 같은 종료 task 를
    계속 돌려줘(`_sync_logs` 가 task_id 존재만으로 재수입) 다음 조회에서 그대로 부활한다.
    감사 위해 행은 남기고 목록에서만 감춘다. 새로 생기는 작업은 계속 쌓인다.
    """
    n = (
        db.query(TaskLog)
        .filter(TaskLog.hidden.is_(False))
        .update({TaskLog.hidden: True}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "hidden": n}


@router.get("/alerts")
def alerts(db: Session = Depends(get_db), _: AdminUser = Depends(get_current_admin)):
    """작업 알림 — 방금 끝난 작업(완료/실패)과 미확인 침입."""
    _sync_events(db)  # 배정 사건도 함께 — 종료 갱신보다 먼저
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


def _drive_fleet(mode: str) -> list[dict]:
    """운영모드 전환 시 로봇을 **실제로** 전이시킨다.

    「순찰」버튼(ops.create_task, kind=patrol → fms_client.request_transition(robot, "PATROL"))과
    **같은 경로**를 쓰되, target 이 SECURITY_PATROL 이고 대상이 전체 로봇이다.
      - night: 스냅샷의 모든 로봇 → SECURITY_PATROL
      - day  : 지금 SECURITY_PATROL 인 로봇만 → IDLE (그 외 상태는 건드리지 않는다)

    force=False 라 로봇이 못 가는 전이는 로봇이 거부하고 그 사유가 결과에 담긴다 — 순찰
    버튼과 **똑같은 제약**이다(전이표상 SECURITY_PATROL 진입은 IDLE→SECURITY_PATROL 하나뿐).
    FMS 미연결이면 빈 목록으로 조용히 물러난다 — 화면의 모드 플래그는 그대로 바뀐다.
    """
    ok, snap = fms_client.fleet_snapshot()
    if not ok:
        return []
    target = "SECURITY_PATROL" if mode == "night" else "IDLE"
    results: list[dict] = []
    for r in snap.get("robots", []):
        name = r.get("name")
        if not name:
            continue
        # 주간 복귀는 야간 순찰 중이던 로봇만 되돌린다(충전·작업 중인 로봇은 안 건드림).
        if mode == "day" and r.get("state") != "SECURITY_PATROL":
            continue
        # 야간 진입은 force=True — 전이표상 SECURITY_PATROL 진입은 IDLE→SECURITY_PATROL 하나뿐인데
        # 로봇은 낮에 PATROL 이라, IDLE 을 거치면 그 순간 auto-PATROL 이 낚아채 진입이 어긋난다.
        # 그래서 PATROL→SECURITY_PATROL 을 강제로 넣고, 로봇 쪽 security_patrol 브랜치가 그 상태를
        # 계속 물고 있게(반복 순찰) 바꿔 두었다. 주간 복귀(→IDLE)는 정규 간선이라 force 불필요.
        called, res = fms_client.request_transition(name, target, force=(mode == "night"))
        results.append({
            "robot": name,
            "accepted": bool(res.get("accepted")) if called else False,
            "reason": res.get("reason", ""),
        })
    return results


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
    # 경계를 새로 지날 때만(위 fingerprint 가드) 로봇을 전이시킨다 — 폴링마다가 아니다.
    # 수동 버튼과 같은 _drive_fleet 를 태워, 자동/수동이 같은 결과를 내게 한다.
    _drive_fleet(desired_mode)


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
    # 순찰 버튼과 같은 경로로 로봇을 실제 전이시킨다(night→SECURITY_PATROL, day→IDLE).
    # 로봇별 수락/거부 결과를 돌려주면 화면이 "N대 순찰 / M대 거부"로 요약해 보여준다.
    results = _drive_fleet(body.mode)
    return {"mode": body.mode, "results": results}


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
