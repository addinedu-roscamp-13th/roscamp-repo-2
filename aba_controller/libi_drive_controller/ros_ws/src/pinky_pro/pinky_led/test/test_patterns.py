import pytest

from pinky_led import patterns

WHITE = (255, 255, 255)
RED = (255, 0, 0)


def test_solid_is_constant_over_time():
    a = patterns.render(patterns.SOLID, 0.0, WHITE, period_sec=1.0, num_pixels=4)
    b = patterns.render(patterns.SOLID, 7.3, WHITE, period_sec=1.0, num_pixels=4)
    assert a == b == [WHITE] * 4


def test_level_and_brightness_multiply():
    full = patterns.render(patterns.SOLID, 0.0, WHITE, num_pixels=1)[0]
    half = patterns.render(patterns.SOLID, 0.0, WHITE, level=0.5, num_pixels=1)[0]
    dim = patterns.render(patterns.SOLID, 0.0, WHITE, level=0.5, brightness=0.5, num_pixels=1)[0]
    assert full == (255, 255, 255)
    assert full[0] > half[0] > dim[0]


def test_brightness_zero_turns_everything_off():
    """야간 조도 파라미터의 극단값 — 전역 brightness 하나로 전체가 꺼져야 한다."""
    assert patterns.render(patterns.SOLID, 0.0, WHITE, brightness=0.0, num_pixels=3) == [(0, 0, 0)] * 3


def test_breathing_dims_at_cycle_start_and_peaks_mid_cycle():
    period = 4.0
    start = patterns.render(patterns.BREATHING, 0.0, WHITE, period_sec=period, num_pixels=1)[0]
    mid = patterns.render(patterns.BREATHING, period / 2, WHITE, period_sec=period, num_pixels=1)[0]
    end = patterns.render(patterns.BREATHING, period, WHITE, period_sec=period, num_pixels=1)[0]
    assert start[0] < mid[0]
    assert start == end          # one full breath returns to where it began
    assert mid == WHITE


def test_breathing_never_goes_fully_dark():
    """호흡 패턴이 0까지 떨어지면 '꺼진 것'과 구분이 안 된다 — 최소 밝기 바닥이 있어야 한다."""
    darkest = patterns.render(patterns.BREATHING, 0.0, WHITE, period_sec=4.0, num_pixels=1)[0]
    assert darkest[0] > 0


def test_blink_alternates_on_and_off_within_one_period():
    on = patterns.render(patterns.BLINK, 0.0, RED, period_sec=1.0, num_pixels=2)
    off = patterns.render(patterns.BLINK, 0.6, RED, period_sec=1.0, num_pixels=2)
    assert on == [RED] * 2
    assert off == [(0, 0, 0)] * 2


def test_blink_period_controls_rate():
    """Same instant, different periods -> different phase. Proves period_sec drives the rate,
    so '느린 깜빡임' vs '빠른 깜빡임' is a YAML value and not a second pattern."""
    fast = patterns.render(patterns.BLINK, 0.2, RED, period_sec=0.25, num_pixels=1)
    slow = patterns.render(patterns.BLINK, 0.2, RED, period_sec=2.0, num_pixels=1)
    assert fast == [(0, 0, 0)]
    assert slow == [RED]


def test_flow_head_advances_with_time():
    period, n = 2.0, 8

    def head_index(t):
        frame = patterns.render(patterns.FLOW, t, WHITE, period_sec=period, num_pixels=n)
        return max(range(n), key=lambda i: frame[i])

    assert head_index(0.0) == 0
    assert head_index(period / n) == 1
    assert head_index(2 * period / n) == 2


def test_flow_completes_one_lap_per_period():
    period, n = 2.0, 8
    assert (patterns.render(patterns.FLOW, 0.0, WHITE, period_sec=period, num_pixels=n)
            == patterns.render(patterns.FLOW, period, WHITE, period_sec=period, num_pixels=n))


def test_flow_has_a_fading_tail_not_a_single_lit_pixel():
    frame = patterns.render(patterns.FLOW, 0.0, WHITE, period_sec=2.0, num_pixels=8)
    lit = [i for i, c in enumerate(frame) if c != (0, 0, 0)]
    assert len(lit) > 1
    assert frame[0] > frame[7] > frame[6]      # head brightest, tail fading behind it


def test_unknown_pattern_and_bad_period_raise():
    with pytest.raises(ValueError, match="disco"):
        patterns.render("disco", 0.0, WHITE)
    with pytest.raises(ValueError, match="period_sec"):
        patterns.render(patterns.SOLID, 0.0, WHITE, period_sec=0.0)
