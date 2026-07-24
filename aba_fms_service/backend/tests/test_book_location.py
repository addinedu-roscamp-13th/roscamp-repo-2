"""BookLocator — 도서→선반→waypoint 해석 검증. 데이터는 주입(dict)."""
import pytest

from app.book_location import BookLocator, LocationError

# 샘플 데이터 (실데이터는 사용자 소유 — 여기선 검증용)
SHELVES = {"B1": "shelf_science", "B2": "shelf_arts"}
WAYPOINTS = {"shelf_science": 7, "shelf_arts": 11, "info_desk": 2, "table": 5}


def _locator(shelves=None):
    s = SHELVES if shelves is None else shelves
    return BookLocator(lambda b: s.get(b), WAYPOINTS)


def test_pickup_waypoint_via_shelf():
    assert _locator().pickup_waypoint("B1") == 7      # B1 → shelf_science → 7


def test_destination_waypoint():
    assert _locator().destination_waypoint("info_desk") == 2


def test_resolve_delivery_returns_pair():
    assert _locator().resolve_delivery("B2", "table") == (11, 5)


def test_unknown_book_raises():
    with pytest.raises(LocationError):
        _locator().pickup_waypoint("B999")


def test_shelf_without_waypoint_raises():
    loc = BookLocator(lambda b: "shelf_ghost", WAYPOINTS)   # 선반은 있는데 정점 매핑 없음
    with pytest.raises(LocationError):
        loc.pickup_waypoint("B1")


def test_unknown_destination_raises():
    with pytest.raises(LocationError):
        _locator().destination_waypoint("mars")


def test_lookup_exception_becomes_location_error():
    def boom(_):
        raise RuntimeError("DB down")
    with pytest.raises(LocationError):
        BookLocator(boom, WAYPOINTS).pickup_waypoint("B1")
