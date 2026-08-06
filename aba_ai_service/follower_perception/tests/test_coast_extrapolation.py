"""코스팅은 알파베타 속도로 **위치만** 민다. 그리고 캘리브가 영영 안 끝나면 포기한다.

## 두 가지가 한 파일에 있는 이유

증상이 하나였다 — "주황 박스가 잘 안 보이고, pose 를 켜면 더 안 보인다".
원인이 둘로 갈렸다:

  1. 보여도 안 움직였다 — 2026-08-02 에 외삽을 끄면서 마지막 박스를 얼렸다
  2. 아예 안 보였다 — `Calibrating` 이 `_NO_COAST_POSTURES` 라 코스팅이 통째로
     막히는데, 골격이 잘 안 잡히면 그 상태가 **영영 안 끝난다**

## 외삽을 되돌리되 그때 결함 두 개는 막는다

  ① 면적은 안 민다 (실측: 3프레임 만에 area 0 → 거리 PID 가 전속 전진)
  ② 밀어낼 거리에 상한 (실측: 가려지기 직전 튐이 속도로 학습돼 박스가 날아감)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from follower_perception.bbox_smoother import BBoxSmoother  # noqa: E402
from follower_perception.constants import COAST_MAX_DRIFT_W, FRAME_DT  # noqa: E402


class _Owner:
    def __init__(self, cx, cy, area, w=40, h=80):
        self.cx, self.cy, self.area = cx, cy, area
        self.bbox = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        self.track_id, self.confidence = 1, 0.9


def _pipeline_in_coast(vx=0.0, vy=0.0, miss=1, posture=None, owner=None):
    """`_miss > 0` 상태의 파이프라인을 손으로 세운다 — 진짜 모델을 안 연다."""
    from follower_perception.pipeline import FollowerPerception
    from follower_perception.exit_direction import CENTER

    p = FollowerPerception.__new__(FollowerPerception)
    p.smoother = BBoxSmoother()
    p._last_owner = owner if owner is not None else _Owner(300.0, 200.0, 1600.0)
    p.smoother.state = np.array([p._last_owner.cx, p._last_owner.cy,
                                 p._last_owner.area], dtype=float)
    p.smoother.velocity = np.array([vx, vy, -9000.0], dtype=float)  # 면적은 폭락 중
    p._miss = miss
    p._exit_dir = CENTER
    p._last_posture = posture
    p.posture_gate = type("G", (), {"allowed": True})()
    p.camera, p.camera_epoch = None, 0
    return p


def test_코스팅이_알파베타_속도로_위치를_민다():
    """이 파일의 존재 이유 — 가려진 동안 가던 쪽으로 조금 더 따라가야 한다."""
    still = _pipeline_in_coast(vx=0.0, miss=6).get_latest()
    moving = _pipeline_in_coast(vx=120.0, miss=6).get_latest()
    assert moving.cx > still.cx, "속도가 있는데 안 밀면 외삽이 아니다"
    assert moving.is_predicted is True


def test_화면_박스도_같이_밀린다():
    """cx 만 밀고 bbox 를 얼리면 **사람 눈에는 아무 변화가 없다** — 예전 증상."""
    det = _pipeline_in_coast(vx=120.0, miss=6).get_latest()
    assert det.bbox[0] > _Owner(300.0, 200.0, 1600.0).bbox[0]
    # 폭은 그대로여야 한다 — 미는 것이지 늘리는 것이 아니다
    assert det.bbox[2] - det.bbox[0] == pytest.approx(40.0)


def test_면적은_절대_외삽하지_않는다():
    """실측 2026-08-02: 면적을 밀면 3프레임 만에 0 이 되고 거리 PID 가 전속 전진한다."""
    for miss in (1, 6, 12, 24):
        det = _pipeline_in_coast(vx=60.0, miss=miss).get_latest()
        assert det.area == pytest.approx(1600.0), \
            f"miss={miss} 에서 면적이 변했다 — √area 가 거리로 읽히는 값이다"


def test_밀어낼_거리에_상한이_있다():
    """가려지기 직전 튐이 속도로 학습돼 박스가 날아가는 것을 막는다."""
    owner = _Owner(300.0, 200.0, 1600.0, w=40)
    det = _pipeline_in_coast(vx=99999.0, miss=24, owner=owner).get_latest()
    drift = abs(det.cx - owner.cx)
    assert drift <= COAST_MAX_DRIFT_W * 40 + 1e-6, "상한을 넘어 밀었다"
    assert drift > 0, "상한이 있다고 아예 안 밀면 안 된다"


def test_대각선도_방향을_유지하며_잘린다():
    """길이만 자르고 방향은 살려야 한다 — x 만 자르면 엉뚱한 쪽을 가리킨다."""
    owner = _Owner(300.0, 200.0, 1600.0, w=40)
    det = _pipeline_in_coast(vx=9000.0, vy=9000.0, miss=24, owner=owner).get_latest()
    dx, dy = det.cx - owner.cx, det.cy - owner.cy
    assert dx == pytest.approx(dy, rel=1e-6), "45도로 나갔는데 방향이 틀어졌다"


def test_lying_은_여전히_코스팅을_막는다():
    """쓰러지는 중이던 대상을 예측 위치로 쫓아가면 안 된다 — 완화하지 않았다."""
    assert _pipeline_in_coast(vx=60.0, miss=6, posture="Lying").get_latest() is None


# ── 캘리브 제한 시간 ─────────────────────────────────────────────────────────

def test_캘리브가_안_끝나면_포기하고_Unknown_으로_넘어간다():
    """`Calibrating` 이 영영 안 끝나면 코스팅이 통째로 막힌다 — 주황 박스가 안 나온다."""
    pose_estimator = pytest.importorskip(
        "follower_perception.pose_estimator",
        reason="ultralytics 없는 환경에서는 건너뛴다")

    class _KP:
        def __init__(self, xy, conf):
            self.xy = [np.asarray(xy, dtype=float)]
            self.conf = [np.asarray(conf, dtype=float)]

    class _Res:
        def __init__(self, kp): self.keypoints = kp

    # 키포인트는 나오는데 **신뢰도가 미달**이다 — 표본에 한 장도 안 들어간다.
    # (아예 안 나오는 경우는 `_guard_only` 가 Unknown 을 내므로 원래 안 막힌다.)
    def _model(crop, verbose=False):
        xy = np.tile(np.array([50.0, 60.0]), (17, 1))
        return [_Res(_KP(xy, np.full(17, 0.1)))]

    clock = {"t": 1000.0}
    est = pose_estimator.PoseEstimator(model=_model, every_n=1)
    pose_estimator.time = type("T", (), {"monotonic": staticmethod(lambda: clock["t"])})
    try:
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        assert est.classify(frame, (10, 10, 120, 180)) == "Calibrating"
        clock["t"] += 2.0
        assert est.classify(frame, (10, 10, 120, 180)) == "Calibrating"
        clock["t"] += 10.0                       # 제한 시간을 넘긴다
        got = est.classify(frame, (10, 10, 120, 180))
    finally:
        import time as _real_time
        pose_estimator.time = _real_time

    assert got != "Calibrating", "영영 Calibrating 이면 코스팅이 영영 막힌다"
    assert est.calibrating is False, "포기했으면 카운트다운도 멈춰야 한다"

    from follower_perception.exit_direction import may_coast, CENTER
    assert may_coast(CENTER, got) is True, "포기한 뒤에는 주황 박스가 돌아와야 한다"
