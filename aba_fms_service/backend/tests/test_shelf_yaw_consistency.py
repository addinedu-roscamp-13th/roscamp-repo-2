"""navgraph 의 서가 yaw 와 코드 상수가 어긋나면 안 된다.

⚠️ 두 벌이라 조용히 어긋날 수 있다. 어긋나면 로봇이 서가가 아닌 곳을 보고 레이를 쏜다.

[P2-2] 두 소비자를 **직접** 대조한다(제3의 `EXPECTED` 사전을 안 둔다 — 그러면 코드 쪽
상수가 바뀌어도 이 시험은 초록으로 남는다, codex 최종 검토 지적):
    - navgraph 의 정점 `yaw`   (`arte2.navgraph.yaml`)
    - `shelf_dock.SHELF_YAW`   (`robot_agent`, 도킹 최종 자세)

`shelf_dock.py` 는 `robot_agent` 쪽이고 다른 에이전트가 작업 중이라 **읽기만** 한다.
import 도 안 한다 — `app.shelf.green_marker` 등 무거운 의존(cv2)을 끌고 온다. `ast` 로
소스에서 상수 값만 뽑는다.
"""
import ast
import math
import pathlib

import yaml

NAVGRAPH = pathlib.Path(__file__).resolve().parents[2] / (
    "fleet_ws/maps/library/arte2.navgraph.yaml")

SHELF_DOCK_PY = pathlib.Path(__file__).resolve().parents[3] / (
    "aba_controller/libi_drive_controller/robot_agent/app/core/shelf_dock.py")


def _vertices() -> dict:
    doc = yaml.safe_load(NAVGRAPH.read_text(encoding="utf-8"))
    out = {}
    for v in doc["levels"]["L1"]["vertices"]:
        name = (v[2] or {}).get("name")
        if name:
            out[name] = v[2]
    return out


def _robot_shelf_yaw() -> dict:
    """`shelf_dock.py` 의 `SHELF_YAW` 상수를 소스에서 그대로 뽑는다(import 안 함)."""
    tree = ast.parse(SHELF_DOCK_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SHELF_YAW" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{SHELF_DOCK_PY} 에서 SHELF_YAW 상수를 못 찾았다")


def test_both_demo_shelves_have_a_yaw():
    verts = _vertices()
    for name in _robot_shelf_yaw():
        assert name in verts, f"{name} 정점이 없다"
        assert "yaw" in verts[name], f"{name} 에 yaw 가 없다"


def test_navgraph_yaw_matches_shelf_dock_constant():
    """[P2-2] navgraph 의 yaw 와 `shelf_dock.SHELF_YAW` 를 직접 대조한다 —
    `SHELF_YAW` 가 바뀌어도, navgraph 가 바뀌어도 이 시험이 잡는다."""
    verts = _vertices()
    robot_yaw = _robot_shelf_yaw()
    assert robot_yaw, "SHELF_YAW 가 비어 있다"
    for name, want in robot_yaw.items():
        assert name in verts, f"{name} 정점이 navgraph 에 없다"
        assert math.isclose(float(verts[name]["yaw"]), float(want), abs_tol=1e-4), name


def test_other_vertices_are_untouched():
    """예술서가는 이번 범위가 아니다 — 두 소비자 모두 다루지 않아야 한다."""
    verts = _vertices()
    assert "yaw" not in verts.get("예술서가", {})
    assert "예술서가" not in _robot_shelf_yaw()
