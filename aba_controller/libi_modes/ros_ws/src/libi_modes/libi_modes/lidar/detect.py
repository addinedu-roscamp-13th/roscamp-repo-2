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


@dataclass(frozen=True)
class NotchObs:
    """도크 노치 관측 한 장.

    `detect()` 는 못 믿을 때 `None` 을 돌려준다 — 신뢰도를 별도 필드로 들고 다니면
    소비자가 확인을 잊는 자리가 생긴다. 값이 있으면 믿어도 되는 값이다.
    """
    d: float          # 벽 법선에 투영한 노치 바닥까지 거리 (m)
    y: float          # 벽 방향으로 잰 노치 축 이탈 (m)
    yaw: float        # 벽 법선 ↔ 후진축 (rad). NEAR 모드에서는 0.0 (못 잰다)
    depth: float      # 노치 깊이 (m) — 오검출 방어에 쓴다. NEAR 에서는 nan
    wall_m: float     # 벽까지 수직거리 (m) — 국면 판정에 쓴다. NEAR 에서는 nan
    near: bool = False  # NEAR 검출기가 낸 값인가 (yaw·depth·wall_m 를 믿으면 안 된다)


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    """이동평균. RPLIDAR C1 의 거리 분해능이 15mm 라 점별 임계로는 못 가른다.

    노치 깊이 25mm 는 눈금의 1.67배밖에 안 된다. 점 하나로 판정하면 경계 점들이
    매 스캔 깜빡인다. 폭 W 로 평활하면 잡음이 √W 로 줄어든다.
    """
    if width <= 1 or values.size < width:
        return values
    kernel = np.ones(int(width), dtype=float) / float(width)
    return np.convolve(values, kernel, mode="same")


def _longest_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """`True` 가 연속된 구간들을 `(시작, 끝포함)` 으로. 브레이크포인트 검출."""
    runs, start = [], None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def detect(ranges, angle_min, angle_increment, range_min, range_max,
           cfg: LidarDockConfig, rng=None) -> NotchObs | None:
    """스캔 한 장에서 노치를 찾는다. 못 믿으면 `None`.

    ## 왜 노치를 벽 직선 **위에서만** 찾나

    이것이 오검출 1차 방어를 공짜로 준다. 도크 앞을 지나는 사람 다리나 의자는 직선을
    이루지 못해 `fit_wall` 이 먼저 기각한다. 거리 프로파일만 보는 방식이었다면
    다리 사이 틈을 노치로 읽었을 것이다.

    ## 왜 중심을 봉우리 꼭짓점이 아니라 양 끝의 중점으로 잡나

    꼭짓점은 표본 하나라 잡음이 그대로 실리고, V 의 경사면은 빔이 비스듬히 맞아 값이
    가장 나쁜 자리다. 두 브레이크포인트의 중점은 좌우 오차가 상쇄된다.

    ## 왜 거리를 x 성분으로 재나

    원시 range 는 광선을 따라 잰 값이라, 좌우로 어긋나 있으면 축거리와 다르다. 그
    차이가 `y` 에 따라 변하므로 `stop_m` 상수 보정으로 지워지지 않는다.
    """
    pts = sector_points(ranges, angle_min, angle_increment, range_min, range_max, cfg)
    wall = fit_wall(pts, cfg, rng=rng)
    if wall is None:
        return None

    # 직선보다 얼마나 뒤로 파였나. 양수 = 더 멀다.
    e = (pts - pts[wall.inliers].mean(axis=0)) @ wall.normal
    e_smooth = _smooth(e, cfg.smooth_rays)

    runs = _longest_runs(e_smooth >= cfg.notch_thresh_m)
    if not runs:
        return None

    # ⚠️ **벽 정렬 좌표계로 잰다.** 라이다 프레임의 x·y 를 쓰면 벽이 기울었을 때
    #    노치 중심의 라이다-y 가 `d·sin(기울기)` 만큼 어긋난다 — 실측으로 기울기 17°
    #    에서 93mm 다. 그러면 조향식 `ω = k_y·y + k_yaw·θ` 의 두 항이 서로 싸운다.
    #    벽 방향 `w` 로 투영하면 기울기 23° 까지 오차가 2mm 안에 든다.
    wvec = np.array([-wall.normal[1], wall.normal[0]])

    best = None
    for lo, hi in runs:
        if hi - lo < 1:
            continue
        s_lo, s_hi = float(pts[lo] @ wvec), float(pts[hi] @ wvec)
        width = abs(s_hi - s_lo)
        if not (cfg.notch_width_min_m <= width <= cfg.notch_width_max_m):
            continue
        centre = 0.5 * (s_lo + s_hi)
        if best is None or abs(centre) < abs(best[2]):
            best = (lo, hi, centre)
    if best is None:
        return None

    lo, hi, centre = best
    depth = float(np.mean(e_smooth[lo:hi + 1]))
    if not (cfg.notch_depth_min_m <= depth <= cfg.notch_depth_max_m):
        return None

    # 구간 중앙 50% 의 법선 성분 평균 — 가장자리는 빔이 경계를 걸쳐 값이 섞인다
    # (mixed pixel). 그 지점의 값은 벽도 노치도 아니다.
    span = hi - lo + 1
    trim = span // 4
    core = pts[lo + trim:hi + 1 - trim]
    if len(core) == 0:
        core = pts[lo:hi + 1]
    return NotchObs(
        d=float((core @ wall.normal).mean()),
        y=centre,
        yaw=wall.yaw,
        depth=depth,
        wall_m=wall.offset,
        near=False,
    )


