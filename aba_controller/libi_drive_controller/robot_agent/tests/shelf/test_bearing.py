"""픽셀 → 방위각. 카메라·ROS 없이 산수만 본다."""
import math

from app.shelf.bearing import bearing_rad, scale_k

# picam_640x480_rot180.npz 실측값
K640 = (609.15651744, 607.39537016, 278.17496904, 250.36175645)


def test_scale_is_identity_at_the_calibrated_width():
    assert scale_k(K640, 640, 640) == K640


def test_scale_halves_every_term_at_half_width():
    fx, fy, cx, cy = scale_k(K640, 640, 320)
    assert math.isclose(fx, 304.57825872)
    assert math.isclose(fy, 303.69768508)
    assert math.isclose(cx, 139.08748452)
    assert math.isclose(cy, 125.180878225)


def test_principal_point_is_zero_bearing():
    fx, _, cx, _ = scale_k(K640, 640, 320)
    assert bearing_rad(cx, fx, cx) == 0.0


def test_one_focal_length_off_axis_is_45_degrees():
    fx, _, cx, _ = scale_k(K640, 640, 320)
    assert math.isclose(bearing_rad(cx + fx, fx, cx), math.pi / 4)


def test_left_of_principal_point_is_negative():
    fx, _, cx, _ = scale_k(K640, 640, 320)
    assert bearing_rad(cx - 10.0, fx, cx) < 0.0


def test_scaled_and_unscaled_agree_on_the_same_physical_ray():
    """같은 방향의 광선이 640 과 320 에서 같은 각을 준다.

    ⚠️ 픽셀 좌표를 cx 로부터 만들면 안 된다 — u-cx 가 상쇄돼 cx 버그에 둔감해진다.
    """
    fx6, cx6 = K640[0], K640[2]
    fx3, _fy3, cx3, _cy3 = scale_k(K640, 640, 320)
    u6 = 378.17496904          # cx6 + 100 을 **계산이 아니라 값으로** 고정
    u3 = 189.08748452          # 그 광선이 320 프레임에서 오는 자리 = u6 / 2
    assert math.isclose(bearing_rad(u6, fx6, cx6), bearing_rad(u3, fx3, cx3))


def test_wrong_cx_scaling_changes_the_bearing():
    """cx 를 스케일 안 하면 각이 달라진다 — 이 시험이 그 회귀를 잡는다."""
    fx3, _fy, cx3, _cy = scale_k(K640, 640, 320)
    wrong = bearing_rad(200.0, fx3, K640[2])     # cx 를 640 값 그대로 쓴 경우
    right = bearing_rad(200.0, fx3, cx3)
    assert not math.isclose(wrong, right)
