from follower_perception.detection import Detection, TrackedBox


def test_detection_fields():
    d = Detection(cx=1.0, cy=2.0, area=100.0, bbox=(0, 0, 10, 10),
                  track_id=7, is_owner=True, confidence=0.9, is_predicted=False)
    assert d.cx == 1.0
    assert d.bbox == (0, 0, 10, 10)
    assert d.track_id == 7
    assert d.is_owner is True
    assert d.is_predicted is False


def test_tracked_box_fields():
    t = TrackedBox(bbox=(0, 0, 10, 20), cx=5.0, cy=10.0, area=200.0,
                   track_id=3, confidence=0.8)
    assert t.area == 200.0
    assert t.track_id == 3
