"""사서 운영 — 작업 알림 · 로그 · 정리 리포트 · 야간 보안.

## 왜 작업 로그를 우리 DB에 쌓나
FMS orchestrator 는 **진행 중인 큐**만 들고 있고, 끝난 작업을 치우면 사라진다. 운영 돌아보기
(성공률·재시도·리포트)를 하려면 보관이 필요해서 `cb_task_logs` 에 스냅샷을 남긴다.
`/sync` 를 부르면 FMS 의 현재 종료 작업들을 가져와 없는 것만 적재한다(멱등).
"""

import os
import re
import time as _time
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
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


# ── 정리 리포트 ────────────────────────────────────────────────────────────


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


class ClipPatch(BaseModel):
    clip_path: str


#: `<video src>` 가 읽을 파일명 — uuid4 + `.mp4` 만 허용한다.
#: 느슨하게 두면 `../../etc/passwd` 같은 경로 탈출이 그대로 통한다.
#: `\A`/`\Z` 를 쓴다 — `^`/`$` 는 문자열 끝의 `\n` 하나를 허용해 검증이 새는 지점이 된다.
_CLIP_NAME = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.mp4\Z")

#: attach_clip 이 받아들이는 clip_path 의 유일한 형태 — 우리 자신이 서빙하는
#: /security/clips/{uuid}.mp4 상대경로뿐이다.
_CLIP_PATH_PREFIX = "/api/admin/ops/security/clips/"

#: 클립 저장 위치. AI 서버(같은 노트북)가 여기에 쓰고 이 라우터가 서빙한다.
SECURITY_MEDIA_DIR = Path(
    os.environ.get("SECURITY_MEDIA_DIR",
                   Path(__file__).resolve().parents[2] / "media" / "security"))

#: AI 서버가 마지막으로 모드를 읽은 시각(monotonic). 화면의 「감지 켜짐」 뱃지 근거다.
#: DB 에 안 넣는다 — 프로세스가 살아 있는 동안만 의미가 있는 값이다.
_ai_seen_at: float = 0.0
AI_ALIVE_SEC = 30.0


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
        "ai_alive": (_time.monotonic() - _ai_seen_at) < AI_ALIVE_SEC,
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


def _zone_from_fleet(source: str | None) -> str | None:
    """AI 서버는 로봇이 어느 정점에 있는지 모른다 — 여기서 채운다.

    `_drive_fleet` 이 이미 쓰는 같은 스냅샷을 본다. **조회가 실패해도 저장을 막지
    않는다** — 알림이 먼저다. 이름이 안 맞으면 위치만 비고 나머지는 정상이다
    (로봇 이름 표기 불일치는 이 레포의 알려진 함정 — fleet_link.py `_fsm_entry`).

    `timeout=1` — 기본 `TIMEOUT_SEC`(8초, 재로그인 재시도 포함 최대 ~24초)를 쓰면 AI
    서비스의 `OpsSink.report()`(3초 타임아웃)가 먼저 끊겨 `event_id` 를 못 받고, 뒤이은
    `attach_clip` 이 조용히 스킵된다. 위치 조회는 알림을 지연시키면 안 된다.
    """
    if not source:
        return None
    try:
        ok, snap = fms_client.fleet_snapshot(timeout=1)
    except Exception:                                           # noqa: BLE001
        return None
    if not ok:
        return None
    for r in (snap or {}).get("robots", []):
        if r.get("name") == source:
            return r.get("node") or r.get("zone") or None
    return None


@router.post("/security/events", status_code=status.HTTP_201_CREATED)
def report_intrusion(body: IntrusionReport, db: Session = Depends(get_db)):
    """침입 보고 — **로봇/AI 서비스가 부른다.** 사서 인증을 요구하지 않는다.

    감지 주체는 사람이 아니라 기계이므로 관리자 토큰을 들고 있을 수 없다. 대신 기록만
    남기고 아무 권한도 주지 않는다(읽기는 사서 인증 필요).
    """
    zone = body.zone or _zone_from_fleet(body.source)
    e = IntrusionEvent(
        source=body.source, zone=zone, note=body.note, clip_path=body.clip_path
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"id": e.id, "detected_at": e.detected_at}


