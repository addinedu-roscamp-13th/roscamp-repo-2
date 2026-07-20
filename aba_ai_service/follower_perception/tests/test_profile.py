import numpy as np
import pytest
from follower_perception.profile import save_profile, load_profile


def _crop(color_bgr=(0, 0, 255), h=40, w=20):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def test_round_trip_preserves_arrays_and_meta(tmp_path):
    d = str(tmp_path / "p1")
    reid = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    hsv = np.arange(48, dtype=np.float32)
    gallery = [reid, reid * 0.5]
    meta = {"name": "visitor1", "reid_backend": "colour", "feat_dim": 3}
    save_profile(d, crop_bgr=_crop(), reid_vec=reid, hsv_vec=hsv,
                 gallery=gallery, meta=meta)

    got = load_profile(d)
    assert got["meta"]["name"] == "visitor1"
    assert got["meta"]["feat_dim"] == 3
    np.testing.assert_allclose(got["reid"], reid, rtol=1e-6)
    np.testing.assert_allclose(got["hsv"], hsv, rtol=1e-6)
    assert got["gallery"].shape == (2, 3)
    assert got["crop"].shape == (40, 20, 3)


def test_load_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile(str(tmp_path / "nope"))
