import json
import os

import numpy as np
import pytest
from follower_perception.detection import TrackedBox
from follower_perception.reid_engine import ReIDEngine
from follower_perception.target_matcher import TargetMatcher
from follower_perception import constants


def _frame(color_bgr, w=64, h=64):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def _full_box(tid, w=64, h=64):
    return TrackedBox(bbox=(0, 0, w, h), cx=w / 2, cy=h / 2, area=w * h,
                      track_id=tid, confidence=0.9)


def _matcher():
    return TargetMatcher(ReIDEngine(backend='colour'))


def test_not_registered_returns_none():
    m = _matcher()
    assert m.is_registered is False
    assert m.match([_full_box(1)], _frame((0, 0, 255))) is None


def test_matches_registered_colour_owner():
    m = _matcher()
    m.register(_frame((0, 0, 255)))          # red owner
    assert m.is_registered is True
    assert m.match([_full_box(1)], _frame((0, 0, 255))) == 1


def test_rejects_different_colour():
    m = _matcher()
    m.register(_frame((0, 0, 255)))          # red owner
    assert m.match([_full_box(9)], _frame((255, 0, 0))) is None  # blue candidate


def test_locks_safe_id_after_verify_frames():
    m = _matcher()
    m.register(_frame((0, 0, 255)))
    red = _frame((0, 0, 255))
    for _ in range(constants.VERIFY_FRAMES):
        m.match([_full_box(1)], red)
    assert m.safe_id == 1


def test_calibrate_grows_gallery_for_novel_view():
    m = _matcher()
    m.register(_frame((0, 0, 255)))
    start = len(m.gallery)
    m.calibrate(_frame((0, 200, 50)))        # different enough (sim < 0.85) -> append
    assert len(m.gallery) == start + 1


def test_reset_clears_registration():
    m = _matcher()
    m.register(_frame((0, 0, 255)))
    m.reset()
    assert m.is_registered is False
    assert m.safe_id is None


class _AlwaysSimReID:
    """ReID stub: extract returns a constant vector, similarity always 1.0.
    Isolates the HSV gate from ReID."""
    feat_dim = 3

    def extract(self, roi_bgr):
        return np.ones(3, dtype=np.float32)

    def similarity(self, a, b):
        return 1.0


def test_hsv_gate_honors_threshold():
    # A high HSV threshold rejects a differently-colored candidate even when
    # ReID passes (red-vs-blue solid HSV correlation ~0.47 < 0.9).
    m = TargetMatcher(_AlwaysSimReID(), hsv_threshold=0.9)
    m.register(_frame((0, 0, 255)))              # red template
    assert m.match([_full_box(1)], _frame((255, 0, 0))) is None  # blue -> HSV fails


def test_hsv_none_matches_despite_different_color():
    m = TargetMatcher(_AlwaysSimReID(), hsv_threshold=None)
    m.register(_frame((0, 0, 255)))              # red template
    # ReID always passes, HSV ignored -> blue candidate still matches
    assert m.match([_full_box(1)], _frame((255, 0, 0))) == 1


def test_last_sims_recorded_after_match():
    m = TargetMatcher(_AlwaysSimReID())
    m.register(_frame((0, 0, 255)))
    m.match([_full_box(1)], _frame((0, 0, 255)))
    assert m.last_reid_sim is not None
    assert m.last_hsv_sim is not None


def test_save_then_load_matches_same_color(tmp_path):
    d = str(tmp_path / "prof")
    m = TargetMatcher(ReIDEngine(backend="colour"))
    m.register(_frame((0, 0, 255)))
    m.save(d, crop_bgr=_frame((0, 0, 255)), meta={"name": "v1"})

    m2 = TargetMatcher(ReIDEngine(backend="colour"))
    m2.load(d)
    assert m2.is_registered is True
    assert m2.match([_full_box(1)], _frame((0, 0, 255))) == 1


def test_backend_mismatch_reextracts_from_crop(tmp_path):
    d = str(tmp_path / "prof")
    m = TargetMatcher(ReIDEngine(backend="colour"))
    m.register(_frame((0, 0, 255)))
    m.save(d, crop_bgr=_frame((0, 0, 255)), meta={"name": "v1"})
    # Forge a mismatching backend/feat_dim in meta.
    mp = os.path.join(d, "meta.json")
    meta = json.load(open(mp))
    meta["reid_backend"] = "osnet"
    meta["feat_dim"] = 512
    json.dump(meta, open(mp, "w"))

    m2 = TargetMatcher(ReIDEngine(backend="colour"))
    m2.load(d)                       # re-extracts from crop, no raise
    assert m2.is_registered is True
    assert m2.reid.similarity(m2.template_reid, m2.template_reid) == 1.0


def test_strict_load_raises_on_mismatch(tmp_path):
    d = str(tmp_path / "prof")
    m = TargetMatcher(ReIDEngine(backend="colour"))
    m.register(_frame((0, 0, 255)))
    m.save(d, crop_bgr=_frame((0, 0, 255)), meta={"name": "v1"})
    mp = os.path.join(d, "meta.json")
    meta = json.load(open(mp)); meta["feat_dim"] = 999
    json.dump(meta, open(mp, "w"))
    m2 = TargetMatcher(ReIDEngine(backend="colour"))
    with pytest.raises(ValueError):
        m2.load(d, strict=True)


def test_load_raises_when_crop_missing_and_mismatch(tmp_path):
    d = str(tmp_path / "prof")
    m = TargetMatcher(ReIDEngine(backend="colour"))
    m.register(_frame((0, 0, 255)))
    m.save(d, crop_bgr=_frame((0, 0, 255)), meta={"name": "v1"})
    mp = os.path.join(d, "meta.json")
    meta = json.load(open(mp)); meta["feat_dim"] = 999
    json.dump(meta, open(mp, "w"))
    os.remove(os.path.join(d, "crop.jpg"))
    m2 = TargetMatcher(ReIDEngine(backend="colour"))
    with pytest.raises((ValueError, FileNotFoundError)):
        m2.load(d)


def test_calibrate_ema_updates_hsv_toward_current():
    from follower_perception.color_hist import hist_similarity
    m = _matcher()
    m.register(_frame((0, 0, 255)))            # red template
    before = m.template_hsv.copy()
    m.calibrate(_frame((0, 60, 200)))          # owner under shifted lighting
    after = m.template_hsv
    assert not np.array_equal(before, after)   # HSV template moved (online update)
    assert hist_similarity(before, after) > 0.9  # but only ~10% blended (EMA)