@router.get("/security/mode")
def security_mode(db: Session = Depends(get_db)):
    """운영 모드만 돌려준다 — **AI 서버가 폴링한다. 인증을 요구하지 않는다.**

    기계가 부르므로 관리자 토큰을 들고 있을 수 없다 — `report_intrusion` 과 같은 논리다.
    모드 문자열 하나뿐이라 노출되는 정보도 없다.
    """
    global _ai_seen_at
    _ai_seen_at = _time.monotonic()
    return {"mode": _get_mode(db)}


@router.patch("/security/events/{event_id}/clip")
def attach_clip(event_id: int, body: ClipPatch, db: Session = Depends(get_db)):
    """클립이 완성된 뒤 경로만 붙인다 — **무인증**(감지 측이 부른다).

    왜 이벤트 생성과 나눴나: 클립은 30초 넘게 걸린다. 완성 후 한 번에 보내면 알림이
    그만큼 늦어 알림 구실을 못 한다. 반대로 경로를 미리 박으면 `<video>` 가 404 를
    물고 마운트되어 **같은 src 로는 재마운트되지 않아 영영 깨진 채 남는다.**

    무인증 엔드포인트가 clip_path 를 검증 없이 저장하면 임의의 호출자가 기존
    이벤트의 clip_path 를 외부 URL로 바꿔치기할 수 있다 — _CLIP_PATH_PREFIX 로
    우리 자신이 서빙하는 경로 형태만 허용해 막는다.
    """
    if not body.clip_path.startswith(_CLIP_PATH_PREFIX) or not _CLIP_NAME.match(
        body.clip_path[len(_CLIP_PATH_PREFIX):]
    ):
        raise HTTPException(status_code=400, detail="잘못된 clip_path 형식입니다")
    e = db.get(IntrusionEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    e.clip_path = body.clip_path
    db.commit()
    return {"ok": True}


@router.get("/security/clips/{name}")
def security_clip(name: str):
    """클립 파일 서빙 — **무인증**. `<video src>` 는 인증 헤더를 못 싣는다.

    파일명이 uuid4 라 추측이 불가능하고, 형식을 엄격히 검증해 경로 탈출을 막는다.
    `FileResponse` 는 Range 요청을 지원하므로 재생기의 탐색(seek)이 동작한다.
    """
    if not _CLIP_NAME.match(name):
        raise HTTPException(status_code=400, detail="잘못된 파일명")
    path = SECURITY_MEDIA_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다")
    return FileResponse(path, media_type="video/mp4")


@router.get("/security/live.jpg")
def security_live_snapshot():
    """야간 감시 라이브 스냅샷 — **무인증**(`<img src>` 도 clip 처럼 인증 헤더를 못 싣는다).

    AI 서버(`perception_server.py --security`)가 `SECURITY_MEDIA_DIR/live.jpg` 를
    1초마다 덮어쓴다 — secview/패널 중 누가 뷰어 자리를 잡고 있든 상관없이 갱신된다
    (serve_loop 이 프레임을 처리할 때마다 쓰는 것이라, 자리싸움과 무관하다).
    파일이 없거나(--security 안 켰거나 아직 시작 전) 5초 넘게 안 갱신됐으면(뷰어가
    아무도 안 붙어 serve_loop 자체가 안 도는 중) 404 — 프론트가 "라이브 없음"으로 안다.
    """
    path = SECURITY_MEDIA_DIR / "live.jpg"
    if not path.is_file() or (_time.time() - path.stat().st_mtime) > 5.0:
        raise HTTPException(status_code=404, detail="라이브 영상이 없습니다")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


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
