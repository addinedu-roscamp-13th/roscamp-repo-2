from .detection import Detection
from .detector import Detector
from .exit_direction import classify_exit, may_coast
from .posture_gate import PostureGate
from .reid_engine import ReIDEngine
from .target_matcher import TargetMatcher
from .bbox_smoother import BBoxSmoother
from .constants import (
    FRAME_DT, PREDICT_DT, COAST_LIMIT, CALIBRATION_INTERVAL,
    REGISTRATION_STABLE_FRAMES, REGISTRATION_MIN_AREA_RATIO,
)


class FollowerPerception:
    """Facade: frame -> Detection. Detect -> track -> identify -> smooth.

    자세 판정(`pose`)은 **선택 사항**이다. 주입하지 않으면 `posture=None` 이 나가고
    소비자는 기존과 똑같이 동작한다 — 자세 모델 없는 배포에서 추종이 죽지 않게 한다.
    """

    def __init__(self, detector=None, reid=None, pose=None):
        self.detector = detector if detector is not None else Detector()
        self.reid = reid if reid is not None else ReIDEngine()
        self.matcher = TargetMatcher(self.reid)
        self.smoother = BBoxSmoother()
        #: 자세 추정기. None 이면 자세 판정을 하지 않는다.
        self.pose = pose
        self.posture_gate = PostureGate()
        self.last_cands = []          # raw YOLO detections from the last run()
        self._last_owner = None       # last TrackedBox seen as owner
        self._miss = 0
        self._frame_count = 0
        self._reg_id = None
        self._reg_streak = 0
        self._last_crop = None        # crop of the most recent registration
        self._last_bbox = None
        #: 마지막으로 판정한 자세. 소실 시 coast 허용 여부를 가른다.
        self._last_posture = None
        #: 놓친 **첫 순간**에 래치한 소실 방향. 매 tick 다시 재면 예측이 흘러가며 바뀐다.
        self._exit_dir = None
        self._frame_shape = None
        #: 이 프레임이 온 카메라와 그 세대. 소비자가 전환 순간의 옛 프레임을 버린다.
        self.camera = None
        self.camera_epoch = 0

    # ---- camera ------------------------------------------------------
    def set_camera(self, name):
        """카메라가 바뀌었다. epoch 를 올리고 **추적 상태만** 비운다.

        ⚠️ `matcher.reset()` 을 부르면 안 된다 — 그건 등록 템플릿까지 지운다.
        전환할 때마다 재등록해야 하는 꼴이 된다. 지울 것은 track_id 잠금과
        스무더뿐이다. 이전 카메라의 track_id 를 새 프레임에 끌고 가면 엉뚱한 사람이
        owner 로 잡힌다.
        """
        if name == self.camera:
            return
        self.camera = name
        self.camera_epoch += 1
        self.matcher.safe_id = None
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._exit_dir = None
        self._last_posture = None
        self.posture_gate.reset()

    # ---- registration -------------------------------------------------
    def register(self, frame):
        cands = self.detector.detect(frame)
        target = self._pick_central(cands, frame)
        if target is None:
            self._reg_id = None
            self._reg_streak = 0
            return False
        if target.track_id == self._reg_id:
            self._reg_streak += 1
        else:
            self._reg_id = target.track_id
            self._reg_streak = 1
        if self._reg_streak >= REGISTRATION_STABLE_FRAMES:
            roi = TargetMatcher._crop(frame, target.bbox)
            self.matcher.register(roi)
            self.smoother.reset()
            self._last_owner = None
            self._miss = 0
            self._reg_id = None
            self._reg_streak = 0
            self._on_registered()
            return True
        return False

    def _on_registered(self):
        """등록 직후 공통 처리.

        자세 기준 비율을 **다시 잰다.** 기준은 체형뿐 아니라 카메라 높이·거리에 따라
        달라지는데(yolo_pose README: 같은 인형이 1.88 vs 1.66) 로봇은 매번 다른 자리에서
        등록한다. 재는 동안에는 "Calibrating" 이 나가 주행이 멈춘다 — 등록 순간은
        사람이 로봇 앞에 서 있는 시점이라 측정 조건이 마침 맞는다.
        """
        self._last_posture = None
        self._exit_dir = None
        self.posture_gate.reset()
        if self.pose is not None:
            self.pose.recalibrate()

    def _pick_central(self, cands, frame):
        if not cands:
            return None
        h, w = frame.shape[:2]
        frame_area = float(w * h)
        cx0 = w / 2.0
        viable = [c for c in cands
                  if c.area / frame_area >= REGISTRATION_MIN_AREA_RATIO]
        if not viable:
            return None
        # nearest to center; larger area breaks ties
        return min(viable, key=lambda c: (abs(c.cx - cx0), -c.area))

    def register_from_image(self, image_bgr):
        """Register the central person from a SINGLE image (bypasses the
        3-frame stability requirement). Returns the chosen TrackedBox or None."""
        cands = self.detector.detect(image_bgr)
        target = self._pick_central(cands, image_bgr)
        if target is None:
            return None
        roi = TargetMatcher._crop(image_bgr, target.bbox)
        self.matcher.register(roi)
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._reg_id = None
        self._reg_streak = 0
        self._last_crop = roi
        self._last_bbox = list(target.bbox)
        self._on_registered()
        return target

    def save_profile(self, dir, *, name, source_image=None, registered_at=None):
        if self._last_crop is None:
            raise ValueError("no registered crop to save; call register_from_image first")
        meta = {
            "name": name,
            "registered_at": registered_at,
            "source_image": source_image,
            "bbox": self._last_bbox,
        }
        self.matcher.save(dir, crop_bgr=self._last_crop, meta=meta)

    def load_profile(self, dir, *, strict=False):
        """Load a saved profile into the matcher so tracking can resume without
        re-registration. Cross-backend safe (re-extracts from crop)."""
        self.matcher.load(dir, strict=strict)
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._frame_count = 0
        # 저장된 프로필을 불러온 것도 '새 대상'이다. 기준 비율은 그때 그 카메라 배치의
        # 값이라 지금 배치에 안 맞는다 — 다시 잰다.
        self._on_registered()

    # ---- runtime ------------------------------------------------------
    def run(self, frame):
        self._frame_count += 1
        if frame is not None:
            self._frame_shape = frame.shape[:2]
        cands = self.detector.detect(frame)
        self.last_cands = cands
        owner_id = self.matcher.match(cands, frame)
        if owner_id is not None:
            owner = next(c for c in cands if c.track_id == owner_id)
            self.smoother.update(owner.cx, owner.cy, owner.area, FRAME_DT)
            self._last_owner = owner
            self._miss = 0
            self._exit_dir = None
            if self.pose is not None:
                self._last_posture = self.pose.classify(frame, owner.bbox)
                self.posture_gate.update(self._last_posture)
            if self._frame_count % CALIBRATION_INTERVAL == 0:
                self.matcher.calibrate(TargetMatcher._crop(frame, owner.bbox))
        else:
            if self._miss == 0 and self._last_owner is not None:
                # 놓친 **첫 순간**에만 방향을 래치한다. 매 tick 다시 재면 스무더의
                # 예측이 흘러가면서 분류가 바뀐다 — 옆으로 나갔던 것이 몇 프레임 뒤
                # 중앙으로 읽히는 식이다.
                h, w = self._frame_shape or (0, 0)
                self._exit_dir = classify_exit(
                    self._last_owner.bbox, self.smoother.velocity, w, h)
            self._miss += 1

    def get_latest(self):
        if self._last_owner is None:
            return None
        if self._miss == 0:
            pred = self.smoother.predict(PREDICT_DT)
            is_pred = False
        elif self._miss <= COAST_LIMIT and may_coast(self._exit_dir, self._last_posture):
            # 예측 추종은 **옆·중앙으로 사라졌을 때만** 한다. 아래(코앞·쓰러짐)나
            # 위(들어 올려짐)로 사라졌는데 마지막 위치로 계속 밀고 들어가면 들이받는다.
            pred = self.smoother.predict(self._miss * FRAME_DT)
            is_pred = True
        else:
            return None
        if pred is None:
            return None
        cx, cy, area = pred
        area = max(0.0, area)          # prediction can extrapolate area below 0
        return Detection(
            cx=cx, cy=cy, area=area, bbox=self._last_owner.bbox,
            track_id=self._last_owner.track_id, is_owner=True,
            confidence=self._last_owner.confidence, is_predicted=is_pred,
            posture=self._last_posture,
            motion_ok=self.posture_gate.allowed,
            camera=self.camera, camera_epoch=self.camera_epoch,
        )

    def reset(self):
        self.matcher.reset()
        self.smoother.reset()
        self.posture_gate.reset()
        self._last_owner = None
        self._miss = 0
        self._frame_count = 0
        self._reg_id = None
        self._reg_streak = 0
        self._last_posture = None
        self._exit_dir = None
