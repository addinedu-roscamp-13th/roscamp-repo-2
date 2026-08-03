"""라이다 노치 검출 — 합성 스캔으로 검증한다.

## 왜 합성 스캔인가

실기 스캔은 정답을 모른다. 합성 스캔은 `d`·`y`·`yaw` 를 우리가 정해서 넣으므로
**오차를 숫자로 잴 수 있다.** 실기 스캔은 나중에 회귀 고정용으로 따로 추가한다
(Task 3~5 는 로봇 없이 끝나야 한다).

## 좌표계

0 rad = 로봇의 물리적 뒤 = 후진 진행 방향. x = 도크 쪽, y = 좌우.
"""
import math

import numpy as np
import pytest

from libi_modes.lidar import detect
from libi_modes.lidar.config import LidarDockConfig

N_RAYS = 500                    # RPLIDAR C1 10Hz: 5kHz / 10Hz
ANGLE_MIN = -math.pi
ANGLE_INC = 2 * math.pi / N_RAYS
RANGE_MIN, RANGE_MAX = 0.05, 12.0


def make_scan(wall_m=0.30, notch_w=0.06, notch_d=0.025, y_off=0.0, yaw=0.0,
              noise=0.0, seed=0, notch=True):
    """벽 + 노치를 가진 합성 스캔을 만든다.

    벽은 원점에서 수직거리 `wall_m`, 법선이 x축과 `yaw` 를 이루는 직선이다.
    노치는 벽을 따라 `y_off` 를 중심으로 폭 `notch_w`, 깊이 `notch_d` 인 평평한 홈.
    (실제 노치는 V 자지만 임계 교차로 찾으므로 평평한 홈이 더 엄격한 시험이다 —
     경계가 뚜렷해 위치 오차가 그대로 드러난다.)
    """
    rng = np.random.default_rng(seed)
    ranges = []
    for i in range(N_RAYS):
        a = ANGLE_MIN + i * ANGLE_INC
        c = math.cos(a - yaw)
        if c <= 1e-3:                       # 벽을 등진 광선
            ranges.append(float("inf"))
            continue
        t = wall_m / c
        s = t * math.sin(a - yaw)           # 벽을 따라간 좌표
        if notch and abs(s - y_off) < notch_w / 2:
            t = (wall_m + notch_d) / c
        if noise:
            t += rng.normal(0.0, noise)
        ranges.append(float(t) if t > RANGE_MIN else float("inf"))
    return ranges


def test_sector_points_are_sorted_by_angle():
    """평활(이동평균)이 각도 순서를 전제한다. 순서가 섞이면 노치가 뭉개진다."""
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(), ANGLE_MIN, ANGLE_INC,
                               RANGE_MIN, RANGE_MAX, cfg)
    angles = np.arctan2(pts[:, 1], pts[:, 0])
    assert np.all(np.diff(angles) >= -1e-9)


def test_sector_points_stay_inside_the_sector():
    cfg = LidarDockConfig(sector_half_deg=60.0)
    pts = detect.sector_points(make_scan(), ANGLE_MIN, ANGLE_INC,
                               RANGE_MIN, RANGE_MAX, cfg)
    angles = np.degrees(np.arctan2(pts[:, 1], pts[:, 0]))
    assert np.all(np.abs(angles) <= 60.0 + 1e-6)


def test_sector_points_drop_unmeasurable_rays():
    cfg = LidarDockConfig()
    ranges = make_scan()
    ranges[N_RAYS // 2] = float("nan")
    pts_all = detect.sector_points(make_scan(), ANGLE_MIN, ANGLE_INC,
                                   RANGE_MIN, RANGE_MAX, cfg)
    pts_one_bad = detect.sector_points(ranges, ANGLE_MIN, ANGLE_INC,
                                       RANGE_MIN, RANGE_MAX, cfg)
    assert len(pts_one_bad) == len(pts_all) - 1


def test_sector_points_x_is_toward_the_dock():
    """0 rad 이 후진 진행 방향이므로 x 는 도크 쪽 거리여야 한다."""
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(wall_m=0.30, notch=False),
                               ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    assert pts[:, 0].min() == pytest.approx(0.30, abs=0.01)


def test_sector_points_sorted_across_the_0_360_wrap():
    """RPLIDAR 류처럼 원본 스캔이 `angle_min=0` 으로 0..2π 를 보고하면, 후방(0 rad)
    섹터는 배열의 시작(작은 인덱스, 작은 양의 각도)과 끝(큰 인덱스, 2π 에 가까워
    래핑되면 작은 음의 각도) 경계에 걸쳐 나뉜다. 정렬이 없으면 두 조각이 원본
    인덱스 순서 그대로(양수 뭉치 다음 음수 뭉치)로 이어져 각도가 거꾸로 뛴다 —
    Task 1 `scan_dump` 의 wrap 시험(`test_rows_sorted_across_the_0_360_wrap`)과
    같은 부류의 경계 사례이며, 브리프의 `ANGLE_MIN=-pi` 고정 스캔에서는 이 경계가
    섹터 밖(±60° 밖)이라 드러나지 않는다.
    """
    cfg = LidarDockConfig(sector_half_deg=60.0)
    n = 360
    angle_min = 0.0
    angle_inc = 2 * math.pi / n
    ranges = [0.5] * n
    pts = detect.sector_points(ranges, angle_min, angle_inc, RANGE_MIN, RANGE_MAX, cfg)
    assert len(pts) > 0
    angles = np.arctan2(pts[:, 1], pts[:, 0])
    assert np.all(np.diff(angles) >= -1e-9)
