import socket
import numpy as np
import cv2
from follower_perception.detection import TrackedBox, Detection
from follower_perception.reid_engine import ReIDEngine
from follower_perception.mocks import MockDetector
from follower_perception.pipeline import FollowerPerception
from scripts.frame_proto import recv_frame
from scripts import perception_server
from scripts.perception_server import (HUD_MIN_SCALE, _hud_scale, draw_overlay,
                                       serve_loop)


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


def test_hud_shrinks_with_frame_width():
    """HUD 치수가 프레임 폭을 따라간다.

    640 폭 기준 상수를 그대로 쓰면 320x240 스트림에서 글자가 화면의 두 배 비중을
    차지해 영상을 가린다. 기준 폭에서는 배율이 1 이라 예전 그림과 픽셀 단위로 같아야
    하고, 좁은 프레임에서는 줄되 하한 아래로는 안 내려가야 한다(못 읽게 된다).
    """
    assert _hud_scale(640) == 1.0                      # 기준 폭 = 예전과 동일
    assert _hud_scale(1280) == 2.0                     # 넓으면 같이 커진다
    assert _hud_scale(320) == HUD_MIN_SCALE            # 비례하면 0.5, 하한이 이긴다
    assert _hud_scale(64) == HUD_MIN_SCALE             # 하한 아래로는 안 내려간다

    # 배율만 맞고 그리기에 안 실리면 위 단언은 전부 통과한다. 실제로 잉크가 주는지 본다.
    # 비교 대상은 **같은 320 프레임을 배율 없이** 그린 것이다 — 640 과 비교하면
    # 프레임 면적까지 같이 줄어 무엇이 줄었는지 갈리지 않는다.
    cmd = {"linear_x": 0.1, "angular_z": 0.0, "drive": "fwd", "turn": "none",
           "state": "FOLLOWING"}

    def _ink(img):
        return int(np.count_nonzero(np.any(img != _frame(w=320, h=240), axis=2)))

    scaled = draw_overlay(_frame(w=320, h=240), None, cmd=cmd)
    old = perception_server.HUD_MIN_SCALE
    perception_server.HUD_MIN_SCALE = 1.0        # 하한을 올려 k=1 로 만든다(=예전 그림)
    try:
        unscaled = draw_overlay(_frame(w=320, h=240), None, cmd=cmd)
    finally:
        perception_server.HUD_MIN_SCALE = old
    assert _ink(scaled) < _ink(unscaled), (
        f"320 프레임에서 HUD 가 안 줄었다 (배율 적용 {_ink(scaled)} / "
        f"미적용 {_ink(unscaled)}) — 배율이 그리기에 안 실렸다")


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

    # 프레임마다 JPEG 하나 + POSE 하나가 같은 스트림에 섞여 나간다. 개수만 세면
    # 둘이 뒤바뀌어도 통과하므로 접두사로 갈라서 각각 확인한다.
    poses = [p for p in got if p.startswith(b"POSE ")]
    jpegs = [p for p in got if not p.startswith(b"POSE ")]
    assert len(jpegs) == 4
    assert len(poses) == 4
    img = cv2.imdecode(np.frombuffer(jpegs[0], np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (64, 64, 3)
    assert perc.matcher.is_registered is True
