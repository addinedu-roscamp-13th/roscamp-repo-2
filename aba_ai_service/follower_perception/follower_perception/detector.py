import os

from .detection import TrackedBox
from .constants import MIN_CONFIDENCE


def default_weights_path():
    """Resolve YOLO weights portably: env override -> package-relative
    weights/best.pt if present -> stock yolo11n.pt (auto-download)."""
    env = os.environ.get("FOLLOWER_WEIGHTS")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", "weights", "best.pt"))
    if os.path.exists(candidate):
        return candidate
    return "yolo11n.pt"


PERSON_LABELS = {"person", "people", "pedestrian", "human"}


def is_person_class0(names):
    """True if class index 0 is a person-like class (person/people/…),
    case-insensitive. Custom weights may name it 'people' rather than 'person'."""
    if not isinstance(names, dict):
        return False
    return str(names.get(0, "")).lower() in PERSON_LABELS


class Detector:
    """YOLO11n detection + built-in ByteTrack. Person (class 0) only."""

    def __init__(self, weights=None, conf=MIN_CONFIDENCE,
                 tracker_cfg='bytetrack.yaml', device=None):
        from ultralytics import YOLO
        self.model = YOLO(weights or default_weights_path())
        self.conf = conf
        self.tracker_cfg = tracker_cfg
        self.device = device

    def detect(self, frame):
        results = self.model.track(
            frame, persist=True, conf=self.conf, classes=[0],
            tracker=self.tracker_cfg, verbose=False, device=self.device,
        )
        if not results:
            return []
        return self._to_tracked_boxes(results[0])

    @staticmethod
    def _to_tracked_boxes(result):
        boxes = getattr(result, 'boxes', None)
        if boxes is None or getattr(boxes, 'id', None) is None:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        ids = boxes.id.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        out = []
        for (x1, y1, x2, y2), tid, conf in zip(xyxy, ids, confs):
            x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
            w, h = x2 - x1, y2 - y1
            out.append(TrackedBox(
                bbox=(x1, y1, x2, y2),
                cx=(x1 + x2) / 2.0,
                cy=(y1 + y2) / 2.0,
                area=w * h,
                track_id=int(tid),
                confidence=float(conf),
            ))
        return out
