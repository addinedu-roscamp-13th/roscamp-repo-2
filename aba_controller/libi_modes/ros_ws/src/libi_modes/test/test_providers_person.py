"""providers 가 사람 차단용 값 세 개를 채우는지. ROS 없이 순수 판정만 본다."""
from types import SimpleNamespace

from libi_modes.ros.providers import RosProviders, is_moving_straight


def test_standing_still_is_not_straight():
    assert is_moving_straight(0.0, 0.0) is False


def test_forward_without_turning_is_straight():
    assert is_moving_straight(0.10, 0.0) is True


def test_turning_in_place_is_not_straight():
    assert is_moving_straight(0.0, 0.5) is False


def test_forward_while_turning_hard_is_not_straight():
    assert is_moving_straight(0.10, 0.5) is False


def test_reversing_is_not_straight():
    assert is_moving_straight(-0.10, 0.0) is False


def test_small_wobble_is_tolerated():
    assert is_moving_straight(0.10, 0.02) is True


def test_thresholds_are_configurable():
    assert is_moving_straight(0.01, 0.0, min_linear=0.05) is False


# ── [codex P1] front_person_size 신선도 ─────────────────────────────────────

class _FakeNode:
    """RosProviders 생성자가 쓰는 것만 흉내낸다 — 구독을 콜백 딕셔너리로 기록한다."""

    def __init__(self):
        self.subscriptions = {}

    def create_subscription(self, msg_type, topic, cb, qos):
        self.subscriptions[topic] = cb
        return object()

    def get_logger(self):
        class _L:
            def warning(self, *a):
                pass

            def debug(self, *a):
                pass
        return _L()


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_stale_person_size_becomes_none():
    """검출 파이프라인이나 도메인 브릿지가 죽으면 마지막 값이 영원히 남으면 안 된다 —
    `PersonBlockGuard` 가 유령 앞에서 영원히 멈추고 차단까지 보고하게 된다
    (detection_receiver.py: "모르는 것을 안다고 하지 않는다").
    """
    clock = _Clock()
    node = _FakeNode()
    p = RosProviders(node, now_fn=clock, front_person_size_ttl_sec=2.0)
    on_front_person_size = node.subscriptions["/libi/front_person_size"]

    on_front_person_size(SimpleNamespace(data=300.0))
    assert p.as_dict()["front_person_size"]() == 300.0

    clock.t = 2.1                     # TTL(2.0) 을 넘겼다 — 값만 밀었을 뿐 새 메시지는 없다
    assert p.as_dict()["front_person_size"]() is None, "TTL 을 넘긴 값은 None 이어야 한다"


def test_fresh_person_size_survives_within_the_ttl():
    clock = _Clock()
    node = _FakeNode()
    p = RosProviders(node, now_fn=clock, front_person_size_ttl_sec=2.0)
    node.subscriptions["/libi/front_person_size"](SimpleNamespace(data=300.0))

    clock.t = 1.9
    assert p.as_dict()["front_person_size"]() == 300.0, "TTL 안이면 값이 살아 있어야 한다"


def test_never_received_person_size_is_none():
    p = RosProviders(_FakeNode())
    assert p.as_dict()["front_person_size"]() is None
