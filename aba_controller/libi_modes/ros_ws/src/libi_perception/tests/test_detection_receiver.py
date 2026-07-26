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


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_stale_detection_expires():
    """소스가 죽으면(poll 이 빈 리스트) 마지막 검출을 영원히 붙들면 안 된다.

    이게 없으면 miss 카운터가 안 오르고, 로봇은 이미 없는 사람을 계속 추종한다.
    회복 BT 도 영원히 안 돈다.
    """
    clock = _Clock()
    r = DetectionReceiver(_Source([[_dict(1.0)]]), ttl_sec=1.0, now=clock)
    r.update()
    assert r.latest().cx == 1.0

    clock.t = 0.9
    r.update()                      # poll() 은 이제 빈 리스트를 준다
    assert r.latest() is not None, "TTL 이내에는 유지돼야 한다"

    clock.t = 1.1
    r.update()
    assert r.latest() is None, "TTL 초과 — 유령을 쫓지 않는다"


def test_fresh_payload_refreshes_stamp():
    clock = _Clock()
    r = DetectionReceiver(_Source([[_dict(1.0)], [], [_dict(2.0)]]), ttl_sec=1.0, now=clock)
    r.update()
    clock.t = 0.8
    r.update()                      # 빈 poll — 스탬프 갱신 없음
    clock.t = 0.9
    r.update()                      # 새 payload — 스탬프 갱신
    clock.t = 1.7
    assert r.latest().cx == 2.0, "새 payload 를 받았으면 그 시각부터 다시 센다"
