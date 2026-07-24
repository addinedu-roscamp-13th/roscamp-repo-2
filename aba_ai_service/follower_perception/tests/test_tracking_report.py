import numpy as np
from follower_perception.reid_engine import ReIDEngine
from follower_perception.mocks import MockDetector
from follower_perception.pipeline import FollowerPerception
from follower_perception.detection import TrackedBox
from follower_perception.tracking_report import track_frames, summarize


def _frame(color=(0, 0, 255), w=64, h=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def _full_box(tid, w=64, h=64):
    return TrackedBox(bbox=(0, 0, w, h), cx=w / 2, cy=h / 2, area=w * h,
                      track_id=tid, confidence=0.9)


def test_track_frames_owner_held_every_frame():
    n = 6
    p = FollowerPerception(detector=MockDetector([[_full_box(1)]] * n),
                           reid=ReIDEngine(backend="colour"))
    p.register_from_image(_frame((0, 0, 255)))
    p.detector = MockDetector([[_full_box(1)]] * n)   # reset script for tracking
    frames = [_frame((0, 0, 255)) for _ in range(n)]
    summary = track_frames(frames, p)
    assert summary["frames"] == n
    assert summary["owner_hold_ratio"] == 1.0
    assert summary["max_miss_streak"] == 0


def test_track_frames_reports_misses():
    n = 4
    p = FollowerPerception(detector=MockDetector([[_full_box(1)]]),
                           reid=ReIDEngine(backend="colour"))
    p.register_from_image(_frame((0, 0, 255)))
    # Owner never appears in tracking -> no owner frames, misses accumulate.
    p.detector = MockDetector([[]] * n)
    frames = [_frame((0, 0, 255)) for _ in range(n)]
    summary = track_frames(frames, p)
    assert summary["owner_frames"] == 0
    assert summary["owner_hold_ratio"] == 0.0
    assert summary["max_miss_streak"] == n


def test_summarize_counts_predicted_and_streaks():
    records = [
        {"owner": True, "predicted": False},
        {"owner": True, "predicted": True},
        {"owner": False, "predicted": False},
        {"owner": False, "predicted": False},
        {"owner": True, "predicted": False},
    ]
    s = summarize(records)
    assert s["frames"] == 5
    assert s["owner_frames"] == 3
    assert s["predicted_frames"] == 1
    assert s["max_miss_streak"] == 2
    assert abs(s["owner_hold_ratio"] - 0.6) < 1e-9
