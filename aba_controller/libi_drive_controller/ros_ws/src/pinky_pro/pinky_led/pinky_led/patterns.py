"""Pure LED pattern maths — no ROS, no hardware, no I/O.

Each pattern maps (elapsed seconds, shape params) -> a per-pixel level in 0.0-1.0, which
render() then scales into RGB. Keeping this layer pure is what lets the whole visual
behaviour be unit-tested on a dev machine without rpi_ws281x.

Nothing here sleeps or blocks: state_led_node.py calls render() once per timer tick and
the call returns immediately. In particular this module deliberately does NOT use
pinkyled.py's color_wipe/theater_chase/rainbow helpers, which drive their animation with
time.sleep() loops and would stall rclpy.spin() along with every other callback.

'느린 호흡' vs '빠른 깜빡임' 같은 속도 차이는 전부 period_sec 로 표현한다 — 패턴 이름을
늘리지 않으므로 YAML 만 고쳐서 주기를 바꿀 수 있다.
"""
import math

SOLID = "solid"
BREATHING = "breathing"
BLINK = "blink"
FLOW = "flow"

PATTERNS = (SOLID, BREATHING, BLINK, FLOW)


def _scale(color, level):
    """(r, g, b) x level -> clamped integer RGB."""
    return tuple(max(0, min(255, int(round(channel * level)))) for channel in color)


def _solid_levels(elapsed, period_sec, num_pixels):
    return [1.0] * num_pixels


def _breathing_levels(elapsed, period_sec, num_pixels, min_level=0.15):
    """Cosine breath between min_level and 1.0, one full breath per period_sec.

    min_level keeps the strip visibly lit at the trough so 'breathing' never reads as 'off'.
    """
    phase = 0.5 * (1.0 - math.cos(2.0 * math.pi * (elapsed / period_sec)))
    return [min_level + (1.0 - min_level) * phase] * num_pixels


def _blink_levels(elapsed, period_sec, num_pixels, duty=0.5):
    on = (elapsed % period_sec) < (period_sec * duty)
    return [1.0 if on else 0.0] * num_pixels


def _flow_levels(elapsed, period_sec, num_pixels):
    """A comet head travelling one full lap per period_sec, with a linearly fading tail."""
    head = (elapsed / period_sec) * num_pixels
    tail = max(1.0, num_pixels / 2.0)
    levels = []
    for index in range(num_pixels):
        behind = (head - index) % num_pixels
        levels.append(max(0.0, 1.0 - behind / tail))
    return levels


_LEVELS = {
    SOLID: _solid_levels,
    BREATHING: _breathing_levels,
    BLINK: _blink_levels,
    FLOW: _flow_levels,
}


def render(pattern, elapsed, color, *, level=1.0, brightness=1.0,
           period_sec=1.0, num_pixels=8):
    """Per-pixel RGB for `pattern` at `elapsed` seconds into its cycle.

    level      — the state's own relative brightness (YAML, per state)
    brightness — the global brightness knob (YAML, one value; lower it for night patrol)
    """
    if pattern not in _LEVELS:
        raise ValueError(f"unknown pattern {pattern!r}, expected one of {PATTERNS}")
    if period_sec <= 0:
        raise ValueError(f"period_sec must be > 0, got {period_sec}")
    gain = level * brightness
    return [_scale(color, pixel_level * gain)
            for pixel_level in _LEVELS[pattern](elapsed, period_sec, num_pixels)]
