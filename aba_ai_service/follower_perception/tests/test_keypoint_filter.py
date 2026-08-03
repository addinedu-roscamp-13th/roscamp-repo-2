"""키포인트 지터 억제 — One-Euro 필터.

정지 구간의 떨림만 깎고 실제 움직임은 안 늦춘다. 15fps 에서 한 프레임이
66.7ms 라 고정 지연(EMA)은 비싸다.
"""
import numpy as np

from follower_perception.keypoint_filter import KeypointFilter, OneEuro

DT = 1.0 / 15.0


def test_constant_input_converges():
    f = OneEuro(min_cutoff=1.0)
    out = [f.filter(10.0, DT) for _ in range(30)]
    assert out[0] == 10.0, "첫 표본은 그대로 통과한다 — 비교할 과거가 없다"
    assert abs(out[-1] - 10.0) < 1e-6


def test_noise_is_damped_when_still():
    """정지 구간: 출력 분산이 입력 분산보다 확실히 작아야 한다."""
    rng = np.random.default_rng(0)
    noisy = 100.0 + rng.normal(0.0, 3.0, 200)
    f = OneEuro(min_cutoff=0.5, beta=0.0)
    out = np.array([f.filter(float(v), DT) for v in noisy])
    assert out[50:].std() < noisy[50:].std() * 0.5


def test_fast_motion_is_not_lagged():
    """빠르게 움직이면 beta 항이 차단을 풀어 따라붙는다."""
    ramp = [float(i * 20) for i in range(40)]        # 프레임당 20px
    slow = OneEuro(min_cutoff=0.5, beta=0.0)
    fast = OneEuro(min_cutoff=0.5, beta=0.05)
    for v in ramp:
        s = slow.filter(v, DT)
        q = fast.filter(v, DT)
    assert abs(q - ramp[-1]) < abs(s - ramp[-1])


def test_reset_clears_state():
    f = OneEuro(min_cutoff=0.5)
    for _ in range(20):
        f.filter(0.0, DT)
    f.reset()
    assert f.filter(500.0, DT) == 500.0


def test_low_confidence_point_passes_through_unfiltered():
    """신뢰도 미달 점은 필터에 안 넣는다 — 난수 좌표가 상태를 오염시킨다."""
    kf = KeypointFilter(min_cutoff=0.5, conf_min=0.5)
    xy = np.zeros((17, 2)); conf = np.full(17, 0.9)
    for _ in range(20):
        kf.apply(xy, conf, DT)
    xy_bad = np.full((17, 2), 999.0); conf_bad = np.full(17, 0.1)
    out = kf.apply(xy_bad, conf_bad, DT)
    assert np.allclose(out, 999.0), "미달 점은 원본 그대로 나온다"


def test_state_resets_after_a_dropout():
    """미달 구간을 지난 뒤 되살아난 점은 옛 상태를 안 끌고 온다."""
    kf = KeypointFilter(min_cutoff=0.5, conf_min=0.5)
    good = np.full(17, 0.9); bad = np.full(17, 0.1)
    for _ in range(20):
        kf.apply(np.zeros((17, 2)), good, DT)
    kf.apply(np.full((17, 2), 999.0), bad, DT)          # 드롭아웃
    out = kf.apply(np.full((17, 2), 300.0), good, DT)   # 복귀
    assert np.allclose(out, 300.0), "복귀 프레임은 그대로 통과해야 한다"


def test_shape_is_preserved():
    kf = KeypointFilter()
    out = kf.apply(np.zeros((17, 2)), np.full(17, 0.9), DT)
    assert out.shape == (17, 2)
