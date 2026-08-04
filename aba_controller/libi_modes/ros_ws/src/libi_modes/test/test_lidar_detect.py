"""라이다 노치 검출 — 합성 스캔으로 검증한다.

## 왜 합성 스캔인가

실기 스캔은 정답을 모른다. 합성 스캔은 `d`·`y`·`yaw` 를 우리가 정해서 넣으므로
**오차를 숫자로 잴 수 있다.** 실기 스캔은 나중에 회귀 고정용으로 따로 추가한다
(Task 3~5 는 로봇 없이 끝나야 한다).

## 좌표계

0 rad = 로봇의 물리적 뒤 = 후진 진행 방향. x = 도크 쪽, y = 좌우.
"""
import math
from dataclasses import replace

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


def _wall_pts(bearing_rad: float, dist_m: float, n: int, span_m: float = 0.3):
    """`bearing_rad` 방향, `dist_m` 거리에 놓인 평평한 벽 점들을 만든다.
    벽은 그 방향에 수직(로봇을 정면으로 보는 벽)이라고 둔다 — `fit_wall_near_bearing`
    시험에서 "이 후보가 어디 있는가"만 조절하면 되므로 이 정도면 충분하다."""
    nx, ny = math.cos(bearing_rad), math.sin(bearing_rad)
    tx, ty = -ny, nx
    s = np.linspace(-span_m / 2, span_m / 2, n)
    return np.stack([nx * dist_m + tx * s, ny * dist_m + ty * s], axis=1)


def test_fit_wall_near_bearing_prefers_the_dock_candidate_over_a_bigger_off_bearing_wall():
    """[2026-08-04 실측, codex P1 확인] 넓은 SEARCH 창에서 순수 inlier 최다 기준은
    도크와 무관한 방의 다른 벽을 우선할 수 있다 — 실측: 200점짜리 옆벽이 110점짜리
    도크 벽을 이겼다. `fit_wall_near_bearing` 은 bearing·거리 사전지식으로 막는다."""
    cfg = LidarDockConfig()
    # SEARCH 가 실제로 쓰는 넓힌 cfg — `wall_yaw_max_rad` 를 `search_wall_yaw_max_rad`
    # 로 바꾼 것(드라이버의 `_search_cfg` 와 같은 구성). 이걸 안 쓰면(그냥 좁은 기본
    # cfg 로 plain fit_wall 을 부르면) 옆벽 자체의 yaw(75도)가 기본 35도 문턱에
    # 걸려 plain 도 그냥 None 을 내서 이 시험의 전제(옆벽이 이긴다)가 성립 안 한다.
    search_cfg = replace(cfg, wall_yaw_max_rad=cfg.search_wall_yaw_max_rad)
    dock_pts = _wall_pts(bearing_rad=0.0, dist_m=0.30, n=60, span_m=0.1)
    # 점도 더 많고(120>60) rms 도 더 좋을 방의 다른 벽 — bearing·거리 둘 다 범위 밖
    side_pts = _wall_pts(bearing_rad=math.radians(75), dist_m=1.3, n=120, span_m=0.6)
    pts = np.vstack([dock_pts, side_pts])

    plain = detect.fit_wall(pts, search_cfg, rng=np.random.default_rng(1))
    assert plain is not None and plain.inliers.sum() >= 100, (
        "이 시험의 전제 확인 — 옆벽(점 더 많음)이 plain fit_wall 에서 이겨야 한다")

    picked = detect.fit_wall_near_bearing(pts, cfg, rng=np.random.default_rng(1))
    assert picked is not None
    assert picked.offset == pytest.approx(0.30, abs=0.02), "옆벽이 아니라 도크 벽을 골라야 한다"
    assert abs(picked.yaw) < math.radians(5)


