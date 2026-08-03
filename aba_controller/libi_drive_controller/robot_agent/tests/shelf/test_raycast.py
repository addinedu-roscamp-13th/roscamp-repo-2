"""점유격자 레이캐스트. ROS 없이 격자 값만 본다."""
import math

from app.shelf.raycast import Grid, first_occupied

# 10x10 격자, 1셀 = 0.1m, 원점 (0,0). 오른쪽 끝 열(col 9)만 점유.
def _grid():
    data = []
    for row in range(10):
        for col in range(10):
            data.append(100 if col == 9 else 0)
    return Grid(data=data, width=10, height=10, resolution=0.1,
                origin_x=0.0, origin_y=0.0)


def test_value_at_reads_row_major_without_flipping():
    """OccupancyGrid 는 data[0] 이 원점(좌하단)이고 row 를 뒤집지 않는다."""
    g = _grid()
    assert g.value_at(0.05, 0.05) == 0        # col 0, row 0
    assert g.value_at(0.95, 0.05) == 100      # col 9, row 0
    assert g.value_at(0.95, 0.95) == 100      # col 9, row 9


def test_value_outside_the_grid_is_none():
    g = _grid()
    assert g.value_at(-0.1, 0.5) is None
    assert g.value_at(1.5, 0.5) is None


def test_half_cell_outside_the_grid_is_still_outside():
    """int() 절삭은 음수 분수를 0 으로 민다 — 격자 밖이 셀 0 으로 읽히면 안 된다."""
    g = _grid()
    assert g.value_at(-0.05, 0.5) is None
    assert g.value_at(0.5, -0.05) is None


def test_ray_along_plus_x_hits_the_wall():
    g = _grid()
    hit = first_occupied(g, 0.05, 0.55, 0.0)
    assert hit is not None
    (hx, hy), dist = hit
    assert math.isclose(hy, 0.55)
    assert 0.85 <= hx <= 0.95
    assert math.isclose(dist, hx - 0.05, abs_tol=1e-9)


def test_ray_away_from_the_wall_finds_nothing_and_returns_none():
    g = _grid()
    assert first_occupied(g, 0.55, 0.55, math.pi) is None


def test_ray_leaving_the_grid_returns_none():
    g = _grid()
    assert first_occupied(g, 0.55, 0.95, math.pi / 2) is None


def test_max_range_stops_the_search():
    g = _grid()
    assert first_occupied(g, 0.05, 0.55, 0.0, max_m=0.5) is None


def test_unknown_cells_are_not_occupied():
    data = [-1] * 100
    g = Grid(data=data, width=10, height=10, resolution=0.1,
             origin_x=0.0, origin_y=0.0)
    assert first_occupied(g, 0.05, 0.55, 0.0) is None


def test_occupied_threshold_is_configurable():
    data = [50] * 100
    g = Grid(data=data, width=10, height=10, resolution=0.1,
             origin_x=0.0, origin_y=0.0)
    assert first_occupied(g, 0.05, 0.55, 0.0) is None
    assert first_occupied(g, 0.05, 0.55, 0.0, occupied_min=50) is not None
