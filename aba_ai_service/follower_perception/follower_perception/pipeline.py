from .detection import Detection
from .detector import Detector
from .reid_engine import ReIDEngine
from .target_matcher import TargetMatcher
from .bbox_smoother import BBoxSmoother
from .constants import (
    FRAME_DT, PREDICT_DT, COAST_LIMIT, CALIBRATION_INTERVAL,
    REGISTRATION_STABLE_FRAMES, REGISTRATION_MIN_AREA_RATIO,
)


class FollowerPerception:
    """Facade: frame -> Detection. Detect -> track -> identify -> smooth."""

    def __init__(self, detector=None, reid=None):
        self.detector = detector if detector is not None else Detector()
        self.reid = reid if reid is not None else ReIDEngine()
        self.matcher = TargetMatcher(self.reid)
        self.smoother = BBoxSmoother()
        self.last_cands = []          # raw YOLO detections from the last run()
        self._last_owner = None       # last TrackedBox seen as owner
        self._miss = 0
        self._frame_count = 0
        self._reg_id = None
        self._reg_streak = 0
        self._last_crop = None        # crop of the most recent registration
        self._last_bbox = None

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
            return True
        return False

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

    # ---- runtime ------------------------------------------------------
    def run(self, frame):
        self._frame_count += 1
        cands = self.detector.detect(frame)
        self.last_cands = cands
        owner_id = self.matcher.match(cands, frame)
        if owner_id is not None:
            owner = next(c for c in cands if c.track_id == owner_id)
            self.smoother.update(owner.cx, owner.cy, owner.area, FRAME_DT)
            self._last_owner = owner
            self._miss = 0
            if self._frame_count % CALIBRATION_INTERVAL == 0:
                self.matcher.calibrate(TargetMatcher._crop(frame, owner.bbox))
        else:
            self._miss += 1

    def get_latest(self):
        if self._last_owner is None:
            return None
        if self._miss == 0:
            pred = self.smoother.predict(PREDICT_DT)
            is_pred = False
        elif self._miss <= COAST_LIMIT:
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
        )

    def reset(self):
        self.matcher.reset()
        self.smoother.reset()
        self._last_owner = None
        self._miss = 0
        self._frame_count = 0
        self._reg_id = None
        self._reg_streak = 0
