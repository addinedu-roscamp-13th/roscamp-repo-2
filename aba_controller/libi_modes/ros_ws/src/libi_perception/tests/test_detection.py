from libi_perception.detection import Detection, detection_from_dict


def _dict(**over):
    base = dict(cx=320.0, cy=240.0, area=400.0, bbox=[0, 0, 20, 20],
                track_id=1, is_owner=True, confidence=0.9, is_predicted=False)
    base.update(over)
    return base


def test_from_dict_none():
    assert detection_from_dict(None) is None


def test_from_dict_maps_fields():
    d = detection_from_dict(_dict())
    assert isinstance(d, Detection)
    assert d.cx == 320.0
    assert d.area == 400.0
    assert d.bbox == (0, 0, 20, 20)
    assert d.is_owner is True
    assert d.is_predicted is False
