"""알파베타 스무더의 `dt` 는 **수식에서 소거된다** — 실측값을 넣지 말 것.

## 왜 이 파일이 있나 (실측 2026-08-06)

"`--pose` 를 켜면 27fps → 17.7fps 로 내려가는데 스무더가 공칭 20fps 상수를 먹고
있으니 그래서 필터가 안 먹는 것"이라는 그럴듯한 가설이 있었다. 실제로 실측 dt 를
넘기도록 고쳤다가 **되돌렸다.** 재보니:

  · dt 를 0.01 과 0.50 으로 줘도(50배) 출력 차이가 2.8e-14 px — 완전히 소거된다
  · 프레임 간격이 흔들리면 실측 dt 쪽이 **더 나빴다**(간격 지터 30%에서 오차 +4.1%)
    — 지나간 간격은 다음 간격을 예측하지 못하는데 앞보기만 같이 흔들리기 때문

수학적으로 `velocity` 는 항상 `velocity * dt` 로만 등장하고
`(beta / dt) * dt = beta` 라 dt 가 상쇄된다. 이 시험이 그 성질을 못 박는다 —
누군가(또는 미래의 나) 같은 가설로 또 고치는 것을 막는다.

필터가 실제로 깎는 양은 검출 지터의 **43%**(27fps) / **30%**(17.7fps)다. 고장난
적이 없다. "안 붙는다"고 보였던 것은 `get_latest` 가 화면용 `bbox` 에는 스무딩
값을 안 쓰기 때문이다(`pipeline.py` 의 그 줄 주석).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from follower_perception.bbox_smoother import BBoxSmoother  # noqa: E402


def _outputs(dt, n=12):
    """같은 측정열을 주고 매 프레임 `predict(dt)` 를 모은다."""
    sm = BBoxSmoother()
    out = []
    for i in range(n):
        z = 100 + 10 * i + (3 if i % 2 else -3)      # 등속 + 지터
        sm.update(z, 200.0, 400.0, dt)
        out.append(sm.predict(dt)[0])
    return np.array(out)


def test_dt_는_출력에_영향이_없다():
    """update 와 predict 가 같은 dt 를 쓰는 한 dt 는 소거된다."""
    assert _outputs(0.01) == pytest.approx(_outputs(0.50), abs=1e-9)


def test_그래도_지터는_실제로_깎인다():
    """소거된다고 필터가 아무 일도 안 하는 건 아니다 — 감쇠는 진짜다."""
    rng = np.random.default_rng(20260806)
    sm = BBoxSmoother()
    truth = lambda k: 100.0 + 2.0 * k                        # noqa: E731
    meas_err, out_err = [], []
    for k in range(120):
        z = truth(k) + rng.normal(0, 4.0)
        sm.update(z, 200.0, 400.0, 0.05)
        if k > 5:
            meas_err.append(z - truth(k))
            out_err.append(sm.predict(0.0)[0] - truth(k))     # 앞보기 빼고 평활만
    assert np.std(out_err) < np.std(meas_err) * 0.75, \
        "지터를 25% 넘게 깎아야 한다 (실측 43%)"


def test_필터를_켜면_그려지는_골격도_걸러진다():
    """`last_keypoints` 는 `_keypoints()` 가 필터 **전** 값으로 채운다. 안 덮으면
    판정만 걸러지고 화면은 원본이라 "필터가 안 먹는다"로 보인다."""
    pose_estimator = pytest.importorskip(
        "follower_perception.pose_estimator",
        reason="ultralytics 없는 환경에서는 건너뛴다")

    class _KP:
        def __init__(self, xy, conf):
            self.xy = [np.asarray(xy, dtype=float)]
            self.conf = [np.asarray(conf, dtype=float)]

    class _Res:
        def __init__(self, kp): self.keypoints = kp

    jitter = [0.0, 6.0, -6.0, 6.0, -6.0, 6.0]     # 제자리 떨림
    base = np.tile(np.array([50.0, 60.0]), (17, 1))
    calls = {"i": 0}

    def _model(crop, verbose=False):
        xy = base.copy()
        xy[:, 0] += jitter[min(calls["i"], len(jitter) - 1)]
        calls["i"] += 1
        return [_Res(_KP(xy, np.full(17, 0.9)))]

    est = pose_estimator.PoseEstimator(model=_model, every_n=1)
    est._filter = pose_estimator.KeypointFilter(min_cutoff=0.05, beta=0.0,
                                                conf_min=est.conf_min)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    for _ in range(len(jitter)):
        est.classify(frame, (10, 10, 120, 180))

    drawn_x = est.last_keypoints[0][0][0]
    raw_x = base[0][0] + jitter[len(jitter) - 1]
    assert drawn_x != pytest.approx(raw_x), \
        "화면 좌표가 원본 그대로다 — 필터가 판정에만 걸렸다는 뜻"
    assert abs(drawn_x - base[0][0]) < abs(raw_x - base[0][0])
