import socket
import numpy as np
import cv2
from follower_perception.detection import TrackedBox, Detection
from follower_perception.reid_engine import ReIDEngine
from follower_perception.mocks import MockDetector
from follower_perception.pipeline import FollowerPerception
from scripts.frame_proto import recv_frame
from scripts.perception_server import draw_overlay, serve_loop


def _frame(color=(0, 0, 255), w=64, h=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def _full_box(tid, w=64, h=64):
    return TrackedBox(bbox=(0, 0, w, h), cx=w / 2, cy=h / 2, area=w * h,
                      track_id=tid, confidence=0.9)


def test_draw_overlay_preserves_shape_owner_and_none():
    f = _frame()
    det = Detection(cx=32, cy=32, area=4096, bbox=(0, 0, 64, 64), track_id=1,
                    is_owner=True, confidence=0.9, is_predicted=False)
    out_owner = draw_overlay(f, det)
    out_none = draw_overlay(f, None)
    assert out_owner.shape == f.shape and out_owner.dtype == np.uint8
    assert out_none.shape == f.shape
    # owner overlay must differ from the plain-none overlay
    assert not np.array_equal(out_owner, out_none)


def test_serve_loop_streams_and_registers():
    a, b = socket.socketpair()
    frames = [_frame((0, 0, 255)) for _ in range(4)]
    perc = FollowerPerception(detector=MockDetector([[_full_box(1)]] * 4),
                              reid=ReIDEngine(backend="colour"))
    cmds = iter(["register"])

    def poll(conn):
        return next(cmds, None)

    serve_loop(a, frames, perc, poll_cmd=poll)
    a.close()   # EOF for client

    got = []
    while True:
        fr = recv_frame(b)
        if fr is None:
            break
        got.append(fr)
    b.close()

    assert len(got) == 4
    img = cv2.imdecode(np.frombuffer(got[0], np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (64, 64, 3)
    assert perc.matcher.is_registered is True
