from libi_perception.detection_receiver import DetectionReceiver


def _dict(cx):
    return dict(cx=cx, cy=240.0, area=400.0, bbox=[0, 0, 20, 20],
                track_id=1, is_owner=True, confidence=0.9, is_predicted=False)


class _Source:
    def __init__(self, batches):
        self.batches = list(batches)
        self.i = 0

    def poll(self):
        out = self.batches[self.i] if self.i < len(self.batches) else []
        self.i += 1
        return out


def test_latest_none_before_update():
    r = DetectionReceiver(_Source([]))
    assert r.latest() is None


def test_update_keeps_most_recent():
    r = DetectionReceiver(_Source([[_dict(1.0), _dict(2.0)]]))
    r.update()
    assert r.latest().cx == 2.0


def test_none_payload_clears_owner():
    r = DetectionReceiver(_Source([[_dict(1.0)], [None]]))
    r.update()
    assert r.latest().cx == 1.0
    r.update()
    assert r.latest() is None