def test_fit_wall_near_bearing_still_accepts_a_large_yaw_dock_candidate():
    """SEARCH 를 만든 원래 이유(2026-08-03, 벽 yaw -40.7도)를 이 필터가 다시
    깨면 안 된다 — bearing·거리는 좁히되 벽 자체의 기울기는 여전히 넓게 허용해야
    한다. bearing 0도 근처에 있지만 그 벽 자체는 크게 기운 경우를 시험한다."""
    cfg = LidarDockConfig()
    # 벽은 bearing 0도 근처에 있지만(가까운 거리), 벽 자체 방향은 크게 기울어 있다
    # (로봇이 잘못 돌아 있어서 벽이 비스듬히 보이는 상황을 흉내)
    tilt = math.radians(-41)
    n = 80
    s = np.linspace(-0.15, 0.15, n)
    base = np.array([0.30, 0.0])
    tangent = np.array([math.sin(tilt), math.cos(tilt)])
    pts = base + np.outer(s, tangent)

    wall = detect.fit_wall_near_bearing(pts, cfg, rng=np.random.default_rng(1))
    assert wall is not None, "bearing 은 가까운데도 거절되면 SEARCH 가 못 쓴다"
    # ⚠️ 부호가 아니라 크기만 본다 — `_refit` 이 `offset>=0` 이 되도록 법선을
    # 뒤집을 수 있어(180도 모호성 해소, detect.py 참고) 부호는 손으로 만든
    # 기하의 접선 방향 관례에 좌우된다. 여기서 잠그려는 건 "35도 훨씬 넘는
    # 기울기도 거절 안 한다"는 것이지 부호 관례가 아니다.
    assert abs(wall.yaw) == pytest.approx(abs(tilt), abs=0.05)


def test_detect_prefers_the_facing_wall_over_a_bigger_off_bearing_side_wall():
    """[2026-08-04 실측, 세 번째 자리] `detect()` 도 SEARCH 와 같은 결함을
    겪는다 — 좁은 60도 창 안에서도 순수 inlier 최다 기준이면 옆벽에 진다.
    실기 재현(pinky-3): bearing 1.9도에 진짜 도크 벽(56점, yaw -1.0°) 이
    있는데, bearing 45~50도대 옆벽이 inlier 수로 이겨서 최종 yaw 문턱에
    걸려 벽 fit 자체가 통째로 `None` 이 났다(dock_scan_visualize 로 확인).
    `detect()` 내부와 같은 방식(`wall_bearing_tol_rad` 로 매핑한 cfg)으로
    `fit_wall_near_bearing` 을 불러 정면 후보를 고르는지 확인한다."""
    cfg = LidarDockConfig()
    dock_pts = _wall_pts(bearing_rad=0.0, dist_m=0.30, n=56, span_m=0.12)
    # `_wall_pts` 는 그 방향을 정면으로 보는 벽을 만들므로 bearing 이 곧 그 벽
    # 자신의 yaw 다 — wall_yaw_max_rad(50도, 오늘 완화됨) 보다 확실히 크게
    # 잡아야 plain fit_wall 이 여전히 거절한다.
    side_pts = _wall_pts(bearing_rad=math.radians(70), dist_m=0.25, n=120, span_m=0.5)
    pts = np.vstack([dock_pts, side_pts])

    plain = detect.fit_wall(pts, cfg, rng=np.random.default_rng(1))
    assert plain is None, (
        "이 시험의 전제 확인 — 옆벽이 더 커서 plain fit_wall 은 yaw 문턱에 걸려 None 이어야 한다")

    bearing_cfg = replace(cfg, search_bearing_tol_rad=cfg.wall_bearing_tol_rad,
                          search_wall_yaw_max_rad=cfg.wall_yaw_max_rad)
    picked = detect.fit_wall_near_bearing(pts, bearing_cfg, rng=np.random.default_rng(1))
    assert picked is not None
    assert picked.offset == pytest.approx(0.30, abs=0.02), "옆벽이 아니라 정면 벽을 골라야 한다"


def test_fit_wall_near_bearing_returns_none_when_only_an_off_bearing_wall_exists():
    """도크 후보가 아예 없을 때(전부 범위 밖) — 잘못된 벽을 억지로 고르지 말고
    `None` 을 내야 SEARCH 가 계속 돈다(엉뚱한 방향으로 확정하지 않는다)."""
    cfg = LidarDockConfig()
    pts = _wall_pts(bearing_rad=math.radians(80), dist_m=1.4, n=100, span_m=0.6)
    assert detect.fit_wall_near_bearing(pts, cfg, rng=np.random.default_rng(1)) is None


