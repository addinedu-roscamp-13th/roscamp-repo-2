"""HandyCore 단위테스트 — ROS·팔 없이 검증. motion 을 페이크로 주입."""
import pytest

from libi_handy_controller.handy_core import HandyCore


class FakeMotion:
    """호출 기록. fail=True 면 예외(팔 동작 실패)."""

    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    def __call__(self, action, obj, location):
        self.calls.append((action, obj, location))
        if self._fail:
            raise RuntimeError("그립 실패")


def test_valid_pick_succeeds_and_calls_motion():
    m = FakeMotion()
    ok, err = HandyCore(m).perform("pick", "book", "bookshelf")
    assert ok is True and err == ""
    assert m.calls == [("pick", "book", "bookshelf")]


def test_valid_place_basket_succeeds():
    ok, err = HandyCore(FakeMotion()).perform("place", "basket", "libi_basket")
    assert ok is True


@pytest.mark.parametrize("loc", ["libi_basket", "collection_bin", "bookshelf", "table", "info_desk"])
def test_all_locations_supported(loc):
    ok, _ = HandyCore(FakeMotion()).perform("place", "book", loc)
    assert ok is True


def test_unknown_action_fails_without_motion():
    m = FakeMotion()
    ok, err = HandyCore(m).perform("throw", "book", "table")
    assert ok is False and "action" in err
    assert m.calls == []               # 잘못된 요청엔 팔 안 움직임


def test_unknown_object_fails():
    ok, err = HandyCore(FakeMotion()).perform("pick", "cat", "table")
    assert ok is False and "object" in err


def test_unknown_location_fails():
    ok, err = HandyCore(FakeMotion()).perform("pick", "book", "moon")
    assert ok is False and "location" in err


def test_motion_exception_becomes_failure_not_crash():
    ok, err = HandyCore(FakeMotion(fail=True)).perform("pick", "book", "bookshelf")
    assert ok is False and "실패" in err


def test_default_stub_motion_succeeds():
    # motion 안 주면 스텁(성공만) — 인터페이스/상위 검증용.
    ok, err = HandyCore().perform("pick", "book", "bookshelf")
    assert ok is True and err == ""
