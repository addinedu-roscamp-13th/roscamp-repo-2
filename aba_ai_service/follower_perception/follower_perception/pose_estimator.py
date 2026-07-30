"""주인(owner) bbox crop 에만 자세 모델을 돌려 "서 있나"를 판정한다.

## 왜 2차 추론인가 — 검출 가중치를 바꾸지 않는 이유

`weights/best.pt` 는 `task=detect` 라 **키포인트를 내지 않는다**(names={0:'people',
1:'figure'}). 자세 판정에는 어깨 2점·골반 2점이 필요하다. 그렇다고 검출 자체를
pose 모델로 갈아치우면 도서관 환경에 맞춰 학습된 그 가중치를 버리게 된다.
그래서 검출·재식별은 그대로 두고, **확정된 owner 의 bbox crop 에만** pose 를 돌린다.
전체 프레임이 아니라 crop 이라 비용이 작다.

## 기준 비율을 등록마다 다시 재는 이유

`RatioCalibrator` 가 재는 기준(몸 폭 대비 몸통 길이)은 체형뿐 아니라 **카메라 높이와
거리**에 따라 달라진다(yolo_pose README: 같은 인형이 1.88 vs 1.66). 로봇은 매번 다른
자리에서 등록하므로 한 번 잰 기준을 계속 쓸 수 없다. 재는 동안에는 "Calibrating" 을
돌려 주행을 막는다 — 등록 순간은 사람이 로봇 앞에 서서 화면을 누르는 시점이라
"똑바로 선 자세를 비춰 달라"는 측정 조건이 마침 맞는다.

## 알고리즘을 복제하지 않는다

판정 로직은 `~/personal_repo/yolo_pose/posture.py` 를 **import 해서** 쓴다. 복제하면
임계값(CONF_MIN, BAND_DEG, 기준비율)이 두 곳으로 갈라져 한쪽만 고쳐진다.
"""
import os
import sys

from .constants import POSE_EVERY_N_FRAMES, POSE_WEIGHTS, YOLO_POSE_DIR

CALIBRATING = "Calibrating"

_posture_module = None


def _posture_dir() -> str:
    # constants.YOLO_POSE_DIR 이 이미 환경변수를 반영하지만, 테스트가 실행 중에
    # 환경변수를 바꿔 끼우므로 여기서 한 번 더 읽는다.
    return os.environ.get("LIBI_YOLO_POSE_DIR", YOLO_POSE_DIR)


def load_posture_module():
    """`yolo_pose` 저장소의 `posture` 모듈. 한 번만 import 한다.

    ⚠️ 경로를 `Path(__file__).parents[N]` 로 계산하면 안 된다 — yolo_pose 는 이
    저장소 **밖**의 별개 저장소라 `aba_project/yolo_pose` 를 가리켜 실패한다.
    """
    global _posture_module
    if _posture_module is not None:
        return _posture_module
    d = _posture_dir()
    if not os.path.isdir(d):
        raise RuntimeError(
            f"yolo_pose 저장소를 찾지 못했습니다: {d}\n"
            f"  LIBI_YOLO_POSE_DIR 환경변수로 경로를 주거나 constants.YOLO_POSE_DIR 을 고치세요.")
    if d not in sys.path:
        sys.path.insert(0, d)
    import posture                                  # noqa: PLC0415 — 지연 import 가 목적
    _posture_module = posture
    return posture


