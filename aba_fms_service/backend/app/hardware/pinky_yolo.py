"""Pinky Pro YOLO inference service."""
from __future__ import annotations

import base64
import threading
import time
from typing import Any

import cv2
import numpy as np

from app.config import BASE_DIR
from app.hardware.camera_stream import camera

MODEL_PATH = BASE_DIR / "models" / "pinky_best.pt"
DATASET_PATH = BASE_DIR / "pinky.v1i.yolov8" / "data.yaml"


class PinkyYoloService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_model_name = ""
        self._model: Any = None
        self._error: str | None = None
        self._confidence = 0.25
        self._last_frame_id = -1
        self._last_result: dict[str, Any] | None = None

    @property
    def confidence(self) -> float:
        return self._confidence

    def set_confidence(self, value: float) -> None:
        self._confidence = max(0.1, min(0.95, float(value)))

    def load_model(self, model_name: str = "pinky_best") -> bool:
        with self._lock:
            if self._model is not None and self._current_model_name == model_name:
                return True
            
            # Determine path based on model_name
            if model_name == "yolov8n":
                path = BASE_DIR / "yolov8n.pt"
            else:
                path = MODEL_PATH  # backend/models/pinky_best.pt

            if not path.exists():
                self._error = f"학습 모델이 없습니다: {path}"
                return False
            try:
                from ultralytics import YOLO

                self._model = YOLO(str(path))
                self._current_model_name = model_name
                self._error = None
                return True
            except Exception as exc:
                self._error = f"YOLO 모델 로드 실패: {exc}"
                return False

    def _predict_frame(self, frame: np.ndarray, model_name: str = "pinky_best") -> dict[str, Any] | None:
        if not self.load_model(model_name):
            return {"type": "error", "message": self._error}

        started = time.perf_counter()
        try:
            # 카메라가 물리적으로 상하반전(180도 회전) 설치되어 있으므로, 원본 이미지는 거꾸로 보입니다.
            # 모델이 사람을 똑바로 인지하도록 세로 반전(vertical flip)한 프레임으로 예측을 수행합니다.
            frame_flipped = cv2.flip(frame, 0)
            result = self._model.predict(frame_flipped, conf=self._confidence, imgsz=960, verbose=False)[0]

            detections: list[dict[str, Any]] = []
            if result.boxes is not None:
                for box in result.boxes:
                    cls_id = int(box.cls[0].item())
                    score = float(box.conf[0].item())
                    # 반전(정방향) 이미지 기준의 좌표계
                    x1, y1, x2, y2 = [round(float(v), 1) for v in box.xyxy[0].tolist()]

                    label = str(result.names[cls_id])
                    # yolov8n 등 COCO 모델 호환성 유지
                    if label == "person":
                        label = "human"

                    detections.append({
                        "class_id": cls_id,
                        "label": label,
                        "confidence": round(score, 4),
                        "box": [x1, y1, x2, y2],
                    })

            # 시각화(plot)는 반전된 프레임에 그린 뒤, 
            # 프론트엔드가 자체적으로 scaleY(-1) 하여 렌더링하므로 다시 상하반전하여 전송합니다.
            rendered_flipped = result.plot()
            rendered = cv2.flip(rendered_flipped, 0)
            ok, buffer = cv2.imencode(".jpg", rendered, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                return None
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            payload = {
                "type": "detection",
                "frame": base64.b64encode(buffer.tobytes()).decode(),
                "detections": detections,
                "inference_ms": elapsed_ms,
                "timestamp": time.time(),
            }
            self._last_result = {key: value for key, value in payload.items() if key != "frame"}
            return payload
        except Exception as exc:
            self._error = f"YOLO 추론 실패: {exc}"
            return {"type": "error", "message": self._error}

    def detect_frame(self, frame: np.ndarray) -> dict[str, Any] | None:
        return self._predict_frame(frame)

    def detect_jpeg(self, jpeg: bytes, model_name: str = "pinky_best") -> dict[str, Any] | None:
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return None
        return self._predict_frame(frame, model_name)

    def status(self) -> dict[str, Any]:
        return {
            "available": MODEL_PATH.exists(),
            "loaded": self._model is not None,
            "model_path": str(MODEL_PATH),
            "dataset_path": str(DATASET_PATH),
            "classes": ["human", "pinky_63"],
            "confidence": self._confidence,
            "error": self._error,
            "last_result": self._last_result,
        }

    def detect_latest(self, model_name: str = "pinky_best") -> dict[str, Any] | None:
        frame_id, jpeg = camera.get_frame()
        if jpeg is None or frame_id == self._last_frame_id:
            return None
        self._last_frame_id = frame_id
        return self.detect_jpeg(jpeg, model_name)


pinky_yolo = PinkyYoloService()