def test_fit_wall_near_bearing_accepts_a_small_dock_wall_diluted_by_a_wide_sector():
    """[2026-08-04 실측, 두 번째 결함] `ransac_min_inlier_ratio × n` 을 그대로 쓰면
    안 된다 — SEARCH 는 섹터를 175도로 넓혀서 쓰는데, 그러면 방 잡음까지 다 잡혀
    n 자체가 커진다(실측 689). 도크 벽 점 개수는 안 변하는데(실측 87) 분모만
    커지니 비율 문턱을 절대 못 넘는다 — bearing·거리 게이트는 다 통과하는데도
    이 줄에서 `None` 이 났다(dock_scan_visualize 15초 연속 재현, 화면엔 벽이 또렷이
    보이는데 SEARCH 만 계속 "searching"). 도크 벽(87점)을 방향·거리 다 안 맞는
    잡음 600점 속에 심어서, 비율 기준이었다면 실패했을 바로 그 비율을 재현한다."""
    cfg = LidarDockConfig()
    dock_pts = _wall_pts(bearing_rad=0.0, dist_m=0.275, n=87, span_m=0.15)
    # 방향(80도, 게이트 60도 밖)·거리(1.4m, 게이트 1.0m 밖) 둘 다 벗어난 잡음이라
    # 자기들끼리 벽으로 뽑혀도 게이트에서 걸린다 — 오직 n(분모)만 불린다.
    rng = np.random.default_rng(3)
    noise = np.stack([rng.uniform(-1.5, 1.5, 600), rng.uniform(-1.5, 1.5, 600)], axis=1)
    noise = noise[np.hypot(noise[:, 0], noise[:, 1]) > 1.0]  # 게이트(1.0m) 밖으로
    pts = np.vstack([dock_pts, noise])
    assert len(pts) > 300, "이 시험의 전제 — n 이 커야 비율 문턱이 재현된다"

    wall = detect.fit_wall_near_bearing(pts, cfg, rng=np.random.default_rng(1))
    assert wall is not None, "방향·거리는 다 맞는데 n 이 커졌다고 벽을 놓치면 안 된다"
    assert wall.offset == pytest.approx(0.275, abs=0.02)


def test_fit_wall_near_bearing_still_rejects_a_cluster_below_the_min_inlier_floor():
    """절대 개수 문턱을 아예 없앤 게 아니다 — bearing·거리는 맞아도 점이 너무
    적으면(잡음일 확률이 높다) 여전히 거절해야 한다."""
    cfg = LidarDockConfig()
    tiny = _wall_pts(bearing_rad=0.0, dist_m=0.30, n=cfg.search_wall_min_inliers - 5, span_m=0.05)
    assert detect.fit_wall_near_bearing(tiny, cfg, rng=np.random.default_rng(1)) is None


def _detect(cfg=None, **scan_kw):
    cfg = cfg or LidarDockConfig()
    return detect.detect(make_scan(**scan_kw), ANGLE_MIN, ANGLE_INC,
                         RANGE_MIN, RANGE_MAX, cfg)


def test_detect_finds_a_centred_notch():
    obs = _detect(wall_m=0.30, y_off=0.0, notch_d=0.025)
    assert obs is not None
    assert obs.y == pytest.approx(0.0, abs=0.005)
    assert obs.depth == pytest.approx(0.025, abs=0.005)
    assert obs.d == pytest.approx(0.325, abs=0.006)
    assert obs.wall_m == pytest.approx(0.30, abs=0.005)


def test_detect_measures_lateral_offset():
    obs = _detect(wall_m=0.30, y_off=0.08)
    assert obs is not None
    assert obs.y == pytest.approx(0.08, abs=0.008)


def test_detect_returns_none_when_there_is_no_notch():
    """없는 것을 찾았다고 하는 것이 가장 위험한 실패다."""
    assert _detect(wall_m=0.30, notch=False) is None


def test_detect_returns_none_for_an_empty_scan():
    cfg = LidarDockConfig()
    assert detect.detect([float("inf")] * N_RAYS, ANGLE_MIN, ANGLE_INC,
                         RANGE_MIN, RANGE_MAX, cfg) is None


def test_detect_rejects_a_notch_that_is_too_shallow():
    """깊이 게이트가 오검출 2차 방어다. 얕은 굴곡은 노치가 아니다."""
    assert _detect(wall_m=0.30, notch_d=0.005) is None


