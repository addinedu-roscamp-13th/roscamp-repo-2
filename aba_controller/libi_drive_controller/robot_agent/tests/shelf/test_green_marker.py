"""초록 표식 중점. 합성 이미지로 색 임계만 본다."""
import numpy as np
import pytest

from app.shelf.green_marker import GreenConfig, centroid_u


def _canvas(h=240, w=320):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _paint(img, x0, x1, y0=100, y1=140, bgr=(120, 200, 40)):
    img[y0:y1, x0:x1] = bgr
    return img


def test_blank_frame_finds_nothing():
    assert centroid_u(_canvas()) is None


def test_none_frame_finds_nothing():
    assert centroid_u(None) is None


def test_green_bar_centroid_is_its_middle():
    u = centroid_u(_paint(_canvas(), 140, 180))
    assert u is not None
    assert 158.0 <= u <= 162.0


def test_offset_bar_moves_the_centroid():
    left = centroid_u(_paint(_canvas(), 40, 80))
    right = centroid_u(_paint(_canvas(), 240, 280))
    assert left is not None and right is not None
    assert left < 80.0 < 240.0 < right


def test_red_object_is_ignored():
    assert centroid_u(_paint(_canvas(), 140, 180, bgr=(20, 20, 220))) is None


def test_white_shelf_is_ignored():
    assert centroid_u(_paint(_canvas(), 0, 320, y0=0, y1=240,
                             bgr=(245, 245, 245))) is None


def test_speck_below_the_minimum_area_is_ignored():
    img = _paint(_canvas(), 160, 162, y0=120, y1=122)
    assert centroid_u(img) is None


def test_minimum_area_is_configurable():
    img = _paint(_canvas(), 160, 162, y0=120, y1=122)
    assert centroid_u(img, GreenConfig(min_area_px=1)) is not None


def test_largest_blob_wins():
    """둘 다 최소 면적을 넘긴 상태에서 큰 쪽이 이겨야 한다.

    ⚠️ 두 함정을 다 피해야 한다:
    1) 작은 쪽이 min_area_px 에 걸려 탈락하면 이 시험은 최소면적 필터만 재검증한다.
    2) 큰 쪽을 항상 나중에(오른쪽에) 두면, "가장 큰 것" 대신 "마지막으로 본 것"을
       고르는 결함도 같은 값을 내놓아 안 걸린다(연결요소 라벨이 스캔 순서라 오른쪽이
       늘 나중 라벨이라서다). 그래서 **큰 덩어리를 왼쪽(먼저 스캔되는 라벨)에** 둔다 —
       "마지막 라벨이 이긴다"는 결함이면 이 시험이 오른쪽(작은 쪽)을 골라 빨개진다.
    """
    img = _canvas()
    _paint(img, 20, 100, y0=100, y1=140)     # 80x40 = 3200px (큼, 왼쪽 → 라벨이 먼저)
    _paint(img, 200, 240, y0=100, y1=140)    # 40x40 = 1600px (작음, 오른쪽 → 라벨이 나중)
    u = centroid_u(img)
    assert u is not None and u < 100.0
