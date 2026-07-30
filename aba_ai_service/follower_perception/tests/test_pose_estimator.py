"""자세 2차 추론. YOLO 가중치 없이 대역 모델로 검증한다.

`posture` 모듈 자체는 진짜를 쓴다 — 그 임계값과 계약이 맞는지 확인하는 게 목적이라
거기까지 대역으로 바꾸면 시험할 게 남지 않는다.
"""
import numpy as np
import pytest

from follower_perception.pose_estimator import PoseEstimator

# COCO 17 키포인트 인덱스: 5/6 어깨, 11/12 골반
L_SH, R_SH, L_HIP, R_HIP = 5, 6, 11, 12


def _keypoints(shoulder_y=100.0, hip_y=300.0, width=100.0):
    """서 있는 사람 모양. hip_y 를 어깨에 붙이면 몸통이 짧아져 누운 것으로 보인다."""
    xy = np.zeros((17, 2), dtype=float)
    cx = 200.0
    xy[L_SH] = (cx - width / 2, shoulder_y)
    xy[R_SH] = (cx + width / 2, shoulder_y)
    xy[L_HIP] = (cx - width / 2, hip_y)
    xy[R_HIP] = (cx + width / 2, hip_y)
    return xy


class _KP:
    def __init__(self, xy, conf):
        self.xy = [xy]
        self.conf = [conf]


class _Res:
    def __init__(self, kp):
        self.keypoints = kp


class FakeModel:
    """(xy, conf) 시퀀스를 순서대로 돌려주는 대역. 마지막 값을 계속 반복한다."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, crop, verbose=False):
        item = self.seq[min(self.calls, len(self.seq) - 1)]
        self.calls += 1
        if item is None:
            return []
        xy, conf = item
        return [_Res(_KP(xy, conf))]


def _frame():
    return np.zeros((480, 640, 3), dtype=np.uint8)


BBOX = (100, 50, 300, 400)
GOOD_CONF = np.ones(17, dtype=float)


def _standing(n=1):
    return [(_keypoints(), GOOD_CONF)] * n


def test_calibrating_until_reference_is_fixed():
    """기준을 재는 동안에는 판정하지 않는다."""
    est = PoseEstimator(model=FakeModel(_standing(200)))
    assert est.classify(_frame(), BBOX) == "Calibrating"
    assert est.calibrating is True


def test_standing_after_calibration():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    frames, needed = est.calibration_progress
    for _ in range(needed + 1):
        state = est.classify(_frame(), BBOX)
    assert est.calibrating is False
    assert state == "Standing"


def test_lying_detected_after_calibration():
    """서 있는 자세로 기준을 잡은 뒤 몸통이 짧아지면 Lying 이 나와야 한다."""
    seq = _standing(60) + [(_keypoints(shoulder_y=100.0, hip_y=120.0), GOOD_CONF)] * 5
    est = PoseEstimator(model=FakeModel(seq))
    _, needed = est.calibration_progress
    for _ in range(needed + 3):
        state = est.classify(_frame(), BBOX)
    assert state == "Lying"


def test_low_confidence_is_unknown():
    low = np.zeros(17, dtype=float)
    seq = _standing(60) + [(_keypoints(), low)] * 3
    est = PoseEstimator(model=FakeModel(seq))
    _, needed = est.calibration_progress
    for _ in range(needed + 2):
        state = est.classify(_frame(), BBOX)
    assert state == "Unknown"


def test_no_detection_is_unknown():
    est = PoseEstimator(model=FakeModel([None]))
    assert est.classify(_frame(), BBOX) == "Unknown"


def test_every_n_skips_inference_and_holds_last():
    """주기를 낮추면 추론 횟수가 줄고 직전 판정이 유지된다."""
    model = FakeModel(_standing(200))
    est = PoseEstimator(model=model, every_n=3)
    for _ in range(6):
        est.classify(_frame(), BBOX)
    assert model.calls == 2


def test_recalibrate_starts_over():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    _, needed = est.calibration_progress
    for _ in range(needed + 1):
        est.classify(_frame(), BBOX)
    assert est.calibrating is False
    est.recalibrate()
    assert est.calibrating is True
    assert est.calibration_progress[0] == 0


def test_degenerate_bbox_is_unknown():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    assert est.classify(_frame(), (10, 10, 11, 11)) == "Unknown"


def test_bbox_outside_frame_is_clamped_not_crashed():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    assert est.classify(_frame(), (-50, -50, 5000, 5000)) in ("Calibrating", "Unknown")


def test_none_frame_is_unknown():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    assert est.classify(None, BBOX) == "Unknown"


def test_model_exception_does_not_propagate():
    """추론 실패가 20Hz 추종 루프를 죽이면 안 된다."""
    class Boom:
        def __call__(self, crop, verbose=False):
            raise RuntimeError("CUDA 어쩌고")

    est = PoseEstimator(model=Boom())
    assert est.classify(_frame(), BBOX) == "Unknown"


def test_missing_yolo_pose_dir_raises_clearly():
    import os
    os.environ["LIBI_YOLO_POSE_DIR"] = "/nonexistent/yolo_pose"
    try:
        import follower_perception.pose_estimator as pe
        pe._posture_module = None                 # 캐시 비우고 다시 시도
        with pytest.raises(RuntimeError, match="yolo_pose"):
            pe.load_posture_module()
    finally:
        del os.environ["LIBI_YOLO_POSE_DIR"]
        pe._posture_module = None
        pe.load_posture_module()                  # 다른 테스트를 위해 되돌린다


# ── 화면에 그릴 키포인트 보관 ──────────────────────────────────────────────────
#
# 판정에는 안 쓴다. 순전히 서버가 스켈레톤을 그리기 위한 것이다(test_pose_overlay).
# 안 채워지면 **에러 없이 골격만 안 보인다** — 그래서 여기서 붙들어 둔다.

def test_keypoints_are_kept_for_drawing():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    est.classify(_frame(), BBOX)
    xy, conf, origin = est.last_keypoints
    assert origin == (BBOX[0], BBOX[1]), "bbox 원점을 같이 보관해야 화면 좌표로 옮긴다"
    assert len(xy) == 17 and len(conf) == 17


def test_keypoints_are_cleared_when_inference_fails():
    """직전 골격이 남으면 사람이 사라진 자리에 유령이 그려진다."""
    est = PoseEstimator(model=FakeModel(_standing(60) + [None]))
    for _ in range(60):
        est.classify(_frame(), BBOX)
    assert est.last_keypoints is not None, "전제: 한 번은 잡혔다"
    for _ in range(est.every_n):
        est.classify(_frame(), BBOX)
    assert est.last_keypoints is None


def test_skipped_frames_keep_the_last_keypoints():
    """every_n 주기로 지우면 골격이 깜빡인다 — 추론한 프레임에서만 갱신한다."""
    est = PoseEstimator(model=FakeModel(_standing(200)), every_n=3)
    for _ in range(3):
        est.classify(_frame(), BBOX)
    kept = est.last_keypoints
    assert kept is not None
    est.classify(_frame(), BBOX)              # 건너뛰는 프레임
    assert est.last_keypoints is kept