def test_detect_rejects_a_shallow_notch_that_still_crosses_the_breakpoint():
    """위 시험(`notch_d=0.005`)은 브레이크포인트 임계(`notch_thresh_m=0.012`)보다도
    얕아서 `_longest_runs` 단계에서 이미 걸러진다 — brief 의 Step 5 되돌리기(깊이
    게이트 삭제)를 실제로 적용해도 이 시험은 빨개지지 않는다(실측 확인함). 깊이
    게이트 자체를 시험하려면 브레이크포인트는 넘되(> 0.012) 최소 깊이는 못 미치는
    (< notch_depth_min_m) 값이 필요하다.

    ⚠️ [2026-08-04 실측 정정] notch_depth_min_m 을 0.015→0.013 으로 낮췄다(검출
    실패가 너무 잦다는 실기 지적 — 사용자가 오검출은 화면 보고 직접 잡기로
    했다). 그래서 `notch_d=0.013` 은 더 이상 이 시험의 반례가 못 된다 — 그 값을
    `0.0125` 로 낮춰 여전히 `notch_thresh_m`(0.012) 은 넘고 새 min(0.013) 은
    못 미치는 값을 쓴다."""
    assert _detect(wall_m=0.30, notch_d=0.0125) is None


def test_detect_rejects_a_notch_that_is_too_wide():
    assert _detect(wall_m=0.30, notch_w=0.30) is None


@pytest.mark.parametrize("noise,tol_y,tol_d", [
    (0.000, 0.005, 0.006),
    (0.005, 0.010, 0.010),
    (0.015, 0.020, 0.020),        # 센서 분해능 수준. 성능 저하 곡선을 고정한다
])
def test_detect_degrades_gracefully_with_noise(noise, tol_y, tol_d):
    obs = _detect(wall_m=0.30, y_off=0.04, noise=noise, seed=7)
    assert obs is not None, f"잡음 {noise*1000:.0f}mm 에서 검출 실패"
    assert obs.y == pytest.approx(0.04, abs=tol_y)
    assert obs.d == pytest.approx(0.325, abs=tol_d)


def test_detect_distance_is_projected_not_raw_range():
    """원시 range 를 쓰면 좌우로 어긋났을 때 축거리보다 길게 나온다.

    이 오차는 `y` 에 따라 변하므로 `stop_m` 상수 보정으로 못 지운다. 도킹 거리에서
    이 차이가 목표 정밀도(5mm)보다 크다.
    """
    centred = _detect(wall_m=0.10, y_off=0.0)
    offset = _detect(wall_m=0.10, y_off=0.05)
    assert centred is not None and offset is not None
    # 축거리는 좌우 어긋남과 무관해야 한다. 원시 range 였다면 5mm 넘게 커진다.
    assert offset.d == pytest.approx(centred.d, abs=0.004)


@pytest.mark.parametrize("tilt_deg", [0, 11, 17, 23])
def test_detect_notch_position_is_stable_under_wall_tilt(tilt_deg):
    """핵심 수정의 회귀 시험 — **벽 정렬 좌표계**로 `d`·`y` 를 재는지 검증한다.

    지금까지 모든 `detect()` 시험이 `yaw=0.0` 을 썼다. 그 각도에서는 벽 정렬 좌표계와
    라이다 원시 좌표계가 수치로 동일해서, 이 수정(`detect.py` 의 `wvec` 투영)이 되돌려도
    (라이다 프레임 x·y 를 직접 썼어도) 시험이 전혀 빨개지지 않았다. 라이다 y 를 썼다면
    `y` 가 `d·sin(기울기)` 만큼 어긋난다 — 실측 17° 에서 93mm."""
    obs = _detect(wall_m=0.30, y_off=0.0, yaw=math.radians(tilt_deg))
    assert obs is not None
    assert obs.y == pytest.approx(0.0, abs=0.003)
    assert obs.d == pytest.approx(0.325, abs=0.005)


