import numpy as np
from follower_perception.color_hist import hsv_hist, hist_similarity


def _solid(color_bgr, size=(40, 40)):
    img = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def test_hist_shape_and_normalization():
    h = hsv_hist(_solid((0, 0, 255)))  # red
    assert h.shape == (48,)
    assert abs(float(h.sum()) - 1.0) < 1e-3


def test_identical_similarity_is_one():
    red = _solid((0, 0, 255))
    assert hist_similarity(hsv_hist(red), hsv_hist(red)) > 0.99


def test_different_colors_low_similarity():
    red = hsv_hist(_solid((0, 0, 255)))
    blue = hsv_hist(_solid((255, 0, 0)))
    assert hist_similarity(red, blue) < 0.5
