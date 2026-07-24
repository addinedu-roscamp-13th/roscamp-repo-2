import os

import numpy as np
from follower_perception.detector import Detector


class _Arr:
    def __init__(self, data):
        self._data = np.asarray(data)

    def cpu(self):
        return self

    def numpy(self):
        return self._data


class _Boxes:
    def __init__(self, xyxy, ids, confs):
        self.xyxy = _Arr(xyxy)
        self.id = _Arr(ids) if ids is not None else None
        self.conf = _Arr(confs)


class _Result:
    def __init__(self, boxes):
        self.boxes = boxes


def test_parse_returns_tracked_boxes():
    result = _Result(_Boxes(
        xyxy=[[10, 20, 30, 60]],   # w=20, h=40 -> area 800, cx 20, cy 40
        ids=[5],
        confs=[0.9],
    ))
    out = Detector._to_tracked_boxes(result)
    assert len(out) == 1
    tb = out[0]
    assert tb.track_id == 5
    assert tb.cx == 20.0
    assert tb.cy == 40.0
    assert tb.area == 800.0
    assert tb.bbox == (10.0, 20.0, 30.0, 60.0)


def test_parse_no_ids_returns_empty():
    result = _Result(_Boxes(xyxy=[[0, 0, 1, 1]], ids=None, confs=[0.5]))
    assert Detector._to_tracked_boxes(result) == []


def test_parse_none_boxes_returns_empty():
    assert Detector._to_tracked_boxes(_Result(None)) == []


from follower_perception.detector import default_weights_path, is_person_class0


def test_default_weights_prefers_env(monkeypatch):
    monkeypatch.setenv("FOLLOWER_WEIGHTS", "/custom/x.pt")
    assert default_weights_path() == "/custom/x.pt"


def test_default_weights_falls_back_when_absent(monkeypatch):
    monkeypatch.delenv("FOLLOWER_WEIGHTS", raising=False)
    monkeypatch.setattr("follower_perception.detector.os.path.exists", lambda p: False)
    assert default_weights_path() == "yolo11n.pt"


def test_default_weights_uses_package_relative_when_present(monkeypatch):
    monkeypatch.delenv("FOLLOWER_WEIGHTS", raising=False)
    monkeypatch.setattr("follower_perception.detector.os.path.exists", lambda p: True)
    got = default_weights_path()
    assert got.endswith("weights/best.pt")
    assert os.path.isabs(got)


def test_is_person_class0():
    assert is_person_class0({0: "person"}) is True
    assert is_person_class0({0: "Person", 1: "car"}) is True
    assert is_person_class0({0: "people", 1: "figure"}) is True   # custom best.pt
    assert is_person_class0({0: "car"}) is False
    assert is_person_class0({}) is False
