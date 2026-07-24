import numpy as np
from scripts.udp_video import (
    resize_to_width, split_chunks, FrameReassembler,
)


def test_resize_to_width_keeps_aspect():
    r = resize_to_width(np.zeros((720, 1280, 3), np.uint8), 640)
    assert r.shape[1] == 640 and r.shape[0] == 360


def test_resize_noop_when_already_width():
    assert resize_to_width(np.zeros((360, 640, 3), np.uint8), 640).shape == (360, 640, 3)


def test_chunk_roundtrip():
    payload = bytes(range(256)) * 20            # 5120 bytes -> multiple chunks
    r = FrameReassembler()
    out = None
    for c in split_chunks(7, payload, chunk_size=1400):
        out = r.feed(c) or out
    assert out == payload


def test_out_of_order_reassembles():
    payload = b"x" * 4000
    r = FrameReassembler()
    out = None
    for c in reversed(split_chunks(3, payload, 1400)):
        out = r.feed(c) or out
    assert out == payload


def test_stale_older_frame_dropped():
    r = FrameReassembler()
    for c in split_chunks(5, b"newer", 1400):   # completes frame 5
        r.feed(c)
    res = None
    for c in split_chunks(4, b"older", 1400):   # frame 4 < 5 -> dropped
        res = r.feed(c) or res
    assert res is None


def test_sender_restart_resyncs():
    r = FrameReassembler()
    for c in split_chunks(500, b"old", 1400):   # advance _latest_done to 500
        r.feed(c)
    out = None
    for c in split_chunks(0, b"new", 1400):      # sender restarts at 0 -> must accept
        out = r.feed(c) or out
    assert out == b"new"


def test_newer_frame_supersedes_partial():
    r = FrameReassembler()
    older = split_chunks(1, b"a" * 3000, 1400)  # 3 chunks
    r.feed(older[0])                            # partial frame 1
    out = None
    for c in split_chunks(2, b"b" * 1000, 1400):  # frame 2 completes
        out = r.feed(c) or out
    assert out == b"b" * 1000
    res = None
    for c in older[1:]:                         # frame 1 now stale -> dropped
        res = r.feed(c) or res
    assert res is None
