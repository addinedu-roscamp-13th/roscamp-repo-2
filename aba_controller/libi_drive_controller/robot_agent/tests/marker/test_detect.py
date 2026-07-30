"""검출·자세추정 테스트 — 합성 마커 이미지로 카메라 없이 검증한다.

정면 렌더만으로는 부족하다(평면 pose 는 해가 둘이라 정면에서는 둘이 같아진다).
그래서 카메라를 옆으로 옮긴 **사시 시점**을 실제로 투영해 렌더하고, 거기서
거리·부호·조향 항의 일관성까지 본다.
"""
import cv2
import numpy as np
import pytest

from app.marker.detect import (detect_marker, make_marker_image, marker_object_points,
                           scan_dicts)

W, H = 640, 480
FX = FY = 609.2
K = np.array([[FX, 0, W / 2], [0, FY, H / 2], [0, 0, 1]], dtype=np.float64)
DIST = np.zeros(5, dtype=np.float64)
LEN_M = 0.07
DICT = "DICT_5X5_100"


def render_frontal(marker_id=1, z_m=1.0, offset_px=0, dict_name=DICT):
    """카메라 정면 z_m 거리에 마커가 있는 것처럼 만든 합성 프레임."""
    side = int(round(LEN_M * FX / z_m))
    img = make_marker_image(dict_name, marker_id, side)
    frame = np.full((H, W), 255, dtype=np.uint8)
    x0 = (W - side) // 2 + offset_px
    y0 = (H - side) // 2
    frame[y0:y0 + side, x0:x0 + side] = img
    return np.dstack([frame] * 3)


