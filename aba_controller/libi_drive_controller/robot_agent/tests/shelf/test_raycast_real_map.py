"""실제 arte3 지도에 두 서가 정점에서 레이를 쏴 알려진 거리를 확인한다.

⚠️ 이 시험은 `.pgm` **이미지 파일**을 읽으므로 행을 뒤집어야 한다(위 raycast.py 머리말).
   그 변환이 여기 한 곳에만 있고, 결과가 OccupancyGrid 관례의 `Grid` 로 들어간다.
"""
import math
import pathlib

import pytest

from app.shelf.raycast import Grid, first_occupied

PGM = pathlib.Path(__file__).resolve().parents[4] / (
    "libi_drive_controller/ros_ws/src/pinky_pro/pinky_navigation/map/arte3.pgm")
RES, OX, OY = 0.02, -0.184, -1.949


def _grid_from_pgm():
    Image = pytest.importorskip("PIL.Image", reason="Pillow 없음")
    im = Image.open(PGM).convert("L")
    w, h = im.size
    px = im.load()
    # pgm 은 위에서 아래로. OccupancyGrid 관례(아래에서 위로)로 뒤집는다.
    data = []
    for row in range(h):
        iy = h - 1 - row
        for col in range(w):
            data.append(100 if px[col, iy] < 89 else 0)
    return Grid(data=data, width=w, height=h, resolution=RES,
                origin_x=OX, origin_y=OY)


@pytest.mark.skipif(not PGM.exists(), reason="지도 파일 없음")
@pytest.mark.parametrize("name,x,y,yaw,lo,hi", [
    ("문학서가",        0.026, -0.361,  1.5708, 0.14, 0.18),
    ("과학-인문학서가", 0.300, -0.660, -1.5708, 0.15, 0.19),
    ("문학서가+우20",   0.026, -0.361,  1.2217, 0.15, 0.19),
    ("과학-인문+우20",  0.300, -0.660, -1.9199, 0.16, 0.20),
])
def test_shelf_is_within_reach(name, x, y, yaw, lo, hi):
    hit = first_occupied(_grid_from_pgm(), x, y, yaw)
    assert hit is not None, f"{name}: 1m 안에 점유 셀이 없다"
    _, dist = hit
    assert lo <= dist <= hi, f"{name}: {dist:.3f}m"
