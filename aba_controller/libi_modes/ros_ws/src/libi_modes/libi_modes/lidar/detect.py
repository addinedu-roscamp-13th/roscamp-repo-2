"""도크 벽의 노치를 찾는다 — RANSAC 직선 + 브레이크포인트.

## 좌표계 (틀리면 로봇이 반대로 간다)

`/scan` 원본을 `rplidar_link` 그대로 읽는다. **0 rad = 로봇의 물리적 뒤**다
(`pinky.urdf.xacro:201` 의 `rpy="0 0 ${pi}"`, 2026-07-30 실측). 후진 진행 방향이
곧 0 rad 이므로:

    x = r·cos(a)    후진축. 양수 = 도크 쪽
    y = r·sin(a)    좌우

⚠️ `y` 의 부호가 로봇의 좌/우 중 어느 쪽인지는 **코드로 못 정한다.** 라이다가 z축
   π 회전으로 달려 있기 때문이다. `cfg.steer_sign` 이 현장에서 그것을 정한다.

## `scan_filtered` 를 안 쓰는 이유

그쪽은 min_range 0.05 로 5cm 미만을 지운다. 근접 도킹에는 그 구간이 필요하다
(기존 ArUco 도킹의 `ScanWatch` 도 같은 이유로 원본을 쓴다).

## `inf` 는 무효가 아니다

LaserScan 규약에서 `+inf` 는 "그 범위 안에 아무것도 없음"이고 정상값이다. 무효로
세면 탁 트인 공간을 라이다 고장으로 오판한다. 다만 직교 변환에는 못 쓰므로 여기서는
버린다 — "고장"과 "트임"의 구분은 상위(`detect()` 의 반환 None)에서 한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from libi_modes.lidar.config import LidarDockConfig


def sector_points(ranges, angle_min, angle_increment, range_min, range_max,
                  cfg: LidarDockConfig) -> np.ndarray:
    """후방 섹터의 유효 광선을 `(N, 2)` 직교좌표로. **각도 오름차순.**

    정렬이 계약의 일부다 — 뒤에서 e 를 이동평균으로 평활하는데, 그 연산이 이웃한
    광선끼리 인접해 있다는 것을 전제한다. 순서가 섞이면 노치가 뭉개진다.
    """
    r = np.asarray(ranges, dtype=float)
    if r.size == 0 or angle_increment == 0.0:
        return np.empty((0, 2), dtype=float)
    a = angle_min + np.arange(r.size) * float(angle_increment)
    a = np.arctan2(np.sin(a), np.cos(a))               # (-pi, pi] 로 감는다
    half = math.radians(cfg.sector_half_deg)
    lo = max(float(range_min), 1e-3)
    hi = min(float(range_max), float(cfg.range_max_m))
    ok = (np.abs(a) <= half) & np.isfinite(r) & (r >= lo) & (r <= hi)
    a, r = a[ok], r[ok]
    order = np.argsort(a, kind="stable")
    a, r = a[order], r[order]
    return np.stack([r * np.cos(a), r * np.sin(a)], axis=1)
