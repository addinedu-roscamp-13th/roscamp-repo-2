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


def test_no_detection_without_reference_is_unknown():
    """검출이 없어서가 아니라 **기준이 없어서** Unknown 이다.

    키포인트가 하나도 없어도 bbox 가드는 물어본다(아래 zero-keypoint 테스트들
    참고). 다만 이 테스트는 캘리브를 안 주입한 새 추정기라 `ref_bbox_hw` 가
    아직 없고, 가드는 비교할 기준이 없으면 판단하지 않는다 — 그래서 Unknown.
    이름이 옛날처럼 "no_detection" 만 남으면 "검출이 없으면 무조건 Unknown"
    으로 오해하기 쉽다.
    """
    est = PoseEstimator(model=FakeModel([None]))
    assert est._ref_bbox_hw is None
    assert est.classify(_frame(), BBOX) == "Unknown"
    assert est.calibration_progress[0] == 0


def test_zero_keypoints_with_seeded_ref_and_flat_bbox_is_lying():
    """PRD Story 41/42 회귀 — 이 테스트가 없으면 가드가 죽은 채로 통과한다.

    포즈 모델이 크롭에서 키포인트를 하나도 못 낸 프레임(뒷캠에서 가장 흔한
    실패 조건)이다. 예전에는 여기서 곧장 Unknown 을 내보내 bbox 가드가
    호출조차 안 됐다 — bbox 는 이미 계산해 놓고 버린 것이다. 이 조건에서도
    누움/측면을 판정해야 로봇이 선다(PRD Story 41/42).
    """
    est = PoseEstimator(model=FakeModel([None]))
    est._ref_bbox_hw = 1.75                     # 등록 시 쟀다고 가정
    flat_bbox = (100, 50, 300, 100)              # 폭 200, 높이 50 — 가로로 누움
    assert est.classify(_frame(), flat_bbox) == "Lying"
    assert est.calibration_progress[0] == 0      # 계산기(축 기준)는 안 건드렸다
    assert est.last_axis == "torso"              # 기본값 그대로 — 잴 키포인트가 없었다


def test_zero_keypoints_with_seeded_ref_and_clipped_bbox_is_unknown():
    """가드는 잘린 bbox 를 안 믿는다 — 높이·폭이 진짜가 아니다(설계상 동작).

    flat_bbox 와 같은 종횡비(폭 300, 높이 50)라 잘리지만 않았으면 Lying 이
    나왔을 값인데, 왼쪽 경계에 붙어 있어 가드가 판단을 보류한다.
    """
    est = PoseEstimator(model=FakeModel([None]))
    est._ref_bbox_hw = 1.75
    edge_bbox = (0, 50, 300, 100)                # 왼쪽 경계(x1=0)에 붙음
    assert est.classify(_frame(), edge_bbox) == "Unknown"
    assert est.calibration_progress[0] == 0


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


def test_recalibrate_reapplies_the_ref_bbox_hw_seed():
    """등록마다 recalibrate() 가 다시 불린다(pipeline.py) — 시드가 첫 등록에서만
    먹으면 두 번째 등록부터 조용히 예전 측정-대기 경로로 돌아간다."""
    calib = _calib_with(ref_bbox_hw=1.8)
    est = PoseEstimator(model=FakeModel(_standing(200)), calib=calib)
    _, needed = est.calibration_progress
    for _ in range(needed + 1):
        est.classify(_frame(), BBOX)
    assert est.calibrating is False
    assert est._ref_bbox_hw == 1.8            # 파일 값 그대로 — 축 기준(ref_ratios)과는 별개다
    assert est._bbox_hw_samples == []          # 시드가 있으니 런타임 표본을 아예 안 모았다

    est.recalibrate()                          # 재등록

    assert est.calibrating is True             # ref_ratios 시드는 없으니 축 기준은 여전히 측정 대기
    assert est.calibration_progress[0] == 0
    assert est._ref_bbox_hw == 1.8             # ⚠️ 여기서 사라지면 두 번째 등록부터 회귀
    assert est._bbox_hw_samples == []


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


