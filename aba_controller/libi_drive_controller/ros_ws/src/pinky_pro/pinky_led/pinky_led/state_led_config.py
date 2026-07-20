"""Loads and validates the state -> (colour, pattern, period, level) map.

This module is the only place that knows the file format, and the YAML file is the only
place that knows the colours — so changing a colour or a period never means touching code.

No ROS and no hardware here, so the whole mapping contract is unit-testable.
"""
import yaml

from pinky_led.patterns import PATTERNS

REQUIRED_STATES = (
    "CHARGING", "IDLE", "PATROL", "SECURITY_PATROL",
    "INTERACTING", "WORKING", "RETURNING", "ERROR",
)

#: Pseudo-state shown when the FSM state feed goes stale. Not one of the 8 real states.
NO_SIGNAL = "NO_SIGNAL"


class ConfigError(ValueError):
    """Raised when led_state_map.yaml is missing something or holds an impossible value."""


class StateStyle:
    __slots__ = ("state", "color", "pattern", "period_sec", "level")

    def __init__(self, state, color, pattern, period_sec, level):
        self.state = state
        self.color = color
        self.pattern = pattern
        self.period_sec = period_sec
        self.level = level

    def signature(self):
        """(pattern, period_sec, level) — what a colour-blind viewer can still tell apart."""
        return (self.pattern, self.period_sec, self.level)

    def __repr__(self):
        return (f"StateStyle({self.state}, {self.color}, {self.pattern}, "
                f"{self.period_sec}, {self.level})")


class LedStateConfig:
    def __init__(self, styles, brightness, num_pixels, state_timeout_sec):
        self.styles = styles
        self.brightness = brightness
        self.num_pixels = num_pixels
        self.state_timeout_sec = state_timeout_sec

    def style_for(self, state):
        """Style for `state`, falling back to NO_SIGNAL for anything unrecognised."""
        return self.styles.get(state, self.styles[NO_SIGNAL])


def _require(mapping, key, where):
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigError(f"missing '{key}' in {where}")
    return mapping[key]


def _parse_color(raw, where):
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ConfigError(f"{where}: color must be a 3-item [r, g, b] list, got {raw!r}")
    for channel in raw:
        if not isinstance(channel, int) or not 0 <= channel <= 255:
            raise ConfigError(f"{where}: colour channels must be ints 0-255, got {raw!r}")
    return tuple(raw)


def _parse_style(name, entry):
    where = f"led_state.states.{name}"

    pattern = _require(entry, "pattern", where)
    if pattern not in PATTERNS:
        raise ConfigError(f"{where}: unknown pattern {pattern!r}, expected one of {PATTERNS}")

    period_sec = float(_require(entry, "period_sec", where))
    if period_sec <= 0:
        raise ConfigError(f"{where}: period_sec must be > 0, got {period_sec}")

    level = float(_require(entry, "level", where))
    if not 0.0 <= level <= 1.0:
        raise ConfigError(f"{where}: level must be within 0.0-1.0, got {level}")

    color = _parse_color(_require(entry, "color", where), where)
    return StateStyle(name, color, pattern, period_sec, level)


def parse(doc):
    root = _require(doc, "led_state", "document root")

    brightness = float(_require(root, "brightness", "led_state"))
    if not 0.0 <= brightness <= 1.0:
        raise ConfigError(f"brightness must be within 0.0-1.0, got {brightness}")

    num_pixels = int(_require(root, "num_pixels", "led_state"))
    if num_pixels <= 0:
        raise ConfigError(f"num_pixels must be > 0, got {num_pixels}")

    state_timeout_sec = float(_require(root, "state_timeout_sec", "led_state"))
    if state_timeout_sec <= 0:
        raise ConfigError(f"state_timeout_sec must be > 0, got {state_timeout_sec}")

    raw_states = _require(root, "states", "led_state")
    styles = {}
    for name in list(REQUIRED_STATES) + [NO_SIGNAL]:
        if name not in raw_states:
            raise ConfigError(f"missing state '{name}' in led_state.states")
        styles[name] = _parse_style(name, raw_states[name])

    return LedStateConfig(styles, brightness, num_pixels, state_timeout_sec)


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return parse(yaml.safe_load(handle))
