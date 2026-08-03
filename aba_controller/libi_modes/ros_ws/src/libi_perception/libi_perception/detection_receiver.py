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
        #: 화면에서 가장 큰 사람의 크기 — **owner 유무와 무관한 별도 슬롯.**
        #: owner 없는 프레임도 `{"front_person_size": ...}` 만 실려 온다
        #: (`detection_sink.detection_to_dict` [2026-08-03]) — `_latest` 가 None 이어도
        #: 이 값은 살아야 `PersonBlockGuard` 가 주행 중(등록 대상 없음)에도 사람을 본다.
        #: `_stamp` 를 같이 쓴다 — payload 가 dict 든 null 이든 매 poll 마다 함께
        #: 갱신되므로(update() 참고) 별도 타임스탬프를 둘 이유가 없다.
        self._size = 0.0

    def update(self):
        for payload in self._source.poll():
            self._latest = detection_from_dict(payload)
            self._size = float(payload.get('front_person_size', 0.0) or 0.0) \
                if payload is not None else 0.0
            self._stamp = self._now()

    def latest(self):
        if self._latest is None:
            return None
        if self._stamp is None or (self._now() - self._stamp) > self._ttl:
            self._latest = None       # 소스가 끊겼다 — 유령을 쫓지 않는다
            return None
        return self._latest

    def front_person_size(self):
        """가장 큰 사람 크기. `latest()` 와 **같은 TTL 규칙**을 쓴다 — 소스가
        끊기면 0.0 을 낸다(모르는 것을 안다고 하지 않는다, `latest()` 머리말과 같은 이유).

        ⚠️ `latest()` 가 None 이어도(owner 가 없어도) 값이 산다 — 그게 이 메서드의
        존재 이유다.
        """
        if self._stamp is None or (self._now() - self._stamp) > self._ttl:
            return 0.0
        return self._size