# ── bbox 종횡비 가드 배선 (Task 8b) ──────────────────────────────────────────
#
# posture.classify_posture() 는 bbox_wh/ref_bbox_hw/bbox_clipped 를 안 받으면
# 가드가 조용히 죽어 있는 코드가 된다. 여기서부터는 그 배선을 검증한다.

def test_bbox_size_is_passed_to_posture():
    """종횡비 항이 돌려면 bbox 를 넘겨야 한다. 안 넘기면 조용히 키포인트 폴백이 된다."""
    import follower_perception.pose_estimator as pe
    seen = {}
    real = pe.load_posture_module().classify_posture

    def spy(xy, conf, **kw):
        seen.update(kw)
        return real(xy, conf, **{k: v for k, v in kw.items()})

    est = PoseEstimator(model=FakeModel(_standing(200)))
    est._posture.classify_posture = spy
    try:
        _, needed = est.calibration_progress
        for _ in range(needed + 2):
            est.classify(_frame(), BBOX)
    finally:
        est._posture.classify_posture = real
    assert seen.get("bbox_wh") == (BBOX[2] - BBOX[0], BBOX[3] - BBOX[1])


def test_calibration_remaining_uses_measured_fps():
    """공칭 fps 로 나누면 프레임이 밀릴 때 카운트다운이 멈춘 것처럼 보인다."""
    est = PoseEstimator(model=FakeModel(_standing(200)))
    for _ in range(30):
        est.classify(_frame(), BBOX)
    assert est.calibration_remaining_sec(15.0) == pytest.approx((60 - 30) / 15.0)
    assert est.calibration_remaining_sec(7.5) == pytest.approx((60 - 30) / 7.5)


def test_calibration_remaining_is_zero_when_done():
    est = PoseEstimator(model=FakeModel(_standing(200)))
    _, needed = est.calibration_progress
    for _ in range(needed + 1):
        est.classify(_frame(), BBOX)
    assert est.calibration_remaining_sec(15.0) == 0.0


def test_calib_file_seeds_the_reference_ratio(tmp_path):
    """캘리브 파일이 있으면 등록 직후 멈춰 있는 구간이 사라진다."""
    import json
    from follower_perception.pose_calib import load_pose_calib
    p = tmp_path / "pose_calib.json"
    p.write_text(json.dumps({"ref_ratios": {"torso": 2.1}, "side_factor": 1.6}))
    est = PoseEstimator(model=FakeModel(_standing(200)), calib=load_pose_calib(str(p)))
    assert est.classify(_frame(), BBOX) == "Standing"
    assert est.calibrating is False


def test_no_calib_file_keeps_the_measuring_window():
    """무설정 회귀 — 파일이 없으면 예전처럼 60프레임을 잰다."""
    est = PoseEstimator(model=FakeModel(_standing(200)))
    assert est.classify(_frame(), BBOX) == "Calibrating"


def test_bbox_clipped_true_at_edge_false_in_middle():
    """잘린 bbox 는 높이·폭이 진짜가 아니다 — 가드가 그걸 기준으로 믿으면 안 된다."""
    import follower_perception.pose_estimator as pe
    seen = {}
    real = pe.load_posture_module().classify_posture

    def spy(xy, conf, **kw):
        seen.update(kw)
        return real(xy, conf, **kw)

    est = PoseEstimator(model=FakeModel(_standing(200)))
    est._posture.classify_posture = spy
    try:
        _, needed = est.calibration_progress
        for _ in range(needed + 1):
            est.classify(_frame(), BBOX)             # 중앙 bbox — 경계에서 안 떨어져 있다
        assert seen.get("bbox_clipped") is False

        est.classify(_frame(), (0, 50, 300, 400))    # 왼쪽 경계에 붙음
        assert seen.get("bbox_clipped") is True
    finally:
        est._posture.classify_posture = real


def test_calibrator_ignores_low_confidence_torso_frames():
    """토르소 신뢰도 미달 프레임은 표본에 안 들어간다.

    예전에는 검사가 없어서 난수 좌표가 기준 중앙값에 섞였다. 측정 구간에 옆으로
    선 프레임이 과반이면 기준이 부풀고, 그만큼 측면 임계도 같이 커져 이후의 진짜
    측면을 못 잡는다.
    """
    low = np.zeros(17, dtype=float)
    est = PoseEstimator(model=FakeModel([(_keypoints(), low)] * 10))
    for _ in range(10):
        est.classify(_frame(), BBOX)
    assert est.calibration_progress[0] == 0


