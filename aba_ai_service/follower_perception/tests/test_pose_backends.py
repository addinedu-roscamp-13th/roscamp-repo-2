"""모델 어댑터 계약. 실제 가중치 없이 계약만 확인한다."""
import numpy as np
import pytest

from scripts.pose_backends import MODELS, UltralyticsBackend, make_backend


def test_model_registry_has_the_four_planned_models():
    for name in ("yolo11n-pose", "rtmpose-m-humanart", "rtmpose-m-body7", "yolo26m-pose"):
        assert name in MODELS, f"{name} 이 레지스트리에 없다"


def test_registry_entries_declare_a_kind():
    for name, spec in MODELS.items():
        assert spec["kind"] in ("ultralytics", "rtmlib"), name


def test_unknown_model_raises_with_the_known_names():
    with pytest.raises(ValueError, match="yolo11n-pose"):
        make_backend("nope")


class _FakeKp:
    def __init__(self, xy, conf):
        self.xy, self.conf = [xy], [conf]


class _FakeRes:
    def __init__(self, kp):
        self.keypoints = kp


def test_ultralytics_backend_returns_17x2_and_17():
    xy = np.arange(34, dtype=float).reshape(17, 2)
    conf = np.full(17, 0.7)
    b = UltralyticsBackend.__new__(UltralyticsBackend)
    b._model = lambda crop, verbose=False: [_FakeRes(_FakeKp(xy, conf))]
    out_xy, out_conf = b.infer(np.zeros((40, 20, 3), dtype=np.uint8))
    assert out_xy.shape == (17, 2)
    assert out_conf.shape == (17,)


def test_ultralytics_backend_returns_none_when_nothing_detected():
    b = UltralyticsBackend.__new__(UltralyticsBackend)
    b._model = lambda crop, verbose=False: []
    assert b.infer(np.zeros((40, 20, 3), dtype=np.uint8)) is None


def test_backend_swallows_inference_errors():
    """추론 실패가 벤치 전체를 죽이면 안 된다 — 그 프레임만 버린다."""
    def boom(crop, verbose=False):
        raise RuntimeError("CUDA 어쩌고")
    b = UltralyticsBackend.__new__(UltralyticsBackend)
    b._model = boom
    assert b.infer(np.zeros((40, 20, 3), dtype=np.uint8)) is None


def test_no_registry_entry_hardcodes_a_home_path():
    """LIBI_YOLO_POSE_DIR 를 이식 가능한 경로로 돌려도 레지스트리에 /home/ 로 시작하는
    값이 남아있으면 안 된다 — 남아있다면 constants.POSE_WEIGHTS 를 거치지 않고
    scripts/pose_backends.py 안에 경로가 다시 하드코딩됐다는 뜻이다.

    (이 머신의 기본값 자체가 /home/ 라 env 를 실제로 바꿔야 의미 있게 잡힌다 —
    constants.py 의 YOLO_POSE_DIR 주석 참고. POSE_WEIGHTS/MODELS 는 import 시
    한 번 계산되는 모듈 상수라 env 를 바꾼 뒤 reload 로 다시 계산시켜야 한다.)
    """
    import importlib
    import os

    from follower_perception import constants
    from scripts import pose_backends

    env_backup = {k: os.environ.get(k) for k in ("LIBI_YOLO_POSE_DIR", "LIBI_POSE_WEIGHTS")}
    os.environ["LIBI_YOLO_POSE_DIR"] = "/opt/yolo_pose"
    os.environ.pop("LIBI_POSE_WEIGHTS", None)
    try:
        importlib.reload(constants)
        importlib.reload(pose_backends)
        for name, spec in pose_backends.MODELS.items():
            assert not str(spec["weights"]).startswith("/home/"), name
    finally:
        for k, v in env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(constants)
        importlib.reload(pose_backends)
