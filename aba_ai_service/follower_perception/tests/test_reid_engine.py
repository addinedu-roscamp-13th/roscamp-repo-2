import numpy as np
from follower_perception.reid_engine import ReIDEngine


def _solid(color_bgr):
    img = np.zeros((64, 32, 3), dtype=np.uint8)
    img[:] = color_bgr
    return img


def test_colour_backend_normalized_vector():
    eng = ReIDEngine(backend='colour')
    v = eng.extract(_solid((0, 0, 255)))
    assert v.shape == (6,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_self_similarity_is_one():
    eng = ReIDEngine(backend='colour')
    v = eng.extract(_solid((0, 0, 255)))
    assert abs(eng.similarity(v, v) - 1.0) < 1e-5


def test_different_colours_less_similar_than_self():
    eng = ReIDEngine(backend='colour')
    red = eng.extract(_solid((0, 0, 255)))
    blue = eng.extract(_solid((255, 0, 0)))
    assert eng.similarity(red, blue) < eng.similarity(red, red)
