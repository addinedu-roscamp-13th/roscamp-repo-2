"""navgraph 에서 갈림길 정점만 뽑는다.

모든 노드에서 서면 안 되는 이유가 여기 있다 — 실제 운용 맵(arte2)의 레인 길이가
0.151~0.601m 라, 로봇 속도 0.12 m/s 기준 1~5초마다 한 번씩 서게 된다.
"""
import textwrap

from libi_modes.common.junctions import JunctionSet, load_junctions


def _write(tmp_path, body):
    p = tmp_path / "navgraph.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


_GRAPH = """
    levels:
      L1:
        vertices:
          - [0.0, 0.0, {name: A}]
          - [1.0, 0.0, {name: B}]
          - [2.0, 0.0, {name: C}]
          - [1.0, 1.0, {name: D}]
        lanes:
          - [0, 1, {}]
          - [1, 2, {}]
          - [1, 3, {}]
    """


def test_picks_only_vertices_with_three_or_more_lanes(tmp_path):
    pts = load_junctions(_write(tmp_path, _GRAPH))
    assert pts == [(1.0, 0.0)]          # B 만 레인 3개


def test_min_degree_is_configurable(tmp_path):
    pts = load_junctions(_write(tmp_path, _GRAPH), min_degree=2)
    assert (0.0, 0.0) not in pts        # A 는 레인 1개
    assert (1.0, 0.0) in pts


def test_missing_file_is_empty_not_an_error(tmp_path):
    """navgraph 를 못 읽으면 확인 동작이 꺼질 뿐, 안내가 죽으면 안 된다."""
    assert load_junctions(str(tmp_path / "nope.yaml")) == []


def test_malformed_yaml_is_empty(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("levels: [this is not a mapping")
    assert load_junctions(str(p)) == []


def test_real_arte2_graph_has_junctions():
    """실제 운용 맵으로도 돌아가는지 — 합성 그래프만 보면 스키마를 놓친다."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", "..", "..", "..", ".."))
    path = os.path.join(root, "aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml")
    if not os.path.exists(path):
        return                           # 체크아웃에 fms 서브트리가 없을 수 있다
    pts = load_junctions(path)
    assert len(pts) > 0


# ── 판정 ────────────────────────────────────────────────────────────────────

def test_contains_within_tolerance():
    js = JunctionSet([(1.0, 2.0)], tolerance=0.05)
    assert js.contains({"x": 1.02, "y": 2.0}) is True
    assert js.contains({"x": 1.2, "y": 2.0}) is False


def test_contains_accepts_tuple():
    assert JunctionSet([(1.0, 2.0)], tolerance=0.05).contains((1.0, 2.0)) is True


def test_empty_set_never_matches():
    assert JunctionSet([]).contains({"x": 0.0, "y": 0.0}) is False


def test_none_target_is_false():
    assert JunctionSet([(0.0, 0.0)]).contains(None) is False


def test_malformed_target_is_false():
    assert JunctionSet([(0.0, 0.0)]).contains({"lat": 1}) is False
