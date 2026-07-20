"""도서 → 선반 → waypoint 해석 (orchestrator 가 주문을 다리로 풀기 전 좌표 해석).

## 왜 여기
orchestrator 코어는 waypoint(정점) 만 다룬다. "이 책이 어느 정점에서 집히나"는 데이터 문제라
이 층이 맡는다: 책 → 선반(도서 DB) → waypoint(선반/장소 매핑).

## 데이터 원천은 주입 (실데이터는 사용자 소유)
- `book_shelf_lookup(book) -> shelf_name`: 도서 위치. 실제로는 aba_service 도서 DB(cb_*) 조회.
  테스트/미배선 시엔 dict.
- `location_waypoints`: 선반/장소 이름 → waypoint 정점. 실제로는 config/DB. arte2 맵의 정점과
  일치해야 한다(⚠️ 현재 fleet navgraph=new_map 과 arte2 정합 필요 — #27 note).

## ⚠️ 미채움
- 실제 도서→선반 데이터(cb_* 스키마 컬럼) 미확정 → book_shelf_lookup 은 사용자가 DB 로 배선.
- location_waypoints 실값(선반/장소 → arte2 정점) 미확정 → 사용자가 채움.
여기서는 "구조 + 해석 로직 + 검증"만 제공한다.
"""
from __future__ import annotations


class LocationError(Exception):
    """책·선반·장소를 waypoint 로 못 풀 때."""


class BookLocator:
    def __init__(self, book_shelf_lookup, location_waypoints: dict):
        # book_shelf_lookup(book) -> shelf_name (or None/raise)
        self._lookup = book_shelf_lookup
        self._wp = dict(location_waypoints)

    def pickup_waypoint(self, book: str):
        """책이 있는 선반의 waypoint. 책/선반/정점 중 하나라도 모르면 LocationError."""
        try:
            shelf = self._lookup(book)
        except Exception as exc:                     # noqa: BLE001
            raise LocationError(f"도서 위치 조회 실패({book}): {exc}") from exc
        if not shelf:
            raise LocationError(f"도서 위치를 모름: {book}")
        return self._location_waypoint(shelf)

    def destination_waypoint(self, destination: str):
        """배달지(고정 장소: table/info_desk/collection_bin 등)의 waypoint."""
        return self._location_waypoint(destination)

    def _location_waypoint(self, location: str):
        wp = self._wp.get(location)
        if wp is None:
            raise LocationError(f"장소→waypoint 매핑 없음: {location}")
        return wp

    def resolve_delivery(self, book: str, destination: str) -> tuple:
        """(pickup_waypoint, dropoff_waypoint) — orchestrator.submit_delivery 로 바로 넘긴다."""
        return self.pickup_waypoint(book), self.destination_waypoint(destination)
