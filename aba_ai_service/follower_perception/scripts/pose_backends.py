"""pose 모델 어댑터 — 같은 인터페이스 뒤에 두 런타임을 숨긴다.

`infer(crop_bgr) -> ((17,2) 좌표, (17,) 신뢰도) | None`

ONNX URL 은 2026-07-31 에 HEAD 로 존재를 확인했다. `rtmpose-m-coco` 는
onnx_sdk 에 **없다**(404) — 그래서 대조군이 body7 이고, 그 비대칭 해석을
리포트에 명시한다(pose_bench.py 머리말).
"""
import os
import sys

import numpy as np

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # follower_perception/
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from follower_perception.constants import POSE_WEIGHTS                   # noqa: E402

_OSS = "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"

MODELS = {
    "yolo11n-pose": {
        "kind": "ultralytics",
        # yolo_pose 는 이 레포 밖의 별개 저장소라 상대경로를 못 쓴다 — 이유는
        # follower_perception/constants.py 의 YOLO_POSE_DIR 주석 참고.
        "weights": POSE_WEIGHTS,
        "note": "현재 베이스라인. 개선폭 측정 기준점",
    },
    "yolo26m-pose": {
        "kind": "ultralytics",
        "weights": "yolo26m-pose.pt",       # ultralytics 가 없으면 받아온다
        "note": "STAL 이 소형 객체 positive 할당을 보장 — 스케일 문제면 여기서 뜬다",
    },
    "rtmpose-m-humanart": {
        "kind": "rtmlib",
        "weights": _OSS + "rtmpose-m_8xb256-420e_humanart-256x192-8430627b_20230611.zip",
        "input_size": (192, 256),
        "note": "최우선. Human-Art 는 Garage Kits·Sculpture 를 포함해 피규어 도메인 직격",
    },
    "rtmpose-m-body7": {
        "kind": "rtmlib",
        "weights": _OSS + "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip",
        "input_size": (192, 256),
        "note": "대조군. ⚠️ coco 가 아니라 실사 7종 합본이라 비대칭 실험이다",
    },
    "rtmpose-l-humanart": {
        "kind": "rtmlib",
        "weights": _OSS + "rtmpose-l_8xb256-420e_humanart-256x192-389f2cb0_20230611.zip",
        "input_size": (192, 256),
        "note": "상한선 확인용. 서버 추론이라 연산 여유가 있다",
    },
}


def _to_numpy(t):
    cpu = getattr(t, "cpu", None)
    if cpu is not None:
        t = cpu()
    numpy = getattr(t, "numpy", None)
    return np.asarray(numpy() if numpy is not None else t, dtype=float)


class UltralyticsBackend:
    def __init__(self, weights, device=None):
        from ultralytics import YOLO
        self._model = YOLO(weights)
        self._device = device

    def infer(self, crop_bgr):
        try:
            res = self._model(crop_bgr, verbose=False)
        except Exception:       # noqa: BLE001 — 한 프레임 실패가 벤치를 죽이면 안 된다
            return None
        if not res:
            return None
        kp = getattr(res[0], "keypoints", None)
        if kp is None or kp.xy is None or len(kp.xy) == 0:
            return None
        xy = _to_numpy(kp.xy[0])
        conf = (_to_numpy(kp.conf[0]) if getattr(kp, "conf", None) is not None
                else np.zeros(len(xy)))
        return xy, conf


class RtmlibBackend:
    def __init__(self, onnx_url, input_size=(192, 256), device="cpu"):
        from rtmlib import RTMPose
        self._model = RTMPose(onnx_model=onnx_url, model_input_size=input_size,
                              backend="onnxruntime", device=device)

    def infer(self, crop_bgr):
        try:
            # bboxes 를 비우면 이미지 전체를 하나의 인스턴스로 본다. 우리는 이미
            # owner crop 을 넘기므로 그게 맞다.
            kpts, scores = self._model(crop_bgr)
        except Exception:       # noqa: BLE001
            return None
        if kpts is None or len(kpts) == 0:
            return None
        return np.asarray(kpts[0], dtype=float), np.asarray(scores[0], dtype=float)


def make_backend(name, device="cpu"):
    spec = MODELS.get(name)
    if spec is None:
        raise ValueError(f"모르는 모델 '{name}'. 아는 것: {', '.join(sorted(MODELS))}")
    if spec["kind"] == "ultralytics":
        return UltralyticsBackend(spec["weights"], device)
    return RtmlibBackend(spec["weights"], spec.get("input_size", (192, 256)), device)
