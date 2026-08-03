"""야간 보안 — 기계용 무인증 엔드포인트 3개와 위치 채우기.

기계(로봇·AI 서버)가 부르므로 관리자 토큰이 없다. 기존 `POST /security/events` 가
이미 같은 논리로 무인증이다(ops_extra.py 의 그 함수 독스트링).
"""
import re

CLIP = "0123abcd-0123-0123-0123-0123456789ab.mp4"


def test_mode_는_인증_없이_읽힌다(client):
    r = client.get("/api/admin/ops/security/mode")
    assert r.status_code == 200
    assert r.json()["mode"] in ("day", "night")


def test_mode_가_토글을_따라간다(client, admin_auth, fms):
    client.post("/api/admin/ops/security/mode", json={"mode": "night"}, headers=admin_auth)
    assert client.get("/api/admin/ops/security/mode").json()["mode"] == "night"


def test_clip_경로를_뒤늦게_붙일_수_있다(client, admin_auth):
    created = client.post("/api/admin/ops/security/events",
                          json={"source": "pinky-3", "note": "야간 순찰 중 인기척 감지"})
    eid = created.json()["id"]
    path = f"/api/admin/ops/security/clips/{CLIP}"

    r = client.patch(f"/api/admin/ops/security/events/{eid}/clip", json={"clip_path": path})
    assert r.status_code == 200

    events = client.get("/api/admin/ops/security", headers=admin_auth).json()["events"]
    assert [e for e in events if e["id"] == eid][0]["clip_path"] == path


def test_없는_이벤트에_붙이면_404(client):
    # clip_path 는 우리 서빙 경로 형식을 갖춰야 한다 — 형식 검증(400)이 존재 조회(404)보다
    # 먼저 돌므로, 형식이 안 맞으면 이 시험이 원래 노리는 404(이벤트 없음)를 가리지 못한다.
    path = f"/api/admin/ops/security/clips/{CLIP}"
    r = client.patch("/api/admin/ops/security/events/999999/clip", json={"clip_path": path})
    assert r.status_code == 404


def test_clip_path가_외부_URL이면_거부하고_기존값을_보존한다(client, admin_auth):
    """무인증 엔드포인트라 임의 호출자가 외부 URL로 바꿔치기할 수 있으면 안 된다."""
    created = client.post("/api/admin/ops/security/events",
                          json={"source": "pinky-3", "note": "x"})
    eid = created.json()["id"]

    r = client.patch(f"/api/admin/ops/security/events/{eid}/clip",
                     json={"clip_path": "http://evil.example/x"})
    assert r.status_code == 400

    events = client.get("/api/admin/ops/security", headers=admin_auth).json()["events"]
    assert [e for e in events if e["id"] == eid][0]["clip_path"] is None


def test_clip_파일명이_uuid_형식이_아니면_거부한다(client, monkeypatch, tmp_path):
    from app.routers import ops_extra

    # 실제 media/security/ 에 쓰지 않는다 — 이 시험 동안만 tmp_path 로 갈아끼운다.
    monkeypatch.setattr(ops_extra, "SECURITY_MEDIA_DIR", tmp_path)

    # 파일이 실제로 있어도 형식이 틀리면 열어보지도 않고 거부해야 한다 — 그래야
    # "파일이 없어서 404"와 "형식이 틀려서 400"이 구분 안 되는 헛도는 검증을 막는다.
    real_bad_file = tmp_path / "not-a-uuid.mp4"
    real_bad_file.write_bytes(b"fake")
    r = client.get(f"/api/admin/ops/security/clips/{real_bad_file.name}")
    assert r.status_code == 400

    # 경로 탈출 — Starlette 라우팅 자체가 `{name}` 에 `/` 를 안 태워 우리 코드까지
    # 오지도 않는다(404). 500 이나 유출 없이 안전하게 막히는지만 느슨히 확인한다.
    assert client.get("/api/admin/ops/security/clips/../../etc/passwd").status_code in (400, 404)

    # 확장자 불일치 — 둘 다 안전한 결과라 느슨하게 확인.
    assert client.get(
        "/api/admin/ops/security/clips/0123abcd-0123-0123-0123-0123456789ab.exe"
    ).status_code in (400, 404)


def test_clip_이_없으면_404(client):
    assert client.get(f"/api/admin/ops/security/clips/{CLIP}").status_code == 404


def test_zone_을_주면_그대로_쓴다(client, admin_auth, fms):
    """기존 호출자(시드·수동 보고)의 동작이 안 바뀌어야 한다."""
    client.post("/api/admin/ops/security/events",
                json={"source": "pinky-3", "zone": "예술서가", "note": "x"})
    events = client.get("/api/admin/ops/security", headers=admin_auth).json()["events"]
    assert events[0]["zone"] == "예술서가"


def test_zone_이_비면_FMS_스냅샷에서_채운다(client, admin_auth, fms, monkeypatch):
    from app.routers import ops_extra
    monkeypatch.setattr(
        ops_extra.fms_client, "fleet_snapshot",
        lambda *a, **k: (True, {"robots": [{"name": "pinky-3", "node": "과학-인문학서가"}]}))
    client.post("/api/admin/ops/security/events", json={"source": "pinky-3", "note": "x"})
    events = client.get("/api/admin/ops/security", headers=admin_auth).json()["events"]
    assert events[0]["zone"] == "과학-인문학서가"


def test_FMS_가_죽어도_이벤트는_저장된다(client, admin_auth, fms, monkeypatch):
    """알림이 먼저다. 위치 조회 실패가 보고를 막으면 안 된다."""
    from app.routers import ops_extra
    def boom(*a, **k):
        raise OSError("연결 거부")
    monkeypatch.setattr(ops_extra.fms_client, "fleet_snapshot", boom)
    r = client.post("/api/admin/ops/security/events", json={"source": "pinky-3", "note": "x"})
    assert r.status_code == 201
    events = client.get("/api/admin/ops/security", headers=admin_auth).json()["events"]
    assert events[0]["zone"] is None


def test_ai_alive_는_mode_를_읽기_전엔_False(client, admin_auth):
    from app.routers import ops_extra
    ops_extra._ai_seen_at = 0.0  # reset: 앞선 시험이 이미 mode 를 읽었어도 격리한다
    assert client.get("/api/admin/ops/security", headers=admin_auth).json()["ai_alive"] is False


def test_ai_alive_는_mode_를_읽은_뒤_True(client, admin_auth):
    from app.routers import ops_extra
    ops_extra._ai_seen_at = 0.0  # reset: 실행 순서에 기대지 않고 이 시험 안에서 다시 채운다
    client.get("/api/admin/ops/security/mode")
    assert client.get("/api/admin/ops/security", headers=admin_auth).json()["ai_alive"] is True
