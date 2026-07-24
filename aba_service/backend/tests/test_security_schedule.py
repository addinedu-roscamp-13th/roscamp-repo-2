"""야간 자동 전환 스케줄 — 시각 저장 + 경계를 지나면 자동으로 모드가 바뀌는지.

`_apply_schedule` 은 백그라운드 스케줄러 없이 GET /security 조회 시점에 계산한다
(ops_extra.py 의 ponytail 주석 참고). 경계 계산(`_latest_boundary`)은 순수 함수라
직접 단위 테스트하고, 자동 전환 자체는 `now` 를 주입해 검증한다.
"""

from datetime import datetime, time

from app.routers.ops_extra import _apply_schedule, _get_mode, _latest_boundary

SECURITY = "/api/admin/ops/security"
SCHEDULE = "/api/admin/ops/security/schedule"
MODE = "/api/admin/ops/security/mode"


def test_스케줄_미설정시_night_start_end_는_None(client, admin_auth):
    body = client.get(SECURITY, headers=admin_auth).json()
    assert body["night_start"] is None
    assert body["night_end"] is None


def test_스케줄_저장하면_조회에_반영(client, admin_auth):
    res = client.post(
        SCHEDULE, json={"night_start": "22:00", "night_end": "06:00"}, headers=admin_auth
    )
    assert res.status_code == 200, res.text

    body = client.get(SECURITY, headers=admin_auth).json()
    assert body["night_start"] == "22:00"
    assert body["night_end"] == "06:00"


def test_잘못된_형식은_400(client, admin_auth):
    res = client.post(
        SCHEDULE, json={"night_start": "22시", "night_end": "06:00"}, headers=admin_auth
    )
    assert res.status_code == 400


def test_시작만_넣으면_400(client, admin_auth):
    res = client.post(
        SCHEDULE, json={"night_start": "22:00", "night_end": None}, headers=admin_auth
    )
    assert res.status_code == 400


def test_둘다_비우면_스케줄_해제(client, admin_auth):
    client.post(SCHEDULE, json={"night_start": "22:00", "night_end": "06:00"}, headers=admin_auth)
    res = client.post(SCHEDULE, json={"night_start": None, "night_end": None}, headers=admin_auth)
    assert res.status_code == 200

    body = client.get(SECURITY, headers=admin_auth).json()
    assert body["night_start"] is None
    assert body["night_end"] is None


def test_로그인_없이_스케줄_저장_불가(client):
    res = client.post(SCHEDULE, json={"night_start": "22:00", "night_end": "06:00"})
    assert res.status_code == 401


def test_시작_종료가_같으면_400(client, admin_auth):
    # 같으면 _latest_boundary 의 두 경계가 겹쳐 fingerprint 가 서로를 덮어쓰고
    # 자동 야간 전환이 먹통이 된다 — 저장 시점에 막는다.
    res = client.post(
        SCHEDULE, json={"night_start": "08:00", "night_end": "08:00"}, headers=admin_auth
    )
    assert res.status_code == 400


def test_자정넘는_야간중_경계는_전날_night_start():
    now = datetime(2026, 7, 23, 23, 30)  # 22:00~06:00 스케줄이면 이 시각은 야간
    boundary_at, mode = _latest_boundary(now, time(22, 0), time(6, 0))
    assert mode == "night"
    assert boundary_at == datetime(2026, 7, 23, 22, 0)


def test_새벽_night_end_직후는_주간_경계():
    now = datetime(2026, 7, 24, 6, 30)  # night_end(06:00) 를 막 지남
    boundary_at, mode = _latest_boundary(now, time(22, 0), time(6, 0))
    assert mode == "day"
    assert boundary_at == datetime(2026, 7, 24, 6, 0)


def test_경계_지나면_자동으로_모드_전환(client, admin_auth, db_session):
    # GET /security 는 항상 실제 현재시각으로 _apply_schedule 을 재호출하므로, 이 아래
    # 두 테스트는 client.get() 을 거치지 않고 DB 상태를 직접 확인한다 — 그래야 주입한
    # 과거 시각이 실제 시각 호출에 덮이지 않는다.
    client.post(SCHEDULE, json={"night_start": "22:00", "night_end": "06:00"}, headers=admin_auth)
    client.post(MODE, json={"mode": "day"}, headers=admin_auth)  # 수동으로 주간 유지 중이라도

    _apply_schedule(db_session, now=datetime(2026, 7, 23, 23, 0))  # 22:00 경계를 지남
    db_session.commit()

    assert _get_mode(db_session) == "night"


def test_같은_경계_안에서는_수동_전환을_덮어쓰지_않음(client, admin_auth, db_session):
    client.post(SCHEDULE, json={"night_start": "22:00", "night_end": "06:00"}, headers=admin_auth)
    _apply_schedule(db_session, now=datetime(2026, 7, 23, 23, 0))
    db_session.commit()

    client.post(MODE, json={"mode": "day"}, headers=admin_auth)  # 사서가 야간 중 수동 복귀

    # 같은 경계 안(다음 경계 전)에서 자동전환 로직을 다시 돌려도 방금 준 수동 설정을 덮으면 안 됨
    _apply_schedule(db_session, now=datetime(2026, 7, 23, 23, 30))
    db_session.commit()
    assert _get_mode(db_session) == "day"
