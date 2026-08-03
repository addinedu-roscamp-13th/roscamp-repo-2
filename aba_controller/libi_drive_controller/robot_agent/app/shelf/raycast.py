"""로봇이 보는 방향으로 지도에 선을 그어 처음 만나는 벽을 찾는다.

## ⚠️ 행 인덱스를 뒤집지 않는다

`nav_msgs/OccupancyGrid` 는 `data[0]` 이 **원점(좌하단)** 이고 행이 아래에서 위로
간다 — `idx = row * width + col`. 반면 같은 지도를 `.pgm` **이미지 파일**로 읽으면
맨 윗줄이 row 0 이라 뒤집어야 한다. 두 관례를 섞으면 세로축이 통째로 미러된다.
이 모듈은 **메시지 쪽 관례**만 쓴다.

## 왜 값 배열만 받나

`rclpy` 를 안 import 해야 로봇 없이 시험할 수 있다. 호출자가 메시지에서 필요한 값만
뽑아 `Grid` 로 넘긴다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Grid:
    data: list
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float

    def value_at(self, x: float, y: float):
        """월드 좌표의 셀 값. 격자 밖이면 `None`."""
        if self.resolution <= 0.0:
            return None
        col = math.floor((x - self.origin_x) / self.resolution)
        row = math.floor((y - self.origin_y) / self.resolution)
        if col < 0 or col >= self.width or row < 0 or row >= self.height:
            return None
        return int(self.data[row * self.width + col])


def first_occupied(grid: Grid, x: float, y: float, yaw: float,
                   max_m: float = 1.0, occupied_min: int = 100,
                   step_frac: float = 0.5):
    """`(x, y)` 에서 `yaw` 방향으로 나아가며 처음 만나는 점유 셀.

    `((hx, hy), 거리)` 를 돌려준다. 격자를 벗어나거나 `max_m` 까지 아무것도 못 만나면
    `None` — **"모른다"를 "없다"로 바꾸지 않는다.**
    """
    step = grid.resolution * step_frac
    if step <= 0.0:
        return None
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    d = 0.0
    while d <= max_m:
        px, py = x + cos_y * d, y + sin_y * d
        v = grid.value_at(px, py)
        if v is None:
            return None
        if v >= occupied_min:
            return (px, py), d
        d += step
    return None
