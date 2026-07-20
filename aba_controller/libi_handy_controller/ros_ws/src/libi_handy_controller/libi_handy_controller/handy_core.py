"""Handy(로봇팔) 코어 — pick/place 요청을 검증하고 팔 모션으로 넘긴다.

rclpy 무관 순수 로직 (orchestrator 코어와 같은 원칙) — ROS·팔 하드웨어 없이 단위테스트.
실제 팔 모션은 `motion` 콜러블로 주입한다. 기본은 스텁(성공만) — 팔 담당자가 채운다.

인터페이스(요청서 참고): action ∈ {pick, place}, object ∈ {book, basket},
location ∈ {libi_basket, collection_bin, bookshelf, table, info_desk}.
모르는 값은 예외로 죽지 않고 (False, 사유) 반환 — Drive/FMS 가 재시도·중단 판단.
"""
from __future__ import annotations

ACTIONS = frozenset({"pick", "place"})
OBJECTS = frozenset({"book", "basket"})
LOCATIONS = frozenset({"libi_basket", "collection_bin", "bookshelf", "table", "info_desk"})


class HandyCore:
    def __init__(self, motion=None):
        # motion(action, object, location) -> None. 실패 시 예외를 던진다.
        self._motion = motion or self._stub_motion

    def perform(self, action: str, obj: str, location: str) -> tuple[bool, str]:
        if action not in ACTIONS:
            return False, f"미지원 action: {action!r}"
        if obj not in OBJECTS:
            return False, f"미지원 object: {obj!r}"
        if location not in LOCATIONS:
            return False, f"미지원 location: {location!r}"
        try:
            self._motion(action, obj, location)
        except Exception as exc:            # noqa: BLE001 — 팔 실패도 정상 반환값으로
            return False, f"팔 동작 실패: {exc}"
        return True, ""

    @staticmethod
    def _stub_motion(action: str, obj: str, location: str) -> None:
        """TODO(팔 담당자): pymycobot(JetCobot) 로 실제 모션.

        각 (action, object, location) 조합의 좌표/그립 시퀀스를 여기(또는 주입 motion)서
        구현한다. 지금은 스텁 — 인터페이스·상위 로직 검증용으로 성공만 반환한다.
        요청서: 2026-07-21 Handy(로봇팔) 인터페이스 요청서.md
        """
        return None
