import numpy as np
from follower_perception.detection import TrackedBox
from follower_perception.reid_engine import ReIDEngine
from follower_perception.mocks import MockDetector
from follower_perception.pipeline import FollowerPerception
from follower_perception.ai_server import AiServer, detection_to_dict
from follower_perception import constants


def _frame(color_bgr, w=64, h=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def _full_box(tid, w=64, h=64):
    return TrackedBox(bbox=(0, 0, w, h), cx=w / 2, cy=h / 2, area=w * h,
                      track_id=tid, confidence=0.9)


class _FrameSource:
    def __init__(self, items):
        self.items = list(items)
        self.i = 0

    def next(self):
        if self.i >= len(self.items):
            return None
        item = self.items[self.i]
        self.i += 1
        return item


class _CommandSource:
    def __init__(self, per_call):
        self.per_call = list(per_call)   # list of list[dict]
        self.i = 0

    def poll(self):
        out = self.per_call[self.i] if self.i < len(self.per_call) else []
        self.i += 1
        return out


class _ResultSink:
    def __init__(self):
        self.sent = []

    def send(self, source_id, payload):
        self.sent.append((source_id, payload))


def test_detection_to_dict_none():
    assert detection_to_dict(None) is None


def test_register_then_track_emits_owner_detection():
    red = _frame((0, 0, 255))
    n = constants.REGISTRATION_STABLE_FRAMES
    # colour ReID perception fed a MockDetector that always sees owner id 1
    script = [[_full_box(1)]] * (n + 5)

    def make_perception():
        return FollowerPerception(detector=MockDetector(script),
                                  reid=ReIDEngine(backend='colour'))

    # n frames with a 'register' command, then a plain tracking frame
    frames = [("drive", red)] * (n + 1)
    commands = [[{"cmd": "register", "source": "drive"}]] * n + [[]]
    sink = _ResultSink()
    server = AiServer(_FrameSource(frames), sink, _CommandSource(commands),
                      make_perception=make_perception)
    for _ in range(n + 1):
        server.process_once()

    src, payload = sink.sent[-1]
    assert src == "drive"
    assert payload is not None
    assert payload["is_owner"] is True
    assert payload["track_id"] == 1


def test_two_sources_are_independent():
    red = _frame((0, 0, 255))

    def make_perception():
        return FollowerPerception(detector=MockDetector([[]] * 10),
                                  reid=ReIDEngine(backend='colour'))

    frames = [("drive", red), ("handy", red)]
    sink = _ResultSink()
    server = AiServer(_FrameSource(frames), sink, _CommandSource([[], []]),
                      make_perception=make_perception)
    server.process_once()
    server.process_once()
    sources = {s for s, _ in sink.sent}
    assert sources == {"drive", "handy"}
    # no registration -> no owner -> None payloads
    assert all(payload is None for _, payload in sink.sent)
