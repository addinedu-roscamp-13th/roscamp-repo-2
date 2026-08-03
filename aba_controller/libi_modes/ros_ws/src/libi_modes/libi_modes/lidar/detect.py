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


@dataclass(frozen=True)
class Wall:
    """도크 벽에 맞춘 직선.

    `normal` 은 **원점에서 벽 쪽**을 향하는 단위벡터다. 방향을 고정하지 않으면
    `yaw` 부호가 스캔마다 뒤집혀 조향이 반대로 걸린다.
    """
    normal: np.ndarray
    offset: float
    yaw: float
    rms: float
    inliers: np.ndarray


def _refit(pts: np.ndarray) -> tuple[np.ndarray, float, float]:
    """전최소제곱(PCA)으로 재피팅 → `(normal, offset, rms)`.

    최소제곱이 아니라 전최소제곱을 쓰는 이유: 보통의 y-on-x 최소제곱은 벽이 수직에
    가까워지면 발산한다. 도크 벽은 로봇 정면에 서므로 정확히 그 경우다.
    """
    mean = pts.mean(axis=0)
    centred = pts - mean
    # 공분산의 최소 고윳값에 대응하는 고유벡터가 법선이다.
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    normal = vt[-1]
    offset = float(normal @ mean)
    if offset < 0:                       # 원점에서 벽 쪽을 향하게 고정한다
        normal, offset = -normal, -offset
    rms = float(np.sqrt(np.mean((centred @ normal) ** 2)))
    return normal, offset, rms


def fit_wall(pts: np.ndarray, cfg: LidarDockConfig, rng=None) -> Wall | None:
    """RANSAC 으로 벽 직선을 찾고 inlier 로 재피팅한다. 못 믿으면 `None`.

    ## 왜 단순 최소제곱이 아닌가

    노치에 해당하는 점들이 **구조적으로** 이상점이다. 단순 최소제곱은 그쪽으로
    끌려가 벽이 기울어진 것처럼 나오고, 그러면 yaw 가 틀린 채로 로봇이 비스듬히
    들어간다. 예외도 로그도 없이 조용히 틀린다.

    ## 왜 난수 씨앗을 고정하나

    같은 스캔에 매번 같은 답이 나와야 한다. 안 그러면 현장에서 "가끔 안 붙는다"가
    재현 불가능해지고, 그때부터는 검출을 의심할 수도 믿을 수도 없게 된다.
    """
    n = len(pts)
    if n < cfg.min_points:
        return None
    rng = np.random.default_rng(0) if rng is None else rng
    tol = float(cfg.ransac_inlier_m)

    best_mask, best_count = None, 0
    for _ in range(int(cfg.ransac_iters)):
        i, j = rng.choice(n, size=2, replace=False)
        d = pts[j] - pts[i]
        norm = math.hypot(d[0], d[1])
        if norm < 1e-6:
            continue
        nvec = np.array([-d[1] / norm, d[0] / norm])
        mask = np.abs((pts - pts[i]) @ nvec) <= tol
        count = int(mask.sum())
        if count > best_count:
            best_mask, best_count = mask, count

    if best_mask is None or best_count < max(2, int(cfg.ransac_min_inlier_ratio * n)):
        return None

    normal, offset, rms = _refit(pts[best_mask])
    if rms > cfg.wall_rms_max_m:
        return None
    yaw = math.atan2(float(normal[1]), float(normal[0]))
    if abs(yaw) > cfg.wall_yaw_max_rad:
        # 이만큼 비뚤어졌으면 도크 벽을 본 게 아닐 확률이 높다.
        return None
    return Wall(normal=normal, offset=offset, yaw=yaw, rms=rms, inliers=best_mask)
