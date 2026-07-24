"""_sync_events — 배정(ASSIGNED) 사건을 로그로 적재하되 종료 상태는 되돌리지 않는지."""

from app.models import TaskLog
from app.routers import ops_extra


def _started_event(task_id="t1", seq=5):
    return {
        "seq": seq,
        "kind": "task_started",
        "task_id": task_id,
        "robot": "Pinky-sim-1",
        "requester": "member:이강택",
        "status": "ASSIGNED",
        "leg_idx": 0,
        "leg_count": 4,
    }


def test_배정_사건이_ASSIGNED_로그로_적재된다(db_session, monkeypatch):
    monkeypatch.setattr(
        ops_extra.fms_client, "list_events",
        lambda since, limit=50: (True, [_started_event()], 5),
    )
    added = ops_extra._sync_events(db_session)
    assert added == 1
    row = db_session.query(TaskLog).filter(TaskLog.task_id == "t1").one()
    assert row.status == "ASSIGNED"
    assert row.robot == "Pinky-sim-1"
    assert row.kind == "이강택"  # requester 의 마지막 조각
    assert row.leg_count == 4


def test_같은_task_는_두_번_적재되지_않는다(db_session, monkeypatch):
    monkeypatch.setattr(
        ops_extra.fms_client, "list_events",
        lambda since, limit=50: (True, [_started_event()], 5),
    )
    ops_extra._sync_events(db_session)
    ops_extra._sync_events(db_session)  # 재실행 — 이미 있는 task_id
    assert db_session.query(TaskLog).filter(TaskLog.task_id == "t1").count() == 1


def test_종료된_행은_배정으로_되돌리지_않는다(db_session, monkeypatch):
    # _sync_logs 가 먼저 FAILED 로 남긴 상황: 늦게 도착한 배정 사건이 덮으면 안 된다.
    db_session.add(TaskLog(task_id="t1", kind="이강택", robot="Pinky-sim-1",
                           status="FAILED", leg_idx=0, leg_count=4, reason="x"))
    db_session.commit()
    monkeypatch.setattr(
        ops_extra.fms_client, "list_events",
        lambda since, limit=50: (True, [_started_event()], 5),
    )
    ops_extra._sync_events(db_session)
    row = db_session.query(TaskLog).filter(TaskLog.task_id == "t1").one()
    assert row.status == "FAILED"  # 그대로


def test_종료_사건이_실제_시각으로_기록된다(db_session, monkeypatch):
    from datetime import datetime

    started = _started_event(seq=5)              # ts=5 → 1970 근처
    done = {"seq": 6, "kind": "task_done", "task_id": "t1",
            "robot": "Pinky-sim-1", "requester": "member:이강택",
            "status": "COMPLETED", "leg_idx": 4, "leg_count": 4, "ts": 5}
    monkeypatch.setattr(
        ops_extra.fms_client, "list_events",
        lambda since, limit=50: (True, [started, done], 6),
    )
    ops_extra._sync_events(db_session)
    row = db_session.query(TaskLog).filter(TaskLog.task_id == "t1").one()
    assert row.status == "COMPLETED"
    # 폴링 시각(now)이 아니라 사건 ts(1970)로 기록됐는지 — 오늘이 아님을 확인.
    # ts=5 는 UTC epoch → 앱 표준 KST(Asia/Seoul, +09:00) 벽시계 1970-01-01 09:00:05.
    # 리터럴로 못박아 **머신 TZ 와 무관하게** UTC→KST(+9) 변환을 증명한다(naive 저장).
    assert row.recorded_at == datetime(1970, 1, 1, 9, 0, 5)
    assert row.recorded_at.tzinfo is None  # 표 전체와 같은 naive-local 계약
    assert row.recorded_at.year == 1970


def test_실패_사건_사유_접두_벗긴다(db_session, monkeypatch):
    failed = {"seq": 7, "kind": "task_failed", "task_id": "t9",
              "robot": "Pinky-sim-2", "requester": "member:이강택",
              "status": "FAILED", "leg_idx": 0, "leg_count": 4,
              "text": "실패: 배터리 부족", "ts": 5}
    monkeypatch.setattr(
        ops_extra.fms_client, "list_events",
        lambda since, limit=50: (True, [failed], 7),
    )
    ops_extra._sync_events(db_session)
    row = db_session.query(TaskLog).filter(TaskLog.task_id == "t9").one()
    assert row.status == "FAILED"
    assert row.reason == "배터리 부족"  # "실패: " 접두 제거


def test_링크_실패면_커서_안_움직인다(db_session, monkeypatch):
    monkeypatch.setattr(
        ops_extra.fms_client, "list_events",
        lambda since, limit=50: (False, [], since),
    )
    assert ops_extra._sync_events(db_session) == 0
    assert ops_extra._get_setting(db_session, ops_extra.EVENT_SEQ_KEY) is None
