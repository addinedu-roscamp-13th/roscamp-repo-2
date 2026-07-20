from follower_perception.detection import TrackedBox
from follower_perception.mocks import MockDetector


def _box(tid):
    return TrackedBox(bbox=(0, 0, 10, 10), cx=5.0, cy=5.0, area=100.0,
                      track_id=tid, confidence=0.9)


def test_returns_scripted_lists_in_order():
    det = MockDetector([[_box(1)], [], [_box(2)]])
    assert det.detect(None)[0].track_id == 1
    assert det.detect(None) == []
    assert det.detect(None)[0].track_id == 2


def test_returns_empty_after_script_exhausted():
    det = MockDetector([[_box(1)]])
    det.detect(None)
    assert det.detect(None) == []