def render_oblique(lateral_m, dist_m=0.8, marker_id=1, dict_name=DICT, side=300, pad=40):
    """카메라를 마커 법선축에서 lateral_m 만큼 옆으로 옮기고 마커를 바라보게 한 시점.

    반환: (프레임, 마커 좌표계에서의 카메라 위치 C)
    """
    img = make_marker_image(dict_name, marker_id, side)
    canvas = np.full((side + 2 * pad, side + 2 * pad), 255, dtype=np.uint8)
    canvas[pad:pad + side, pad:pad + side] = img
    src = np.array([[pad, pad], [pad + side, pad],
                    [pad + side, pad + side], [pad, pad + side]], dtype=np.float32)

    C = np.array([lateral_m, 0.0, dist_m])          # 마커 좌표계에서의 카메라 위치
    z_axis = -C / np.linalg.norm(C)                 # 카메라 +Z 가 마커 원점을 향한다
    y_axis = np.array([0.0, -1.0, 0.0])             # 영상 y 아래 = 마커 -Y
    x_axis = np.cross(y_axis, z_axis)
    x_axis /= np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)
    R = np.stack([x_axis, y_axis, z_axis])          # 마커→카메라 회전
    t = -R @ C
    rvec, _ = cv2.Rodrigues(R)
    dst, _ = cv2.projectPoints(marker_object_points(LEN_M), rvec, t, K, DIST)
    warp = cv2.getPerspectiveTransform(src, dst.reshape(4, 2).astype(np.float32))
    out = cv2.warpPerspective(canvas, warp, (W, H),
                              borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return np.dstack([out] * 3), C


def observe(frame, dict_name=DICT, target_id=1):
    return detect_marker(frame, K, DIST, marker_len_m=LEN_M,
                         target_id=target_id, dict_name=dict_name)


# ---------------------------------------------------------------- 기본

def test_none_when_blank():
    assert observe(np.full((H, W, 3), 255, dtype=np.uint8)) is None


def test_none_for_other_id():
    assert observe(render_frontal(marker_id=7)) is None


@pytest.mark.parametrize("z", [0.5, 1.0, 2.0])
def test_distance_within_five_percent(z):
    o = observe(render_frontal(z_m=z))
    assert o is not None
    assert abs(o.z_m - z) / z < 0.05


def test_ex_sign_follows_offset():
    right = observe(render_frontal(offset_px=+80))
    left = observe(render_frontal(offset_px=-80))
    assert right.ex > 0.1
    assert left.ex < -0.1


def test_centered_marker_is_square_on():
    o = observe(render_frontal())
    assert abs(o.yaw_deg) < 5.0
    assert abs(o.lateral_m) < 0.02


def test_wrong_dictionary_finds_nothing():
    """5X5_100 마커를 4X4_50 으로 찾으면 검출 0개 — 조용한 실패의 정체."""
    assert observe(render_frontal(), dict_name="DICT_4X4_50") is None


def test_scan_dicts_identifies_the_right_dictionary():
    hits = dict(scan_dicts(render_frontal()))
    assert DICT in hits
    assert 1 in hits[DICT]


def test_scan_dicts_empty_on_blank_frame():
    assert scan_dicts(np.full((H, W, 3), 255, dtype=np.uint8)) == []


def test_unknown_dictionary_raises():
    with pytest.raises(ValueError):
        observe(render_frontal(), dict_name="DICT_9X9_9")


# ---------------------------------------------------------------- 사시 시점

@pytest.mark.parametrize("dx", [-0.15, 0.15])
def test_oblique_lateral_sign_and_magnitude(dx):
    """축에서 벗어난 시점에서 lateral_m 의 부호가 실제 이탈 방향과 같아야 한다.

    부호가 뒤집히면 조향이 이탈을 키우는 방향으로 나간다.
    """
    frame, C = render_oblique(dx)
    o = observe(frame)
    assert o is not None
    assert np.sign(o.lateral_m) == np.sign(dx)
    assert abs(o.lateral_m - dx) < 0.4 * abs(dx)      # 워프 렌더 오차 감안
    assert abs(o.z_m - np.linalg.norm(C)) < 0.03


def test_oblique_yaw_and_lateral_agree_in_sign():
    """사시 자세에서 yaw 와 lateral 은 **같은 부호**로 나온다.

    이게 축 정렬 제어식이 `k_lat*lateral - k_yaw*(yaw/90)` 인 이유다(더하기 아님).
    같은 부호라서 빼야 헤딩 항이 교차오차 항을 상쇄하고 평형이 잡힌다.
    더하면 회전이 자기를 부추겨 마커를 시야 밖으로 날린다(폐루프 시뮬레이션에서 확인).
    """
    for dx in (-0.15, 0.15):
        o = observe(render_oblique(dx)[0])
        assert o is not None
        assert o.yaw_deg * o.lateral_m > 0, f"dx={dx}: yaw={o.yaw_deg} lat={o.lateral_m}"


def test_oblique_still_detected_at_working_distance():
    """10cm 목표 근처(12~15cm)에서도 검출돼야 무시각 구간이 2~3cm 로 유지된다."""
    o = observe(render_oblique(0.02, dist_m=0.13)[0])
    assert o is not None
    assert 0.11 < o.z_m < 0.16


# ------------------------------------------------- codex 적대적 리뷰 회귀 테스트

def test_pose_behind_camera_is_rejected(monkeypatch):
    """카메라 뒤쪽 해(z<0)를 그대로 내보내면 정지 조건을 즉시 만족시켜 버린다."""
    import app.marker.detect as det

    def fake_solve(obj, pts, K, dist, flags=None):
        return True, np.zeros((3, 1)), np.array([[0.0], [0.0], [-0.2]])

    monkeypatch.setattr(det.cv2, "solvePnP", fake_solve)
    assert observe(render_frontal()) is None


def test_pose_with_large_reprojection_error_is_rejected(monkeypatch):
    """코너와 안 맞는 자세 해는 못 믿는다 — 거리·각도가 통째로 거짓이 된다."""
    import app.marker.detect as det

    def fake_solve(obj, pts, K, dist, flags=None):
        # 실제 위치와 전혀 다른 곳(옆으로 1m)에 있다고 우기는 해
        return True, np.zeros((3, 1)), np.array([[1.0], [0.0], [1.0]])

    monkeypatch.setattr(det.cv2, "solvePnP", fake_solve)
    assert observe(render_frontal()) is None


def test_good_pose_survives_the_reprojection_gate():
    """게이트가 정상 검출까지 버리면 안 된다(문지방이 너무 낮은지 확인)."""
    o = observe(render_frontal(z_m=1.0))
    assert o is not None and abs(o.z_m - 1.0) < 0.05
    o2 = observe(render_oblique(0.15)[0])
    assert o2 is not None