def test_ref_bbox_hw_stays_none_with_too_few_valid_samples():
    """표본이 최소 개수 미만이면 가드를 아예 끈다.

    근거 없는 기준으로 판정하는 것보다 그 신호를 안 쓰는 편이 낫다.
    """
    est = PoseEstimator(model=FakeModel(_standing(1)))
    est._bbox_hw_samples = [1.9, None, None, 2.0]   # CALIBRATION_FRAMES(60)//3=20 에 한참 못 미침
    est._freeze_ref_bbox_hw()
    assert est._ref_bbox_hw is None


def test_last_axis_reflects_the_selected_axis():
    """오버레이가 축 선을 그릴 축이다 — select_axis 가 고른 축과 어긋나면 안 된다."""
    from follower_perception.pose_calib import PoseCalib
    calib = PoseCalib(axis_priority=("shoulder_knee",))
    est = PoseEstimator(model=FakeModel(_standing(200)), calib=calib)
    assert est.last_axis == "torso"          # 기본값 — 아직 성공한 추론이 없다
    _, needed = est.calibration_progress
    for _ in range(needed + 1):
        est.classify(_frame(), BBOX)
    assert est.last_axis == "shoulder_knee"


# ── ref_bbox_hw 오프라인 캘리브 우선순위 ─────────────────────────────────────
#
# scripts/calibrate_pose.py 는 이미 결과 JSON 에 ref_bbox_hw 를 싣는다(사람이
# 직립을 확인한 구간에서 잰 값). 그 값이 있으면 런타임 60프레임 측정보다
# 우선해야 한다 — 런타임 표본은 등록 순간 카메라 앞에 뭐가 있었든 그대로
# 잰 값이라 신뢰도가 다르다.
#
# ⚠️ 이 리포트를 쓰는 시점에 `PoseCalib` 는 아직 `ref_bbox_hw` 필드가 없다
# (다른 에이전트가 pose_calib.py 를 따로 고치는 중이다 — 그 파일은 이 작업
# 범위 밖이라 손대지 않는다). 로컬 서브클래스로 그 필드를 흉내 낸다 — 필드가
# 실제로 추가된 뒤에도(같은 이름·기본값이면) 그대로 통과한다.

def _calib_with(**overrides):
    from dataclasses import dataclass

    from follower_perception.pose_calib import PoseCalib

    @dataclass(frozen=True)
    class _CalibWithRefBboxHw(PoseCalib):
        ref_bbox_hw: float = None

    return _CalibWithRefBboxHw(**overrides)


def test_calib_ref_bbox_hw_seeds_and_skips_runtime_samples():
    """캘리브 파일 값이 있으면 즉시 쓰고 런타임 표본을 더 모으지 않는다."""
    calib = _calib_with(ref_bbox_hw=1.8)
    est = PoseEstimator(model=FakeModel(_standing(200)), calib=calib)
    assert est._ref_bbox_hw == 1.8
    _, needed = est.calibration_progress
    for _ in range(needed + 1):
        est.classify(_frame(), BBOX)
    assert est._ref_bbox_hw == 1.8          # 런타임 측정에 안 덮인다
    assert est._bbox_hw_samples == []       # 표본을 아예 안 모았다


def test_calib_ref_bbox_hw_none_keeps_runtime_measurement():
    """파일에 없으면(None) 예전처럼 런타임 60프레임에서 재고 중앙값을 굳힌다."""
    calib = _calib_with(ref_bbox_hw=None)
    est = PoseEstimator(model=FakeModel(_standing(200)), calib=calib)
    _, needed = est.calibration_progress
    for _ in range(needed + 1):
        est.classify(_frame(), BBOX)
    assert est._ref_bbox_hw == pytest.approx((BBOX[3] - BBOX[1]) / (BBOX[2] - BBOX[0]))
    assert len(est._bbox_hw_samples) == needed
