"""주문 화면의 waypoint 목록이 로봇 내비 그래프와 어긋나지 않는지 감시한다.

`frontend/src/components/admin/dispatch/waypoints.ts` 는 로봇의
`pinky_navigation/params/waypoint.yaml` 정점 이름을 복사해둔 정적 목록이다(로봇 없이도
주문을 만들 수 있어야 해서 API 가 아니다). 복사본이라 맵을 다시 그리면 조용히 썩는다 —
실제로 예전 목록(`문학-1`, `테이블-1번-상`, `입구` …)이 정점에 없는 이름이 된 채 남아 있었고,
그대로 주문을 넣으면 orchestrator 가 목적지를 못 풀어 실패한다.

여기서 검사하는 건 하나뿐이다: **화면에 뜨는 모든 값이 waypoint.yaml 에 실재하는가.**
반대 방향(yaml 에 있는데 화면에 없다)은 검사하지 않는다 — 경유점(`순회경로-*`)이나
충전 도크(`주차장*`)처럼 일부러 뺀 정점이 있다.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WAYPOINT_YAML = (
    REPO / "aba_controller" / "libi_drive_controller" / "ros_ws" / "src" / "pinky_pro"
    / "pinky_navigation" / "params" / "waypoint.yaml"
)
WAYPOINTS_TS = (
    REPO / "aba_fms_service" / "frontend" / "src" / "components" / "admin" / "dispatch"
    / "waypoints.ts"
)


def _yaml_vertices() -> set[str]:
    """`vertices:` 블록의 키만 뽑는다 (PyYAML 없이 — 들여쓰기 2칸 키가 정점 이름)."""
    names: set[str] = set()
    in_vertices = False
    for line in WAYPOINT_YAML.read_text(encoding="utf-8").splitlines():
        if line.startswith("vertices:"):
            in_vertices = True
            continue
        if in_vertices and line and not line.startswith(" "):
            break                                   # 다음 최상위 키(lanes:)에서 끝
        m = re.match(r"^  (\S+):\s*$", line)
        if in_vertices and m:
            names.add(m.group(1))
    return names


def _ts_values() -> set[str]:
    return set(re.findall(r'value:\s*"([^"]+)"', WAYPOINTS_TS.read_text(encoding="utf-8")))


def test_source_files_are_where_we_think_they_are():
    """경로가 틀리면 아래 검사가 빈 집합끼리 비교하며 조용히 통과한다."""
    assert WAYPOINT_YAML.is_file(), f"waypoint.yaml 을 {WAYPOINT_YAML} 에서 못 찾았습니다."
    assert WAYPOINTS_TS.is_file(), f"waypoints.ts 를 {WAYPOINTS_TS} 에서 못 찾았습니다."
    assert _yaml_vertices(), "waypoint.yaml 에서 정점을 하나도 못 읽었습니다(파싱 실패)."
    assert _ts_values(), "waypoints.ts 에서 value 를 하나도 못 읽었습니다(파싱 실패)."


def test_every_dispatch_option_exists_in_the_nav_graph():
    missing = sorted(_ts_values() - _yaml_vertices())
    assert not missing, (
        f"주문 화면의 waypoint 값이 내비 그래프에 없습니다: {missing}. "
        f"{WAYPOINT_YAML.name} 의 정점 이름과 글자까지 같아야 주문이 목적지를 찾습니다."
    )


@pytest.mark.parametrize("shelf", ["문학서가", "예술서가", "과학-인문학서가"])
def test_book_shelves_are_selectable(shelf):
    """도서 DB `cb_books.zone` 이 이 셋이다 — 하나라도 빠지면 책을 골라도 출발지가 안 채워진다."""
    assert shelf in _ts_values(), f"{shelf} 가 주문 화면 목록에 없습니다."
