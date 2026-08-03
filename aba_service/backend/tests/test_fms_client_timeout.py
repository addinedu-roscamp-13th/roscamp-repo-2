"""`fleet_snapshot` 의 `timeout` 파라미터가 실제 `urlopen` 까지 전달되는지,
그리고 인자를 안 주는 기존 호출부는 그대로 `TIMEOUT_SEC`(8초)를 쓰는지 확인한다.

요구사항 20: `_zone_from_fleet` 이 짧은 타임아웃(1초)을 쓸 수 있으려면, `fleet_snapshot`
이 `timeout` 을 받아 `_authed`→`_request`→`urlopen` 까지 그대로 흘려보내야 한다. 동시에
다른 호출부(`ops.py` 의 dashboard/robots)는 인자를 안 주므로 기본값(8초)이 안 바뀌어야
한다 — 순수 추가(opt-in) 변경인지가 이 시험의 핵심.
"""
import json

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
