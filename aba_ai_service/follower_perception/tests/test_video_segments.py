import pytest

from scripts.video_segments import frame_in_segment, parse_labeled, parse_segment


def test_parse_mmss_range():
    assert parse_segment("0:03-0:33") == (3.0, 33.0)


def test_parse_seconds_range():
    assert parse_segment("3-33") == (3.0, 33.0)


def test_parse_hmmss_range():
    assert parse_segment("1:02:03-1:02:33") == (3723.0, 3753.0)


def test_reversed_range_is_rejected():
    with pytest.raises(ValueError, match="끝"):
        parse_segment("0:33-0:03")


def test_garbage_is_rejected():
    with pytest.raises(ValueError):
        parse_segment("나중에")


def test_zero_length_segment_is_legal():
    """한 순간을 가리키는 구간. 라벨 초안이 실제로 이 형태를 쓴다."""
    assert parse_segment("0:32-0:32") == (32.0, 32.0)


def test_parse_labeled():
    assert parse_labeled("front=0:03-0:33") == ("front", 3.0, 33.0)


def test_unlabeled_is_rejected():
    with pytest.raises(ValueError, match="label=시작-끝"):
        parse_labeled("0:03-0:33")


def test_frame_in_segment():
    assert frame_in_segment(45, 15.0, (3.0, 33.0)) is True     # 3.0s
    assert frame_in_segment(44, 15.0, (3.0, 33.0)) is False
    assert frame_in_segment(495, 15.0, (3.0, 33.0)) is True     # 33.0s
    assert frame_in_segment(496, 15.0, (3.0, 33.0)) is False
