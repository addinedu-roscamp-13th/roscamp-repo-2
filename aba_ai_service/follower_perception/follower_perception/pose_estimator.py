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
import math
import os
import sys
import time
from statistics import median

from .constants import (
    POSE_CALIBRATION_TIMEOUT_SEC, POSE_EVERY_N_FRAMES, POSE_WEIGHTS, YOLO_POSE_DIR,
)
from .keypoint_filter import KeypointFilter
from .pose_calib import load_pose_calib

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
                 weights: str = POSE_WEIGHTS, threshold_deg: float = None,
                 calib=None):
        self._posture = load_posture_module()
        self._model = model
        self._weights = weights
        self._threshold_deg = threshold_deg
        self.every_n = max(1, int(every_n))
        self._frame = 0
        self._last = self._posture.UNKNOWN
        #: pose_calib.json (Task 4). 없으면 기본값 — 예전처럼 60프레임을 재고
        #: bbox 가드는 꺼져 있다.
        self._calib = calib if calib is not None else load_pose_calib()
        #: 켜져 있을 때만 만든다 — 필터가 좌표를 바꾸면 "설정이 없으면 예전과 같은
        #: 판정"이라는 회귀 방어선이 깨진다(keypoint_filter.py 머리말 참고).
        self._filter = (KeypointFilter(**self._calib.filter, conf_min=self.conf_min)
                         if isinstance(self._calib.filter, dict) else None)
        self._filter_ts = None          # 마지막으로 필터를 먹인 시각(monotonic)
        #: 마지막으로 **선택된** 몸통 축. 오버레이가 이 축으로 선을 그린다 —
        #: 안 맞추면 shoulder_knee/head_hip 로 판정하고 torso 로 그리는 어긋남이 난다.
        self.last_axis = self._posture.AXIS_TORSO
        self._reset_calibrator()
        #: 마지막으로 **추론한** 키포인트 `(xy(17,2), conf(17,), (ox, oy))`. 없으면 None.
        #  좌표는 crop 기준이라 그리려면 원점 `(ox, oy)` 를 더해야 한다.
        #  판정에는 안 쓴다 — 순전히 화면에 스켈레톤을 그리기 위한 것이다. 추론을 건너뛴
        #  프레임에서는 갱신하지 않아, 그림이 `every_n` 주기로 깜빡이지 않고 유지된다.
        self.last_keypoints = None
        #: 마지막으로 잰 **몸통 비율**(선택된 축 기준). 판정에 쓰는 그 값이다 —
        #  화면에 숫자로 띄우려고 따로 계산하면 화면과 판정이 어긋난다.
        #  키포인트가 없거나 폭이 0 이면 None.
        self.last_ratio = None

    @property
    def conf_min(self) -> float:
        """키포인트를 믿을 최소 신뢰도. 판정과 **같은 값**을 써야 화면과 판정이 안 어긋난다."""
        return float(getattr(self._posture, "CONF_MIN", 0.5))

    # ── 기준 비율 ────────────────────────────────────────────────────────
    def _effective_axis_priority(self):
        """캘리브의 축 우선순위. 비어 있으면(`axis_priority=()`) 모듈 기본값으로 되돌린다."""
        return self._calib.axis_priority or self._posture.DEFAULT_AXIS_PRIORITY

    def _reset_calibrator(self) -> None:
        """새 `RatioCalibrator` 를 만들고, bbox 가드 표본도 같이 비운다.

        캘리브 파일에 우선순위 첫 축의 기준값이 있으면 60프레임 측정 없이
        즉시 그 값을 쓴다(Task 4 seed) — 등록 직후 멈춰 서 있는 구간이 사라진다.

        `ref_bbox_hw` 도 파일 값이 있으면 그걸 즉시 쓴다 — 파일 값은 사람이
        **직립을 확인한** 구간에서 오프라인으로 잰 값이고, 런타임 60프레임은
        등록 순간 카메라 앞에 뭐가 있었든(옆으로 서 있어도) 그대로 잰 값이라
        신뢰도가 다르다. 파일 값이 없으면(None) 예전처럼 런타임에서 재고,
        표본이 모자라면 가드를 끈다(`_freeze_ref_bbox_hw`).

        ⚠️ `getattr` 로 읽는다 — 이 필드를 아직 안 가진 `PoseCalib` 이 들어와도
        (역주: pose_calib.py 는 다른 작업 범위다) None 취급돼 예전 동작 그대로다.
        """
        cal = self._posture.RatioCalibrator()
        axis = self._effective_axis_priority()[0]
        seed = (self._calib.ref_ratios or {}).get(axis)
        if seed is not None:
            cal.reference = seed
        self._calibrator = cal
        self._bbox_hw_samples = []
        self._ref_bbox_hw = getattr(self._calib, "ref_bbox_hw", None)
        #: 측정을 시작한 시각. 제한 시간을 재는 기준이다 — 첫 판정 프레임에 채운다
        #: (여기서 채우면 등록 전 대기 시간까지 세어 시작하자마자 만료될 수 있다).
        self._calib_since = None
        self._calib_gave_up = False

    def _freeze_ref_bbox_hw(self) -> None:
        """측정 구간 bbox 종횡비 표본의 중앙값을 굳힌다.

        표본이 `CALIBRATION_FRAMES // 3` 미만이면 `None` 으로 둔다 — 근거가
        모자란 기준으로 판정하는 것보다 그 신호를 아예 안 쓰는 편이 낫다.
        """
        valid = [s for s in self._bbox_hw_samples if s is not None]
        min_samples = self._posture.CALIBRATION_FRAMES // 3
        self._ref_bbox_hw = median(valid) if len(valid) >= min_samples else None

    def recalibrate(self) -> None:
        """등록 시 호출. 기준을 처음부터 다시 잰다.

        리셋 메서드가 아니라 **새 인스턴스**를 만든다 — `RatioCalibrator` 는 한 번
        확정하면 갱신하지 않는 설계이고(그 클래스 주석: "다시 재려면 새 인스턴스를
        만든다"), 내부 상태를 밖에서 비우면 그 계약을 깨는 것이다.
        """
        self._reset_calibrator()
        self._frame = 0
        self._last = CALIBRATING
        self.last_axis = self._posture.AXIS_TORSO
        self.last_ratio = None
        if self._filter is not None:
            self._filter.reset()
        self._filter_ts = None

    @property
    def calibrating(self) -> bool:
        """측정 중인가. **포기했으면 False** — 화면 카운트다운이 영영 안 끝나는
        숫자를 띄우면 사람이 로봇이 고장 났다고 읽는다."""
        return not self._calibrator.done and not self._calib_gave_up

    @property
    def ref_ratio(self):
        """등록할 때 잰 **기준 비율**. 측정이 안 끝났으면 None."""
        return self._calibrator.reference if self._calibrator.done else None

    @property
    def side_trip(self):
        """이 값을 넘으면 측면으로 본다 = 기준 × side_factor. 기준이 없으면 None.

        화면에 기준만 띄우면 "지금 값이 얼마나 모자란지"를 사람이 암산해야 한다.
        판정선을 같이 내보내면 숫자 셋만 보고 바로 읽힌다.
        """
        ref = self.ref_ratio
        f = self._calib.side_factor
        if ref is None or not f or f < 1:
            return None
        return ref * f

    @property
    def calibration_progress(self):
        """(모은 프레임, 필요한 프레임). 패널의 "자세 측정 중 23/60" 표시에 쓴다."""
        return self._calibrator.progress

    def calibration_remaining_sec(self, fps) -> float:
        """기준 측정이 끝나기까지 남은 초. 화면 카운트다운이 쓴다.

        ⚠️ **실측 fps 를 받는다.** 공칭 15 로 나누면 프레임이 밀릴 때 숫자가
        멈춘 것처럼 보여, 사람이 로봇이 고장 났다고 생각한다.
        """
        if not self.calibrating:
            return 0.0
        got, need = self.calibration_progress
        fps = float(fps) if fps and fps > 0 else 15.0
        return max(0.0, (need - got) / fps)

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

        xy, conf, clamped = self._keypoints(frame, bbox)
        if xy is None:
            # ⚠️ 여기서 곧장 Unknown 을 내보내면 bbox 가드가 죽은 코드가 된다
            # (PRD Story 41/42). 포즈 모델이 크롭에서 키포인트를 하나도 못 낸
            # 경우가 뒷캠에서 가장 흔한 실패 조건인데, 그게 바로 가드가 유일한
            # 판정 근거가 되는 순간이다. bbox 는 이미 계산해 놨으니 버리지 않고
            # 키포인트 없이도 판단하는 신호(bbox_guard)에 먼저 물어본다.
            self._last = self._guard_only(frame, clamped)
            return self._last

        if self._filter is not None:
            now = time.monotonic()
            dt = 0.0 if self._filter_ts is None else now - self._filter_ts
            self._filter_ts = now
            xy = self._filter.apply(xy, conf, dt)
            if self.last_keypoints is not None:
                self.last_keypoints = (xy, conf, self.last_keypoints[2])
            # ⚠️ 화면도 **판정과 같은 좌표**를 그린다.
            #
            # `last_keypoints` 는 `_keypoints()` 가 필터 **전** 값으로 채운다(원본
            # 배열이고, `apply` 는 복사본을 돌려준다). 여기서 안 덮으면 판정만 걸러지고
            # 그림은 원본이라 "판정은 나아졌는데 화면은 그대로 떤다"가 된다 —
            # 필터를 켜 본 사람이 "필터가 안 먹는다"로 읽게 되는 자리다.

        bbox_wh, bbox_clipped = self._bbox_wh_and_clipped(frame, clamped)

        if not self._calibrator.done and not self._calib_gave_up:
            # ⚠️ 신뢰도를 통과한 프레임만 표본에 넣는다. 예전에는 검사가 없어서
            #    난수 좌표가 기준 중앙값에 섞였다. 측정 구간에 옆으로 선 프레임이
            #    많으면 기준이 커지고, 그만큼 측면 임계도 같이 커져 이후의 진짜
            #    측면을 못 잡는다.
            if all(conf[i] >= self.conf_min for i in self._posture.TORSO_INDICES):
                self._calibrator.update(self._posture.torso_ratio(xy))
                # ref_bbox_hw 도 같은 표본에서 잰다 — Task 1 가드가 이 값을
                # 기준으로 쓴다. 캘리브 파일이 이미 값을 줬으면(_reset_calibrator)
                # 런타임 표본을 더 모으지 않는다 — 파일 값이 이긴다.
                if self._ref_bbox_hw is None:
                    self._bbox_hw_samples.append(self._posture.bbox_hw(bbox_wh))
                    if self._calibrator.done:
                        self._freeze_ref_bbox_hw()
            # 제한 시간 — 골격이 안 잡혀 표본이 안 모이면 **영영 안 끝난다.** 그동안
            # 코스팅이 통째로 막히고(`_NO_COAST_POSTURES`) 주행도 멈춘다. 시간이 다
            # 되면 기준을 억지로 세우지 않고 `Unknown` 으로 넘어간다 — 판정은 포기하되
            # 거짓말은 안 한다(근거는 constants.POSE_CALIBRATION_TIMEOUT_SEC 주석).
            if not self._calibrator.done and POSE_CALIBRATION_TIMEOUT_SEC > 0:
                now = time.monotonic()
                if self._calib_since is None:
                    self._calib_since = now          # 첫 판정 프레임부터 잰다
                elif now - self._calib_since >= POSE_CALIBRATION_TIMEOUT_SEC:
                    got, need = self.calibration_progress
                    print(f"[pose] 기준 측정 포기 — {POSE_CALIBRATION_TIMEOUT_SEC:.0f}초 안에 "
                          f"{got}/{need} 장만 모였습니다(골격 신뢰도 부족). "
                          f"자세를 Unknown 으로 두고 계속합니다", flush=True)
                    self._calib_gave_up = True
                    self._last = self._posture.UNKNOWN
                    return self._last
            # 방금 확정됐더라도 이번 프레임은 측정 프레임으로 친다 — 확정 직후
            # 같은 프레임으로 판정하면 표본에 넣은 값으로 자기 자신을 재는 셈이다.
            self._last = CALIBRATING
            return self._last

        axis_priority = self._effective_axis_priority()
        # classify_posture 는 어느 축을 썼는지 돌려주지 않는다 — 오버레이가
        # 그릴 축을 알려면 여기서도 한 번 더 골라야 한다.
        axis = self._posture.select_axis(xy, conf, axis_priority)
        if axis is not None:
            self.last_axis = axis
        # 판정이 실제로 쓰는 그 비율을 그대로 들고 있는다(화면에 숫자로 띄운다).
        # 폭이 0 이면 posture 쪽이 inf 를 낸다 — 화면에는 숫자가 아니라 없음으로 낸다.
        r = self._posture.torso_ratio(xy, conf, self.last_axis)
        self.last_ratio = r if isinstance(r, float) and math.isfinite(r) else None

        kwargs = {
            "ref_ratio": self._calibrator.reference,
            "bbox_wh": bbox_wh,
            "ref_bbox_hw": self._ref_bbox_hw,
            "bbox_clipped": bbox_clipped,
            "bbox_lying_frac": self._calib.bbox_lying_frac,
            "bbox_side_frac": self._calib.bbox_side_frac,
            "side_factor": self._calib.side_factor,
            "axis_priority": axis_priority,
            "ref_ratios": self._calib.ref_ratios,
        }
        if self._threshold_deg is not None:
            kwargs["threshold_deg"] = self._threshold_deg
        # ⚠️ (상태, 각도) **튜플**을 돌려준다. 튜플을 그대로 payload 에 실으면
        #    소비자(PostureGate)가 전부 Unknown 으로 처리한다.
        state, _angle = self._posture.classify_posture(xy, conf, **kwargs)
        self._last = state
        return self._last

    def _bbox_wh_and_clipped(self, frame, clamped):
        """클램프된 bbox 에서 (bbox_wh, bbox_clipped) 를 만든다.

        키포인트가 있는 경로와 없는 경로(`_guard_only`) 가 같은 계산을 공유한다
        — 따로 두면 둘 중 하나만 고쳤을 때 화면 경계 판정이 어긋난다.
        """
        x1, y1, x2, y2 = clamped
        fh, fw = frame.shape[:2]
        bbox_wh = (x2 - x1, y2 - y1)
        # 프레임 경계에서 2px 이내면 잘린 것으로 본다 — 잘린 bbox 는 높이·폭이
        # 진짜가 아니라 bbox 가드가 믿으면 안 된다(posture.bbox_guard 참고).
        bbox_clipped = x1 <= 2 or y1 <= 2 or (fw - x2) <= 2 or (fh - y2) <= 2
        return bbox_wh, bbox_clipped

    def _guard_only(self, frame, clamped):
        """키포인트를 하나도 못 얻은 프레임에서 bbox 가드만으로 내리는 판정.

        여기가 bbox 가드가 있어야 할 자리다 — 키포인트 신뢰도 게이트보다도
        먼저다(PRD Story 41/42). `clamped` 가 None 이면 크롭 자체가 없었다는
        뜻이라(프레임/bbox 가 없거나 너무 작음) 비교할 bbox 조차 없어 Unknown
        으로 둔다. `clamped` 가 있으면 크롭은 유효했는데 포즈 모델이 키포인트를
        하나도 못 낸 것이다 — 뒷캠에서 가장 흔한 실패 조건이 바로 이거라 여기가
        가드의 존재 이유다.

        ⚠️ 계산기(`_calibrator`)도 `last_axis` 도 안 건드린다 — 잴 키포인트
        자체가 없으니 여기 값을 표본에 넣으면 기준을 오염시킨다.
        """
        if clamped is None:
            return self._posture.UNKNOWN
        bbox_wh, bbox_clipped = self._bbox_wh_and_clipped(frame, clamped)
        guard = self._posture.bbox_guard(
            bbox_wh, self._ref_bbox_hw,
            lying_frac=self._calib.bbox_lying_frac,
            side_frac=self._calib.bbox_side_frac,
            clipped=bbox_clipped,
        )
        return guard if guard is not None else self._posture.UNKNOWN

    def _clamp_bbox(self, frame, bbox):
        """bbox 를 프레임 경계로 자른다. 못 쓰는 bbox 면 None.

        crop 과 판정(bbox_wh/bbox_clipped/ref_bbox_hw 표본)이 같은 클램프 값을
        써야 화면과 판정이 서로 다른 bbox 를 보는 일이 없다.
        """
        if frame is None or bbox is None:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return None
        return x1, y1, x2, y2

    def _keypoints(self, frame, bbox):
        """crop 을 추론해 (17,2) 좌표·(17,) 신뢰도·클램프된 bbox 를 돌려준다.

        키포인트를 못 얻으면 (None, None, clamped) — crop 자체는 유효했다는
        뜻이라 호출자가 `clamped` 로 bbox 가드를 돌릴 수 있다. bbox 자체가
        없으면(crop 을 못 만들었으면) (None, None, None).

        ⚠️ 키포인트 실패 경로에서도 `clamped` 를 버리지 않는다 — 예전에는
        여기서 `None` 을 돌려줘 bbox 가드가 죽은 코드였다(PRD Story 41/42).

        crop 좌표계 그대로 쓴다 — 자세 판정은 **상대 기하**(어깨선·골반선·몸통축의
        길이 비율과 각도)만 보므로 원본 프레임 좌표로 되돌릴 필요가 없다.
        """
        self.last_keypoints = None      # 아래에서 성공했을 때만 다시 채운다
        clamped = self._clamp_bbox(frame, bbox)
        if clamped is None:
            return None, None, None
        x1, y1, x2, y2 = clamped
        crop = frame[y1:y2, x1:x2]
        try:
            res = self._ensure_model()(crop, verbose=False)
        except Exception:              # noqa: BLE001 — 추론 실패로 추종 루프를 죽이지 않는다
            return None, None, clamped
        if not res:
            return None, None, clamped
        kp = getattr(res[0], "keypoints", None)
        if kp is None or kp.xy is None or len(kp.xy) == 0:
            return None, None, clamped
        xy = _to_numpy(kp.xy[0])
        conf = _to_numpy(kp.conf[0]) if getattr(kp, "conf", None) is not None else None
        if conf is None:
            # 신뢰도가 없으면 판정 근거가 없다. 0 으로 채우면 전부 Unknown 이 되는데,
            # 그게 맞다 — "모른다"를 "괜찮다"로 바꾸지 않는다.
            import numpy as np         # noqa: PLC0415
            conf = np.zeros(len(xy), dtype=float)
        self.last_keypoints = (xy, conf, (x1, y1))
        return xy, conf, clamped


def _to_numpy(t):
    """torch 텐서든 numpy 든 numpy 배열로. posture.py 가 np.asarray 로 받는다."""
    cpu = getattr(t, "cpu", None)
    if cpu is not None:
        t = cpu()
    numpy = getattr(t, "numpy", None)
    return numpy() if numpy is not None else t