def detect_near(ranges, angle_min, angle_increment, range_min, range_max,
                cfg: LidarDockConfig) -> NotchObs | None:
    """**벽이 사라진 뒤** 쓰는 검출기 — 직선을 안 맞춘다.

    ## 왜 따로 필요한가 (P0)

    최종 정지에서 라이다→노치가 6~7cm 면 벽면은 4cm 로 C1 최소 거리(5cm) 아래다.
    `fit_wall` 이 실패하고, `detect()` 는 노치를 그 직선 **위에서** 찾으므로
    **노치가 멀쩡히 보여도 `None` 을 낸다.** 그러면 `lost_frames_max` 에 걸려
    목표 2cm 앞에서 도킹이 중단된다. 실측 검출률: 벽 7cm 100% → 벽 5cm **0%**.

    ## 왜 이때는 더 쉬운가

    최소 거리 아래의 벽이 통째로 사라지므로, **중앙 창에 남는 반사가 노치뿐이다.**
    센서의 사각지대가 필터 노릇을 한다. 직선을 맞출 필요가 없다.

    ## ⚠️ 이것은 독립 검출기가 아니라 **인수인계**다

    직선 피팅이라는 1차 오검출 방어가 없다. 실측: 노치 **없는** 평평한 벽 4.5cm 에서
    100% "검출"한다. 그래서 호출하는 쪽이 `FINAL` 국면에서만 부른다 — `ACQUIRE` 는
    절대 쓰지 않는다. 오검출로 도킹이 **시작**될 수 없게 하는 구조적 방어다.

    yaw 는 못 낸다(맞출 벽이 없다). `FINAL` 은 어차피 yaw 가중치가 0 이다.
    """
    r = np.asarray(ranges, dtype=float)
    if r.size == 0 or angle_increment == 0.0:
        return None
    a = angle_min + np.arange(r.size) * float(angle_increment)
    a = np.arctan2(np.sin(a), np.cos(a))
    half = math.radians(cfg.near_half_deg)
    ok = (np.abs(a) <= half) & np.isfinite(r) & (r >= max(float(range_min), 1e-3)) & (r <= 0.5)
    a, r = a[ok], r[ok]
    if a.size < 6:
        return None
    order = np.argsort(a, kind="stable")
    a, r = a[order], r[order]

    # 거리 도약으로 물체 경계를 끊는다 (adaptive breakpoint).
    cuts = np.where(np.abs(np.diff(r)) > cfg.near_gap_m)[0]
    groups, start = [], 0
    for c in cuts:
        groups.append((start, int(c)))
        start = int(c) + 1
    groups.append((start, len(r) - 1))

    best = None
    for lo, hi in groups:
        if hi - lo < 3:
            continue
        seg_a, seg_r = a[lo:hi + 1], r[lo:hi + 1]
        x, y = seg_r * np.cos(seg_a), seg_r * np.sin(seg_a)
        width = float(abs(y[-1] - y[0]))
        if not (cfg.near_width_min_m <= width <= cfg.near_width_max_m):
            continue
        centre = 0.5 * float(y[0] + y[-1])
        if best is None or abs(centre) < abs(best[0]):
            best = (centre, float(np.mean(x)))
    if best is None:
        return None
    centre, d = best
    return NotchObs(d=d, y=centre, yaw=0.0, depth=float("nan"),
                    wall_m=float("nan"), near=True)
