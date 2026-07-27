"""`/libi/camera_select` 값 + 만료 워치독. ROS 를 몰라서 단독으로 시험된다.

## latched QoS 만으로는 부족하다

`TRANSIENT_LOCAL` 은 **살아 있는 발행자**의 마지막 샘플만 늦게 붙은 구독자에게 준다.
그래서 두 방향으로 어긋난다:

  · 발행자(follow_node)가 죽으면 durable 캐시도 같이 사라진다. 송출기는 마지막으로
    받은 값을 그대로 들고 계속 인코딩한다 — 세션이 끝났는데 영상이 계속 나간다.
  · 송출기가 재시작하면 이미 끝난 세션의 stale `front` 를 받아 영상이 되살아난다.

둘 다 "아무도 안 보는데 카메라가 켜져 있다"로 끝난다. 그래서 값이 갱신되지 않으면
스스로 `none` 으로 떨어진다. 발행자는 만료보다 촘촘한 주기로 다시 발행해야 한다.
"""
VALID = ("front", "back", "none")
NONE = "none"


class CameraSelect:
    def __init__(self, expiry_sec: float):
        self.expiry_sec = float(expiry_sec)
        self._value = NONE
        self._stamp = None

    def set(self, value, stamp: float) -> None:
        # 모르는 값은 none 으로 떨어뜨린다 — 오타 하나로 카메라가 계속 켜지는 것보다,
        # 안 켜지고 로그로 드러나는 편이 낫다.
        self._value = value if value in VALID else NONE
        self._stamp = stamp

    def current(self, now: float) -> str:
        if self._stamp is None:
            return NONE                      # 아무도 발행한 적이 없다
        if self.expiry_sec > 0 and now - self._stamp > self.expiry_sec:
            return NONE                      # 갱신이 끊겼다
        return self._value

    @property
    def raw(self) -> str:
        """만료를 무시한 마지막 수신값. 진단용."""
        return self._value
