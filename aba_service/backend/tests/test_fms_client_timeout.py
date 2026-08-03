"""`fleet_snapshot` 의 `timeout` 파라미터가 실제 `urlopen` 까지 전달되는지,
그리고 인자를 안 주는 기존 호출부는 그대로 `TIMEOUT_SEC`(8초)를 쓰는지 확인한다.

요구사항 20: `_zone_from_fleet` 이 짧은 타임아웃(1초)을 쓸 수 있으려면, `fleet_snapshot`
이 `timeout` 을 받아 `_authed`→`_request`→`urlopen` 까지 그대로 흘려보내야 한다. 동시에
다른 호출부(`ops.py` 의 dashboard/robots)는 인자를 안 주므로 기본값(8초)이 안 바뀌어야
한다 — 순수 추가(opt-in) 변경인지가 이 시험의 핵심.

`_authed` 안의 `_login()` 호출 두 곳(콜드스타트 — `_token is None`, 그리고 401 재시도 —
재로그인)도 같은 `timeout` 을 받아야 한다. 안 그러면 `fleet_snapshot(timeout=1)` 을 불러도
토큰이 없거나 만료된 순간(야간 순찰 도중 흔함)엔 로그인 요청이 기본 8초를 그대로 써서
알림-먼저 설계가 로그인 경로로 새어나간다.
"""
import io
import json
import urllib.error

from app import fms_client


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_urlopen(monkeypatch, seen: dict):
    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(json.dumps({"snapshot": {"robots": []}}).encode("utf-8"))

    monkeypatch.setattr(fms_client, "_token", "tok")  # 로그인 왕복 스킵
    monkeypatch.setattr(fms_client.urllib.request, "urlopen", fake_urlopen)


def test_fleet_snapshot_기본_호출은_TIMEOUT_SEC를_그대로_쓴다(monkeypatch):
    seen: dict = {}
    _mock_urlopen(monkeypatch, seen)

    ok, snap = fms_client.fleet_snapshot()

    assert ok is True
    assert seen["timeout"] == fms_client.TIMEOUT_SEC


def test_fleet_snapshot_timeout_인자가_urlopen까지_전달된다(monkeypatch):
    seen: dict = {}
    _mock_urlopen(monkeypatch, seen)

    ok, snap = fms_client.fleet_snapshot(timeout=1)

    assert ok is True
    assert seen["timeout"] == 1


def _dispatching_fake_urlopen(calls: list[dict], snapshot_status: list[int]):
    """호출마다 로그인/스냅샷 경로를 구분해 `timeout` 을 기록한다.

    `snapshot_status` 는 스냅샷 GET 이 불릴 때마다 하나씩 꺼내 쓰는 상태코드 큐 —
    401 재시도 시나리오에서 [401, 200] 처럼 준다.
    """

    def fake_urlopen(req, timeout=None):
        is_login = req.get_full_url().endswith(fms_client.FMS_LOGIN_PATH)
        calls.append({"login": is_login, "timeout": timeout})
        if is_login:
            return _FakeResponse(json.dumps({"access_token": "tok"}).encode("utf-8"))
        status = snapshot_status.pop(0)
        if status == 401:
            raise urllib.error.HTTPError(
                req.get_full_url(), 401, "unauthorized", None, io.BytesIO(b"")
            )
        return _FakeResponse(json.dumps({"snapshot": {"robots": []}}).encode("utf-8"))

    return fake_urlopen


def test_콜드스타트_로그인도_timeout을_받는다(monkeypatch):
    """`_token is None` 인 상태(재시작 직후·처음 호출)에서 `_authed` 가 타는 로그인
    분기도 `fleet_snapshot(timeout=1)` 의 timeout 을 그대로 받아야 한다."""
    monkeypatch.setattr(fms_client, "_token", None)
    calls: list[dict] = []
    monkeypatch.setattr(
        fms_client.urllib.request, "urlopen",
        _dispatching_fake_urlopen(calls, snapshot_status=[200]),
    )

    ok, snap = fms_client.fleet_snapshot(timeout=1)

    assert ok is True
    login_calls = [c for c in calls if c["login"]]
    assert login_calls and all(c["timeout"] == 1 for c in login_calls)
    snapshot_calls = [c for c in calls if not c["login"]]
    assert snapshot_calls and all(c["timeout"] == 1 for c in snapshot_calls)


def test_401_재로그인도_timeout을_받는다(monkeypatch):
    """토큰이 있지만 만료돼 401 이 오면 `_authed` 가 재로그인 후 재시도한다 — 그 재로그인도
    같은 timeout 을 받아야 한다."""
    monkeypatch.setattr(fms_client, "_token", "expired")
    calls: list[dict] = []
    monkeypatch.setattr(
        fms_client.urllib.request, "urlopen",
        _dispatching_fake_urlopen(calls, snapshot_status=[401, 200]),
    )

    ok, snap = fms_client.fleet_snapshot(timeout=1)

    assert ok is True
    login_calls = [c for c in calls if c["login"]]
    assert login_calls and all(c["timeout"] == 1 for c in login_calls)
    snapshot_calls = [c for c in calls if not c["login"]]
    assert len(snapshot_calls) == 2
    assert all(c["timeout"] == 1 for c in snapshot_calls)
