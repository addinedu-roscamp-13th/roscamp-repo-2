import time

from .detection import detection_from_dict

#: 이 시간 넘게 새 payload 가 안 오면 마지막 검출을 버린다.
#: 카메라 15fps · 제어 20Hz 이므로 정상 상황에서는 1초 안에 반드시 뭔가 도착한다.
DEFAULT_TTL_SEC = 1.0


class DetectionReceiver:
    """Holds the latest owner Detection parsed from incoming JSON dicts.

    `source.poll()` returns a list of payloads received since last poll;
    each payload is a Detection dict, or None meaning 'no owner this frame'.
    Concrete TCP socket wraps this small interface (integration-tested).

    ## 왜 TTL 이 있는가

    소스가 "사람 없음"을 알릴 때는 payload `None` 이 오므로 그건 문제가 아니다.
    문제는 **소켓이 죽는 경우**다 — 그때 `poll()` 은 빈 리스트만 돌려주고 `_latest` 는
    영원히 마지막 검출로 남는다. `ControlLoop` 은 그걸 "사람이 보인다"로 읽어 `miss` 를
    올리지 않고, 로봇은 이미 존재하지 않는 대상을 계속 추종한다. 회복 BT 도 안 돈다.

    그래서 "마지막으로 payload 를 받은 시각"을 같이 들고, 그게 낡으면 None 을 돌려준다.
    모르는 것을 안다고 하지 않는다.
    """

    def __init__(self, source, ttl_sec=DEFAULT_TTL_SEC, now=time.monotonic):
        self._source = source
        self._latest = None
        self._ttl = ttl_sec
        self._now = now
        self._stamp = None

    def update(self):
        for payload in self._source.poll():
            self._latest = detection_from_dict(payload)
            self._stamp = self._now()

    def latest(self):
        if self._latest is None:
            return None
        if self._stamp is None or (self._now() - self._stamp) > self._ttl:
            self._latest = None       # 소스가 끊겼다 — 유령을 쫓지 않는다
            return None
        return self._latest
