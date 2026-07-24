import os

import numpy as np
from follower_perception.detection import TrackedBox
from follower_perception.reid_engine import ReIDEngine
from follower_perception.mocks import MockDetector
from follower_perception.pipeline import FollowerPerception
from follower_perception import constants


def _frame(color_bgr, w=64, h=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def _full_box(tid, w=64, h=64):
    return TrackedBox(bbox=(0, 0, w, h), cx=w / 2, cy=h / 2, area=w * h,
                      track_id=tid, confidence=0.9)


def _perception(script):
    return FollowerPerception(detector=MockDetector(script),
                              reid=ReIDEngine(backend='colour'))


def test_register_requires_stable_frames():
    script = [[_full_box(1)]] * constants.REGISTRATION_STABLE_FRAMES
    p = _perception(script)
    red = _frame((0, 0, 255))
    results = [p.register(red) for _ in range(constants.REGISTRATION_STABLE_FRAMES)]
    assert results[-1] is True            # confirmed on the last stable frame
    assert results[0] is False            # not yet stable on frame 1


def test_run_then_get_latest_returns_owner():
    n = constants.REGISTRATION_STABLE_FRAMES
    script = [[_full_box(1)]] * (n + 1)
    p = _perception(script)
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    p.run(red)
    det = p.get_latest()
    assert det is not None
    assert det.is_owner is True
    assert det.is_predicted is False
    assert det.track_id == 1


def test_coasting_then_none():
    n = constants.REGISTRATION_STABLE_FRAMES
    # n stable frames to register, 1 run with owner, then misses (empty lists)
    script = [[_full_box(1)]] * (n + 1) + [[]] * (constants.COAST_LIMIT + 2)
    p = _perception(script)
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    p.run(red)                            # owner seen
    assert p.get_latest().is_predicted is False
    p.run(red)                            # first miss
    assert p.get_latest().is_predicted is True   # coasting
    for _ in range(constants.COAST_LIMIT + 1):
        p.run(red)
    assert p.get_latest() is None         # beyond coast limit


def test_reset_clears_everything():
    n = constants.REGISTRATION_STABLE_FRAMES
    p = _perception([[_full_box(1)]] * (n + 1))
    red = _frame((0, 0, 255))
    for _ in range(n):
        p.register(red)
    p.reset()
    assert p.get_latest() is None


def test_register_from_image_registers_central_person():
    p = _perception([])
    p.detector = MockDetector([[_full_box(1)]])
    box = p.register_from_image(_frame((0, 0, 255)))
    assert box is not None
    assert box.track_id == 1
    assert p.matcher.is_registered is True


def test_register_from_image_no_person_returns_none():
    p = _perception([])
    p.detector = MockDetector([[]])
    assert p.register_from_image(_frame((0, 0, 255))) is None
    assert p.matcher.is_registered is False


def test_save_profile_writes_folder(tmp_path):
    p = _perception([])
    p.detector = MockDetector([[_full_box(1)]])
    p.register_from_image(_frame((0, 0, 255)))
    d = str(tmp_path / "v1")
    p.save_profile(d, name="v1", source_image="x.jpg",
                   registered_at="2026-07-10T00:00:00")
    assert os.path.exists(os.path.join(d, "crop.jpg"))
    assert os.path.exists(os.path.join(d, "meta.json"))


def test_save_then_load_profile_round_trip(tmp_path):
    p = _perception([])
    p.detector = MockDetector([[_full_box(1)]])
    p.register_from_image(_frame((0, 0, 255)))
    d = str(tmp_path / "v1")
    p.save_profile(d, name="v1")

    p2 = _perception([])
    p2.load_profile(d)
    assert p2.matcher.is_registered is True
