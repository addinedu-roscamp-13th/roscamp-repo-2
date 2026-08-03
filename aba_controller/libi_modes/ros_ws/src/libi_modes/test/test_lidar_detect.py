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


def test_fit_wall_finds_distance_and_yaw():
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(wall_m=0.30, yaw=0.0),
                               ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    wall = detect.fit_wall(pts, cfg)
    assert wall is not None
    assert wall.offset == pytest.approx(0.30, abs=0.005)
    assert wall.yaw == pytest.approx(0.0, abs=0.02)


def test_fit_wall_measures_a_tilted_wall():
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(wall_m=0.30, yaw=0.20),
                               ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    wall = detect.fit_wall(pts, cfg)
    assert wall is not None
    assert wall.yaw == pytest.approx(0.20, abs=0.03)


def test_fit_wall_is_not_dragged_by_the_notch():
    """노치 점들은 구조적 이상점이다. 단순 최소제곱이면 직선이 그쪽으로 끌려가
    **yaw 가 틀린 채로 로봇이 비스듬히 들어간다** — 조용히 틀리는 부류다."""
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(wall_m=0.30, y_off=0.10, notch_d=0.025),
                               ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    wall = detect.fit_wall(pts, cfg)
    assert wall is not None
    assert wall.yaw == pytest.approx(0.0, abs=0.02)
    assert wall.offset == pytest.approx(0.30, abs=0.005)


def test_fit_wall_is_not_dragged_by_the_notch_at_close_range():
    """위 시험(`wall_m=0.30`)은 노치를 plain refit(TLS, RANSAC 없이)으로 돌려도
    170 점 중 12 점뿐이라 여유 있게 통과해 버린다 — brief 의 Step 5 되돌리기가
    이 시험으로는 실제로 빨개지지 않는다(실측 확인함). 도킹 막바지는 벽에 훨씬
    가깝다(`cfg.stop_m=0.065`, `cfg.v_far_dist_m=0.30`) — `wall_m=0.15`(같은
    섹터각에서 벽까지 거리가 절반이면 점군의 y 폭도 절반이라 노치가 차지하는
    비중이 커진다)에 config 유효범위 안(`notch_depth_max_m=0.040`)의 노치를
    두면 plain refit 이 실제로 끌려간다(실측: yaw −0.027rad, offset +6.6mm 오차,
    둘 다 아래 허용오차를 벗어난다) — 이 시험이 RANSAC 이 필요하다는 것의
    진짜 증거다."""
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(wall_m=0.15, y_off=0.05, notch_d=0.040),
                               ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    wall = detect.fit_wall(pts, cfg)
    assert wall is not None
    assert wall.yaw == pytest.approx(0.0, abs=0.02)
    assert wall.offset == pytest.approx(0.15, abs=0.005)


def test_fit_wall_rejects_a_scan_with_no_wall():
    """점들이 직선을 이루지 않으면(사람 다리 등) 벽이 아니다.
    노치를 벽 직선 위에서만 찾으므로, 이 기각이 곧 오검출 1차 방어다."""
    cfg = LidarDockConfig()
    rng = np.random.default_rng(1)
    pts = np.stack([rng.uniform(0.2, 1.0, 200), rng.uniform(-0.5, 0.5, 200)], axis=1)
    assert detect.fit_wall(pts, cfg) is None


def test_fit_wall_rejects_too_few_points():
    cfg = LidarDockConfig(min_points=20)
    pts = np.array([[0.3, 0.0], [0.3, 0.01], [0.3, 0.02]])
    assert detect.fit_wall(pts, cfg) is None


def test_fit_wall_is_deterministic():
    """RANSAC 이 난수를 쓰지만 같은 입력에 같은 답이 나와야 한다 — 안 그러면
    현장에서 '가끔 안 붙는다'가 재현 불가능해진다."""
    cfg = LidarDockConfig()
    pts = detect.sector_points(make_scan(), ANGLE_MIN, ANGLE_INC,
                               RANGE_MIN, RANGE_MAX, cfg)
    a = detect.fit_wall(pts, cfg)
    b = detect.fit_wall(pts, cfg)
    assert a.yaw == b.yaw and a.offset == b.offset