class PoseEstimator:
    """`classify(frame, bbox) -> 자세 문자열`. 모델은 첫 호출에 지연 로드한다.

    `model` 을 주입하면 그걸 쓴다(테스트·다른 가중치). 주입한 모델은
    `model(crop, verbose=False)` 호출에 ultralytics 결과와 같은 모양을 돌려주면 된다.
    """

    def __init__(self, model=None, every_n: int = POSE_EVERY_N_FRAMES,
                 weights: str = POSE_WEIGHTS, threshold_deg: float = None):
        self._posture = load_posture_module()
        self._model = model
        self._weights = weights
        self._threshold_deg = threshold_deg
        self.every_n = max(1, int(every_n))
        self._frame = 0
        self._last = self._posture.UNKNOWN
        self._calibrator = self._posture.RatioCalibrator()
        #: 마지막으로 **추론한** 키포인트 `(xy(17,2), conf(17,), (ox, oy))`. 없으면 None.
        #  좌표는 crop 기준이라 그리려면 원점 `(ox, oy)` 를 더해야 한다.
        #  판정에는 안 쓴다 — 순전히 화면에 스켈레톤을 그리기 위한 것이다. 추론을 건너뛴
        #  프레임에서는 갱신하지 않아, 그림이 `every_n` 주기로 깜빡이지 않고 유지된다.
        self.last_keypoints = None

    @property
    def conf_min(self) -> float:
        """키포인트를 믿을 최소 신뢰도. 판정과 **같은 값**을 써야 화면과 판정이 안 어긋난다."""
        return float(getattr(self._posture, "CONF_MIN", 0.5))

    # ── 기준 비율 ────────────────────────────────────────────────────────
    def recalibrate(self) -> None:
        """등록 시 호출. 기준을 처음부터 다시 잰다.

        리셋 메서드가 아니라 **새 인스턴스**를 만든다 — `RatioCalibrator` 는 한 번
        확정하면 갱신하지 않는 설계이고(그 클래스 주석: "다시 재려면 새 인스턴스를
        만든다"), 내부 상태를 밖에서 비우면 그 계약을 깨는 것이다.
        """
        self._calibrator = self._posture.RatioCalibrator()
        self._frame = 0
        self._last = CALIBRATING

    @property
    def calibrating(self) -> bool:
        return not self._calibrator.done

    @property
    def calibration_progress(self):
        """(모은 프레임, 필요한 프레임). 패널의 "자세 측정 중 23/60" 표시에 쓴다."""
        return self._calibrator.progress

    # ── 판정 ────────────────────────────────────────────────────────────
    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO             # noqa: PLC0415 — 지연 로드
            self._model = YOLO(self._weights)
        return self._model

    def classify(self, frame, bbox) -> str:
        self._frame += 1
        if self._frame % self.every_n != 0:
            return self._last          # 주기를 낮춘 프레임 — 직전 판정을 유지한다

        xy, conf = self._keypoints(frame, bbox)
        if xy is None:
            self._last = self._posture.UNKNOWN
            return self._last

        if not self._calibrator.done:
            self._calibrator.update(self._posture.torso_ratio(xy))
            # 방금 확정됐더라도 이번 프레임은 측정 프레임으로 친다 — 확정 직후
            # 같은 프레임으로 판정하면 표본에 넣은 값으로 자기 자신을 재는 셈이다.
            self._last = CALIBRATING
            return self._last

        kwargs = {"ref_ratio": self._calibrator.reference}
        if self._threshold_deg is not None:
            kwargs["threshold_deg"] = self._threshold_deg
        # ⚠️ (상태, 각도) **튜플**을 돌려준다. 튜플을 그대로 payload 에 실으면
        #    소비자(PostureGate)가 전부 Unknown 으로 처리한다.
        state, _angle = self._posture.classify_posture(xy, conf, **kwargs)
        self._last = state
        return self._last

    def _keypoints(self, frame, bbox):
        """crop 을 추론해 (17,2) 좌표와 (17,) 신뢰도를 돌려준다. 못 얻으면 (None, None).

        crop 좌표계 그대로 쓴다 — 자세 판정은 **상대 기하**(어깨선·골반선·몸통축의
        길이 비율과 각도)만 보므로 원본 프레임 좌표로 되돌릴 필요가 없다.
        """
        self.last_keypoints = None      # 아래에서 성공했을 때만 다시 채운다
        if frame is None or bbox is None:
            return None, None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return None, None
        crop = frame[y1:y2, x1:x2]
        try:
            res = self._ensure_model()(crop, verbose=False)
        except Exception:              # noqa: BLE001 — 추론 실패로 추종 루프를 죽이지 않는다
            return None, None
        if not res:
            return None, None
        kp = getattr(res[0], "keypoints", None)
        if kp is None or kp.xy is None or len(kp.xy) == 0:
            return None, None
        xy = _to_numpy(kp.xy[0])
        conf = _to_numpy(kp.conf[0]) if getattr(kp, "conf", None) is not None else None
        if conf is None:
            # 신뢰도가 없으면 판정 근거가 없다. 0 으로 채우면 전부 Unknown 이 되는데,
            # 그게 맞다 — "모른다"를 "괜찮다"로 바꾸지 않는다.
            import numpy as np         # noqa: PLC0415
            conf = np.zeros(len(xy), dtype=float)
        self.last_keypoints = (xy, conf, (x1, y1))
        return xy, conf


def _to_numpy(t):
    """torch 텐서든 numpy 든 numpy 배열로. posture.py 가 np.asarray 로 받는다."""
    cpu = getattr(t, "cpu", None)
    if cpu is not None:
        t = cpu()
    numpy = getattr(t, "numpy", None)
    return numpy() if numpy is not None else t