def _near_scan(d=0.06, y_off=0.0, width=0.05):
    """`detect_near` 전용 합성 스캔 — `make_scan` 은 안 맞다.

    `make_scan` 은 벽을 무한 평면으로 모델링해서, 벽의 수직거리가 5cm 밑으로
    내려가도 광축에서 벗어난 광선은 사선 거리가 늘어나 여전히 유효하게 잡힌다
    (실측 확인함 — `wall_m=0.04` 로 만든 스캔이 `fit_wall` 에 여전히 걸린다).
    NEAR 국면의 실제 물리는 그게 아니다 — 노치를 뺀 나머지는 로봇 몸체/도크
    구조물이라 각도에 무관하게 5cm 안쪽이면 전부 안 보인다. 그래서 노치 창
    밖은 각도와 무관하게 전부 `inf` 로 두고, 창 안쪽만 평평한 바닥 반사로 채운다.
    """
    ranges = []
    for i in range(N_RAYS):
        a = ANGLE_MIN + i * ANGLE_INC
        c = math.cos(a)
        if c <= 1e-3:
            ranges.append(float("inf"))
            continue
        t = d / c
        s = t * math.sin(a)
        ranges.append(float(t) if abs(s - y_off) < width / 2 else float("inf"))
    return ranges


def _detect_near(cfg=None, **scan_kw):
    cfg = cfg or LidarDockConfig()
    return detect.detect_near(_near_scan(**scan_kw), ANGLE_MIN, ANGLE_INC,
                              RANGE_MIN, RANGE_MAX, cfg)


def test_detect_near_finds_a_centred_notch():
    """`fit_wall` 없이도 노치 섬(고립된 반사 군집)을 찾아야 한다 — 이게 이
    검출기가 존재하는 이유다. 벽이 사라진 자리에서 `d`·`y` 가 나와야 한다."""
    obs = _detect_near(d=0.06, y_off=0.0)
    assert obs is not None
    assert obs.near is True
    assert obs.d == pytest.approx(0.06, abs=0.005)
    assert obs.y == pytest.approx(0.0, abs=0.005)


def test_detect_near_measures_lateral_offset():
    obs = _detect_near(d=0.06, y_off=0.015)
    assert obs is not None
    assert obs.y == pytest.approx(0.015, abs=0.005)


def test_detect_near_returns_none_for_an_empty_scan():
    """반사가 하나도 없으면(전부 `inf`) 조용히 죽지 않고 `None` 이어야 한다 —
    NEAR 모드에는 `fit_wall` 같은 1차 방어가 없어서 이 하한선이 유일한 방어다."""
    cfg = LidarDockConfig()
    assert detect.detect_near([float("inf")] * N_RAYS, ANGLE_MIN, ANGLE_INC,
                              RANGE_MIN, RANGE_MAX, cfg) is None


def test_detect_near_rejects_a_cluster_that_is_too_wide():
    """직선 피팅이 없어 폭 게이트가 유일한 오검출 방어다. 실측: 이 폭에서
    `near_half_deg` 로 잘라내도 여전히 `near_width_max_m` 을 넘는다."""
    assert _detect_near(d=0.10, y_off=0.0, width=0.30) is None


def test_min_range_m_returns_the_closest_raw_reading_in_the_near_sector():
    """[2026-08-04, 사용자 요청] 노치 검출과 무관한 안전 정지가 이 값을 쓴다 —
    그룹핑·폭 필터 없이 그 창 안 원시 최소거리 그대로여야 한다."""
    cfg = LidarDockConfig()
    ranges = make_scan(wall_m=0.20, notch=False)
    mr = detect.min_range_m(ranges, ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    assert mr == pytest.approx(0.20, abs=0.01)


def test_min_range_m_is_not_fooled_by_a_deeper_notch():
    """노치는 벽보다 **더 멀다**(깊이만큼 파여 있다) — 최소값이 노치 쪽으로
    끌려가면 안 된다. 이게 흔들리면 안전 정지가 실제보다 늦게 걸린다."""
    cfg = LidarDockConfig()
    ranges = make_scan(wall_m=0.20, notch_d=0.03, notch=True)
    mr = detect.min_range_m(ranges, ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg)
    assert mr == pytest.approx(0.20, abs=0.01), "노치(더 먼 곳)에 안 속아야 한다"


def test_min_range_m_returns_none_with_no_valid_rays():
    cfg = LidarDockConfig()
    ranges = [float("inf")] * N_RAYS
    assert detect.min_range_m(ranges, ANGLE_MIN, ANGLE_INC, RANGE_MIN, RANGE_MAX, cfg) is None
