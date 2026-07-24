# 상태별 LED 표시 패키지 Implementation Plan (3단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the driving Pi's LED strip show the robot's current FSM state (8종) by colour + motion pattern, driven by a ROS2 topic published from `libi_modes`, with every colour/period/brightness value living in a YAML file rather than in code.

**Architecture:** Three layers, split so the visual behaviour is testable without ROS2 or LED hardware. (a) `patterns.py` — pure maths: `(pattern, elapsed, colour, level, brightness, period, num_pixels) → per-pixel RGB list`. (b) `state_led_config.py` + `config/led_state_map.yaml` — the state→style mapping and its validation. (c) `led_state_model.py` — pure runtime model that turns state messages + a clock into a frame, including the stale-feed fallback. On top of those sits `state_led_node.py`, a thin rclpy shell that owns the real `LED` object and pushes `model.frame(now)` to the strip on a non-blocking timer. Layers (a)–(c) are unit-tested on the dev machine; only the shell needs the Pi.

**Tech Stack:** Python 3.12, ROS2 Jazzy (ament_python), `PyYAML` 6.0.1, `pytest` 7.4.4, `rpi_ws281x` (Pi only, via the existing `pinky_led.pinkyled.LED` wrapper).

## Global Constraints

- **Extend `pinky_led`, do not fork it.** INSTRUCTION.md 3단계 explicitly permits this: "기존 LED 제어 패키지를 응용해도 무방하다. 새로 만들 필요는 없다." All work lands in `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/`.
- **`pinkyled.py` and `led_server.py` are another author's files and are NOT modified.** `pinkyled.py`'s `LED` class is reused as-is. Only two files in that package receive **additive-only** edits (`package.xml` exec_depends, `setup.py` data_files + one console_script) — Task 4 flags these for user confirmation before they are made, per the project CLAUDE.md rule about touching files outside the request.
- **No blocking calls anywhere in the tick path.** `pinkyled.py`'s `color_wipe` / `theater_chase` / `rainbow` / `rainbowCycle` / `theaterChaseRainbow` all run `time.sleep()` loops; calling any of them from a ROS2 callback would freeze `rclpy.spin()` and stall every other callback on the node. They are never called. INSTRUCTION.md 구현 규칙: "블로킹 금지 — update() 내부에서 블로킹 호출 금지. tick 전체가 정지한다."
- **No hardcoded colours or periods.** Every colour, pattern, period, and level comes from `config/led_state_map.yaml` (INSTRUCTION.md 설계 규칙: "색상 매핑 하드코딩하지 말 것"). Code holds only pattern *shapes*, never state→colour knowledge.
- **Red is reserved for `ERROR`.** No other state may use a red-dominant colour. Enforced by a test, not just convention.
- **The state topic name is a ROS parameter, never a literal.** The mission-PC ROS domain and the topic name are still undecided (see "Deferred / open decisions"); nothing in this plan may bake in a guess.
- **The 8 states are exactly:** `CHARGING`, `IDLE`, `PATROL`, `SECURITY_PATROL`, `INTERACTING`, `WORKING`, `RETURNING`, `ERROR`. Plus one internal pseudo-state `NO_SIGNAL` for the stale-feed pattern.
- **Honest verification boundary.** Tasks 1–3 are fully verified by `pytest` on the dev machine. Task 4 touches `rclpy` and `rpi_ws281x`; `rpi_ws281x` is Pi-only and **cannot be exercised here**, so Task 4's LED behaviour is marked "verify on the Pi" and must not be reported as tested until it has actually run on hardware.
- ROS2 Jazzy is installed at `/opt/ros/jazzy` and `colcon` at `/usr/bin/colcon`, but neither is sourced by default — prefix ROS commands with `source /opt/ros/jazzy/setup.bash`. No apt installs are needed for this plan.
- Related plans: `2026-07-20-libi-modes-fsm-bt.md` (the FSM that publishes the state) and `2026-07-20-fms-fsm-bt-panel.md` (Stage 2 UI).

---

### Task 1: Pure pattern engine (`patterns.py`)

**Files:**
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/patterns.py`
- Test: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/test/test_patterns.py`

**Interfaces:**
- Consumes: nothing (leaf module — no ROS, no hardware, no I/O).
- Produces:
  - Pattern name constants `SOLID = "solid"`, `BREATHING = "breathing"`, `BLINK = "blink"`, `FLOW = "flow"`, and the tuple `PATTERNS`.
  - `render(pattern: str, elapsed: float, color: tuple, *, level: float = 1.0, brightness: float = 1.0, period_sec: float = 1.0, num_pixels: int = 8) -> list[tuple[int, int, int]]` — returns one clamped `(r, g, b)` per pixel. Raises `ValueError` on an unknown pattern or `period_sec <= 0`. Tasks 2–4 import only these names.

Speed differences from INSTRUCTION.md's draft table ("느린 호흡" vs "빠른 깜빡임") are expressed purely through `period_sec` from YAML, so the code needs only four pattern shapes, not eight.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_patterns.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led
python3 -m pytest test/test_patterns.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'pinky_led.patterns'`

- [ ] **Step 3: Implement**

```python
# pinky_led/patterns.py
"""Pure LED pattern maths — no ROS, no hardware, no I/O.

Each pattern maps (elapsed seconds, shape params) -> a per-pixel level in 0.0-1.0, which
render() then scales into RGB. Keeping this layer pure is what lets the whole visual
behaviour be unit-tested on a dev machine without rpi_ws281x.

Nothing here sleeps or blocks: state_led_node.py calls render() once per timer tick and
the call returns immediately. (INSTRUCTION.md 구현 규칙 — 블로킹 금지.) In particular this
module deliberately does NOT use pinkyled.py's color_wipe/theater_chase/rainbow helpers,
which drive their animation with time.sleep() loops and would stall rclpy.spin().

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_patterns.py -v`
Expected: `11 passed`

- [ ] **Step 5: Git**

```bash
git add aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/patterns.py \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/test/test_patterns.py
git commit -m "feat(pinky_led): add non-blocking LED pattern engine (solid/breathing/blink/flow)"
```

---

### Task 2: State→style map (`led_state_map.yaml` + `state_led_config.py`)

**Files:**
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/config/led_state_map.yaml`
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/state_led_config.py`
- Test: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/test/test_state_led_config.py`

**Interfaces:**
- Consumes: `patterns.PATTERNS` (Task 1) for pattern-name validation.
- Produces:
  - `REQUIRED_STATES` — the 8-state tuple; `NO_SIGNAL = "NO_SIGNAL"`.
  - `ConfigError(ValueError)`.
  - `StateStyle` with attributes `state`, `color: tuple`, `pattern: str`, `period_sec: float`, `level: float`, and `signature() -> (pattern, period_sec, level)` — the part of a style a colour-blind viewer can still tell apart.
  - `LedStateConfig` with `styles: dict[str, StateStyle]`, `brightness: float`, `num_pixels: int`, `state_timeout_sec: float`, and `style_for(state) -> StateStyle` (falls back to the `NO_SIGNAL` style for any unknown name).
  - `parse(doc: dict) -> LedStateConfig` and `load(path) -> LedStateConfig`. Task 3 imports `NO_SIGNAL` and `load`.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_state_led_config.py
from pathlib import Path

import pytest

from pinky_led import patterns
from pinky_led.state_led_config import (
    ConfigError, NO_SIGNAL, REQUIRED_STATES, load, parse,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "led_state_map.yaml"


def _cfg():
    return load(CONFIG_PATH)


def _minimal_doc():
    """A valid-but-boring document, used to test the validator's rejections in isolation."""
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 1.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    return {"led_state": {"brightness": 1.0, "num_pixels": 8,
                          "state_timeout_sec": 3.0, "states": states}}


def _is_reddish(color):
    red, green, blue = color
    return red > 150 and green < 100 and blue < 100


def test_shipped_config_covers_all_eight_states_plus_no_signal():
    cfg = _cfg()
    assert len(REQUIRED_STATES) == 8
    for state in REQUIRED_STATES:
        assert state in cfg.styles
    assert NO_SIGNAL in cfg.styles


def test_shipped_config_matches_the_instruction_draft_mapping():
    cfg = _cfg()
    assert cfg.styles["CHARGING"].pattern == patterns.BREATHING
    assert cfg.styles["IDLE"].pattern == patterns.SOLID
    assert cfg.styles["PATROL"].pattern == patterns.FLOW
    assert cfg.styles["SECURITY_PATROL"].pattern == patterns.BLINK
    assert cfg.styles["INTERACTING"].pattern == patterns.SOLID
    assert cfg.styles["WORKING"].pattern == patterns.FLOW
    assert cfg.styles["RETURNING"].pattern == patterns.BLINK
    assert cfg.styles["ERROR"].pattern == patterns.BLINK
    # 초안 표의 '느린/빠른' 관계가 실제 주기에 반영되어 있는지
    assert cfg.styles["PATROL"].period_sec > cfg.styles["WORKING"].period_sec
    assert cfg.styles["SECURITY_PATROL"].period_sec > cfg.styles["ERROR"].period_sec
    # IDLE 은 '약한 상시 점등', INTERACTING 은 '밝은 상시 점등'
    assert cfg.styles["IDLE"].level < cfg.styles["INTERACTING"].level


def test_red_is_reserved_for_error_only():
    cfg = _cfg()
    assert _is_reddish(cfg.styles["ERROR"].color)
    for name, style in cfg.styles.items():
        if name != "ERROR":
            assert not _is_reddish(style.color), (
                f"{name} uses a red-ish colour reserved for ERROR")


def test_every_state_is_distinguishable_without_colour():
    """색각 이상 대비 — 색을 못 봐도 (패턴, 주기, 밝기) 조합만으로 서로 구별되어야 한다."""
    cfg = _cfg()
    signatures = {name: style.signature() for name, style in cfg.styles.items()}
    assert len(set(signatures.values())) == len(signatures), (
        f"states share a motion signature: {signatures}")


def test_global_knobs_are_present_and_sane():
    cfg = _cfg()
    assert 0.0 <= cfg.brightness <= 1.0
    assert cfg.state_timeout_sec > 0
    assert cfg.num_pixels > 0


def test_style_for_unknown_state_falls_back_to_no_signal():
    cfg = _cfg()
    assert cfg.style_for("NOT_A_STATE") is cfg.styles[NO_SIGNAL]


def test_parse_rejects_missing_state():
    doc = _minimal_doc()
    del doc["led_state"]["states"]["ERROR"]
    with pytest.raises(ConfigError, match="ERROR"):
        parse(doc)


def test_parse_rejects_unknown_pattern():
    doc = _minimal_doc()
    doc["led_state"]["states"]["IDLE"]["pattern"] = "strobe"
    with pytest.raises(ConfigError, match="strobe"):
        parse(doc)


def test_parse_rejects_bad_colour_and_bad_period():
    doc = _minimal_doc()
    doc["led_state"]["states"]["IDLE"]["color"] = [1, 2]
    with pytest.raises(ConfigError):
        parse(doc)

    doc = _minimal_doc()
    doc["led_state"]["states"]["IDLE"]["period_sec"] = 0
    with pytest.raises(ConfigError):
        parse(doc)


def test_changing_only_the_yaml_changes_the_output():
    """검수 기준: '파라미터 파일 수정만으로 색상·주기를 바꿀 수 있을 것' — 코드는 그대로."""
    doc = _minimal_doc()
    doc["led_state"]["states"]["IDLE"]["color"] = [10, 20, 30]
    doc["led_state"]["states"]["IDLE"]["period_sec"] = 9.5
    cfg = parse(doc)
    assert cfg.styles["IDLE"].color == (10, 20, 30)
    assert cfg.styles["IDLE"].period_sec == 9.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_state_led_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pinky_led.state_led_config'`

- [ ] **Step 3: Create `config/led_state_map.yaml`**

```yaml
# 상태 -> LED 색상·패턴 매핑.
# 이 파일만 고치면 색·주기·밝기가 바뀐다 (코드 수정 불필요 — INSTRUCTION.md 설계 규칙).
#
#   pattern    : solid | breathing | blink | flow
#   period_sec : 한 사이클 길이(초). '느린/빠른'은 전부 이 값으로 표현한다.
#                solid 에서는 쓰이지 않지만 값은 있어야 한다.
#   level      : 그 상태의 상대 밝기(0.0-1.0). 전역 brightness 와 곱해진다.
#
# 빨강(red-dominant)은 ERROR 전용이다. 다른 상태에 쓰면 테스트가 실패한다.
# 색각 이상 대비: 어떤 두 상태도 (pattern, period_sec, level) 조합이 겹치면 안 된다.
led_state:
  brightness: 1.0          # 전역 밝기. 야간 순찰 시 낮춘다 (예: 0.3)
  num_pixels: 8            # pinkyled.LED 기본 픽셀 수
  state_timeout_sec: 3.0   # 이 시간 넘게 상태 토픽이 없으면 NO_SIGNAL 패턴

  states:
    CHARGING:        { color: [  0, 200,  60], pattern: breathing, period_sec: 4.0,  level: 0.9  }  # 초록 · 느린 호흡
    IDLE:            { color: [255, 255, 255], pattern: solid,     period_sec: 1.0,  level: 0.25 }  # 흰색 · 약한 상시 점등
    PATROL:          { color: [  0,  90, 255], pattern: flow,      period_sec: 3.0,  level: 0.8  }  # 파랑 · 느린 흐름
    SECURITY_PATROL: { color: [150,   0, 220], pattern: blink,     period_sec: 2.0,  level: 0.7  }  # 보라 · 느린 깜빡임
    INTERACTING:     { color: [  0, 210, 210], pattern: solid,     period_sec: 1.0,  level: 1.0  }  # 청록 · 밝은 상시 점등
    WORKING:         { color: [255, 130,   0], pattern: flow,      period_sec: 0.8,  level: 0.9  }  # 주황 · 빠른 흐름
    RETURNING:       { color: [255, 210,   0], pattern: blink,     period_sec: 1.0,  level: 0.85 }  # 노랑 · 깜빡임
    ERROR:           { color: [255,   0,   0], pattern: blink,     period_sec: 0.25, level: 1.0  }  # 빨강 · 빠른 깜빡임
    NO_SIGNAL:       { color: [255, 255, 255], pattern: blink,     period_sec: 0.2,  level: 0.6  }  # 상태 미수신 · 흰색 빠른 깜빡임
```

- [ ] **Step 4: Implement `state_led_config.py`**

```python
# pinky_led/state_led_config.py
"""Loads and validates the state -> (colour, pattern, period, level) map.

This module is the only place that knows the file format, and the YAML file is the only
place that knows the colours — so changing a colour or a period never means touching code
(INSTRUCTION.md 설계 규칙: '색상 매핑 하드코딩하지 말 것').

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test/test_state_led_config.py -v`
Expected: `10 passed`

- [ ] **Step 6: Git**

```bash
git add aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/config/led_state_map.yaml \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/state_led_config.py \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/test/test_state_led_config.py
git commit -m "feat(pinky_led): add YAML state->LED style map with validation"
```

---

### Task 3: Runtime model (`led_state_model.py`) — state tracking + stale-feed fallback

**Files:**
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/led_state_model.py`
- Test: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/test/test_led_state_model.py`

**Interfaces:**
- Consumes: `patterns.render` (Task 1); `NO_SIGNAL`, `LedStateConfig` (Task 2).
- Produces: `LedStateModel(config)` with
  - `on_state(state: str, now: float)` — feed a received state message; restarts the pattern clock only when the state actually changes.
  - `resolve(now: float) -> (StateStyle, float)` — the style to show and its elapsed time, substituting the `NO_SIGNAL` style when the feed is stale.
  - `frame(now: float) -> list[tuple[int, int, int]]` — the pixel frame to push.
  - `active_state` property.

  Task 4's rclpy node holds one of these and does nothing else with timing logic.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_led_state_model.py
from pathlib import Path

from pinky_led.led_state_model import LedStateModel
from pinky_led.state_led_config import NO_SIGNAL, load

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "led_state_map.yaml"


def _model():
    return LedStateModel(load(CONFIG_PATH))


def test_before_any_message_it_shows_the_no_signal_pattern():
    """부팅 직후 아직 상태를 못 받았을 때도 뭔가는 보여야 한다."""
    style, _ = _model().resolve(now=0.0)
    assert style.state == NO_SIGNAL


def test_state_message_selects_that_states_style():
    model = _model()
    model.on_state("WORKING", now=100.0)
    style, elapsed = model.resolve(now=100.5)
    assert style.state == "WORKING"
    assert elapsed == 0.5


def test_transition_restarts_the_pattern_clock_immediately():
    """검수 기준: '상태 전이 시 지연 없이 즉시 반영' — 이전 패턴의 위상을 물려받으면 안 된다."""
    model = _model()
    model.on_state("PATROL", now=10.0)
    model.on_state("ERROR", now=13.0)
    style, elapsed = model.resolve(now=13.0)
    assert style.state == "ERROR"
    assert elapsed == 0.0


def test_repeated_same_state_does_not_restart_the_clock():
    """같은 상태가 20Hz 로 계속 들어와도 패턴이 매번 처음으로 되감기면 안 된다."""
    model = _model()
    model.on_state("PATROL", now=10.0)
    model.on_state("PATROL", now=11.0)
    _, elapsed = model.resolve(now=12.0)
    assert elapsed == 2.0


def test_stale_feed_falls_back_to_no_signal_and_recovers():
    model = _model()
    timeout = model.config.state_timeout_sec
    model.on_state("IDLE", now=0.0)
    assert model.resolve(now=timeout - 0.01)[0].state == "IDLE"
    assert model.resolve(now=timeout + 0.01)[0].state == NO_SIGNAL
    model.on_state("IDLE", now=100.0)
    assert model.resolve(now=100.0)[0].state == "IDLE"


def test_unknown_state_name_falls_back_to_no_signal():
    """오타나 미지의 상태를 받아도 조용히 꺼지지 말고 NO_SIGNAL 로 알린다."""
    model = _model()
    model.on_state("BANANA", now=5.0)
    assert model.resolve(now=5.0)[0].state == NO_SIGNAL


def test_frame_returns_one_rgb_tuple_per_pixel():
    model = _model()
    model.on_state("ERROR", now=0.0)
    frame = model.frame(now=0.0)
    assert len(frame) == model.config.num_pixels
    assert all(len(pixel) == 3 for pixel in frame)
    assert frame[0] == model.config.styles["ERROR"].color   # blink starts ON at full level
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_led_state_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pinky_led.led_state_model'`

- [ ] **Step 3: Implement**

```python
# pinky_led/led_state_model.py
"""Pure runtime model: state messages + a clock -> the pixel frame to show.

Holds no ROS handles and no LED handle, so the timeout fallback and the
transition-is-immediate behaviour are unit-testable without hardware.
state_led_node.py owns the rclpy bits and the real LED object and just feeds this.
"""
from pinky_led import patterns
from pinky_led.state_led_config import NO_SIGNAL


class LedStateModel:
    def __init__(self, config):
        self.config = config
        self._state = None
        self._state_since = None
        self._last_seen_at = None

    @property
    def active_state(self):
        return self._state

    def on_state(self, state, now):
        """Feed a received state message.

        The pattern clock restarts only on an actual change: that is what makes a
        transition show up immediately instead of inheriting the previous pattern's
        phase, while a state republished at 20 Hz doesn't rewind the animation.
        """
        self._last_seen_at = now
        if state != self._state:
            self._state = state
            self._state_since = now

    def resolve(self, now):
        """(style, elapsed) for `now`, substituting NO_SIGNAL when the feed goes stale.

        In the stale branch the raw clock is used as `elapsed` — NO_SIGNAL is a blink, so
        only its phase matters, and it keeps blinking for as long as the feed is missing.
        """
        stale = (self._last_seen_at is None
                 or now - self._last_seen_at > self.config.state_timeout_sec)
        if stale:
            return self.config.styles[NO_SIGNAL], now
        return self.config.style_for(self._state), now - self._state_since

    def frame(self, now):
        style, elapsed = self.resolve(now)
        return patterns.render(
            style.pattern,
            elapsed,
            style.color,
            level=style.level,
            brightness=self.config.brightness,
            period_sec=style.period_sec,
            num_pixels=self.config.num_pixels,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_led_state_model.py -v`
Expected: `7 passed`

- [ ] **Step 5: Run the whole pure-logic suite together**

Run: `python3 -m pytest test/test_patterns.py test/test_state_led_config.py test/test_led_state_model.py -v`
Expected: `28 passed` (11 + 10 + 7). If the count drifts, recount with `python3 -m pytest test/ --collect-only -q` and reconcile before treating the run as green — do not just update the number.

Note: the package's pre-existing `test/test_copyright.py`, `test/test_flake8.py`, `test/test_pep257.py` are another author's ament linters and are deliberately not run or modified here.

- [ ] **Step 6: Git**

```bash
git add aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/led_state_model.py \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/test/test_led_state_model.py
git commit -m "feat(pinky_led): add LED state model with stale-feed fallback"
```

---

### Task 4: ROS2 node, launch file, package wiring, README

⚠️ **This task edits two files owned by another author** (`package.xml`, `setup.py` — additive only) **and adds a node that can only be verified on the Pi.** Per the project CLAUDE.md rule ("요청/계획에 없던 파일을 바꿔야 할 상황이 생기면 … 이유를 설명한 뒤 사용자 확인을 받고 나서 바꾼다"), confirm with the user before Step 4. The justification: an ament_python package cannot expose a new executable or install a new config/launch directory without those two entries; there is no way to ship this node otherwise.

**Files:**
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/state_led_node.py`
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/launch/state_led.launch.xml`
- Create: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/README.md`
- Modify: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/package.xml` (add exec_depends only)
- Modify: `aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/setup.py` (add two data_files entries + one console_script)

**Interfaces:**
- Consumes: `LedStateModel` (Task 3), `state_led_config.load` (Task 2), and the existing `pinky_led.pinkyled.LED`.
- Produces: console script `state_led` (`pinky_led.state_led_node:main`) with ROS parameters `config_path` (required), `state_topic` (default `fsm_state`), `tick_hz` (default `20.0`). Subscribes `std_msgs/String` on `state_topic`.

- [ ] **Step 1: Implement the node**

```python
# pinky_led/state_led_node.py
"""FSM 상태 토픽을 구독해 LED 색상·패턴을 출력하는 노드.

이 파일은 얇은 껍데기다 — 패턴 계산은 patterns.py, 매핑은 state_led_config.py,
상태·타임아웃 판정은 led_state_model.py 가 맡으며 셋 다 ROS·하드웨어 없이 테스트된다.
여기서는 rclpy 배선과 실제 LED 쓰기만 한다.

주의 1 (블로킹 금지): pinkyled.LED 의 color_wipe / theater_chase / rainbow / rainbowCycle /
theaterChaseRainbow 는 내부에서 time.sleep() 루프를 돈다. 콜백에서 호출하면 rclpy.spin()
전체가 멈춰 다른 콜백이 하나도 실행되지 않는다. 절대 쓰지 않는다 — 매 tick 계산된 프레임을
set_pixel() + show() 로 한 번에 밀어넣는 논블로킹 방식만 사용한다.

주의 2 (LED 점유): rpi_ws281x 는 한 프로세스만 스트립을 소유할 수 있다. 같은 패키지의
led_server 와 이 노드를 동시에 띄울 수 없다. 둘 중 하나만 실행할 것 (README 참조).

주의 3 (root): pinkyled 모듈은 import 시점에 root 가 아니면 sudo 로 자기 자신을 재실행한다.
따라서 이 노드도 결국 root 권한으로 동작한다.
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from pinky_led.led_state_model import LedStateModel
from pinky_led.pinkyled import LED
from pinky_led.state_led_config import load


class StateLedNode(Node):
    def __init__(self):
        super().__init__("state_led")
        self.declare_parameter("config_path", "")
        self.declare_parameter("state_topic", "fsm_state")
        self.declare_parameter("tick_hz", 20.0)

        config_path = self.get_parameter("config_path").value
        if not config_path:
            raise RuntimeError("config_path parameter is required (path to led_state_map.yaml)")

        self.config = load(config_path)
        self.model = LedStateModel(self.config)
        self.led = LED(num=self.config.num_pixels)
        self._last_frame = None

        topic = self.get_parameter("state_topic").value
        self.create_subscription(String, topic, self._on_state, 10)

        tick_hz = float(self.get_parameter("tick_hz").value)
        self.create_timer(1.0 / tick_hz, self._tick)
        self.get_logger().info(f"state_led ready — topic '{topic}', {tick_hz:.0f} Hz")

    def _now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def _on_state(self, msg):
        self.model.on_state(msg.data.strip().upper(), self._now())

    def _tick(self):
        frame = self.model.frame(self._now())
        if frame == self._last_frame:
            return                       # nothing changed — skip the strip write
        for index, color in enumerate(frame):
            self.led.set_pixel(index, color)
        self.led.show()
        self._last_frame = frame


def main(args=None):
    rclpy.init(args=args)
    node = StateLedNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.led.clear()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create the launch file**

This is a **new launch file inside `pinky_led`**, not an edit to `pinky_bringup/launch/bringup_robot.launch.xml`. Reason: that bringup file belongs to a different package and currently starts only `bringup` and `battery_publisher`; adding an LED node there would (a) modify another package outside the request, and (b) force the LED node on every robot that runs bringup, including ones where `led_server` is wanted instead. Shipping our own launch file keeps the decision at the operator's hands. The README documents the one-line `<include>` for whoever decides to wire it into bringup later.

```xml
<launch>
  <!-- 상태별 LED 표시 노드.

       ⚠️ pinky_led 의 led_server 와 동시에 띄우면 rpi_ws281x 스트립 점유가 충돌한다.
          둘 중 하나만 실행할 것.
       ⚠️ state_topic 은 libi_modes 가 발행하는 상태 토픽 이름에 맞춰야 한다 (미확정 — README 참조).
  -->
  <arg name="config_path" default="$(find-pkg-share pinky_led)/config/led_state_map.yaml"/>
  <arg name="state_topic" default="fsm_state"/>
  <arg name="tick_hz" default="20.0"/>

  <node pkg="pinky_led" exec="state_led" name="state_led" output="screen">
    <param name="config_path" value="$(var config_path)"/>
    <param name="state_topic" value="$(var state_topic)"/>
    <param name="tick_hz" value="$(var tick_hz)"/>
  </node>
</launch>
```

- [ ] **Step 3: Write the package README**

```markdown
# pinky_led

Pinky 로봇의 WS281x LED 스트립 제어 패키지.

## 노드 두 개 — 동시에 띄울 수 없다

`rpi_ws281x` 는 한 프로세스만 스트립을 점유할 수 있으므로 **아래 둘 중 하나만** 실행한다.

| 노드 | 실행 | 역할 |
|---|---|---|
| `led_server` | `ros2 run pinky_led led_server` | `set_led` / `set_brightness` 서비스로 수동 제어 (기존) |
| `state_led` | `ros2 launch pinky_led state_led.launch.xml` | FSM 상태 토픽을 구독해 자동으로 색·패턴 출력 (신규) |

## state_led

`libi_modes` 가 발행하는 상태(`std_msgs/String`)를 구독해 LED 를 바꾼다.

```bash
ros2 launch pinky_led state_led.launch.xml state_topic:=<실제_토픽명>
```

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `config_path` | `<share>/config/led_state_map.yaml` | 상태→색·패턴 매핑 파일 |
| `state_topic` | `fsm_state` | ⚠️ 미확정 — libi_modes 발행 토픽명으로 맞출 것 |
| `tick_hz` | `20.0` | 패턴 갱신 주기 |

### 색·패턴 바꾸기

`config/led_state_map.yaml` 만 고치면 된다. 코드 수정은 필요 없다.
야간 순찰 시 조도를 낮추려면 `brightness` 를 내린다 (예: `0.3`).

제약 두 가지 — 어기면 테스트가 실패한다.
- **빨강은 `ERROR` 전용.** 다른 상태에 빨강 계열을 쓸 수 없다.
- **어떤 두 상태도 `(pattern, period_sec, level)` 조합이 같으면 안 된다.** 색각 이상
  이용자도 움직임만으로 상태를 구분할 수 있어야 하기 때문이다.

상태 토픽이 `state_timeout_sec` 이상 끊기면 흰색 빠른 깜빡임(`NO_SIGNAL`)으로 바뀐다.

### bringup 에 넣으려면

현재 `pinky_bringup/launch/bringup_robot.launch.xml` 에는 LED 노드가 등록되어 있지 않다.
자동 기동이 필요하면 그 파일에 아래 한 줄을 추가한다 (다른 패키지 수정이므로 담당자 확인 후).

```xml
<include file="$(find-pkg-share pinky_led)/launch/state_led.launch.xml"/>
```

## 테스트

순수 로직(패턴 계산·매핑·타임아웃)은 ROS2·하드웨어 없이 돌아간다.

```bash
python3 -m pytest test/test_patterns.py test/test_state_led_config.py test/test_led_state_model.py -v
```

`state_led_node.py` 자체는 실물 Pi 에서만 검증 가능하다.
```

- [ ] **Step 4: Additive edits to `package.xml` and `setup.py`** (get user confirmation first — see the warning at the top of this task)

In `package.xml`, add these four lines after the `<license>` line (leave everything else untouched):

```xml
  <exec_depend>rclpy</exec_depend>
  <exec_depend>std_msgs</exec_depend>
  <exec_depend>python3-yaml</exec_depend>
```

In `setup.py`, extend `data_files` and `console_scripts` (leave `maintainer`, `version`, `license`, and the existing `led_server` entry exactly as they are):

```python
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/led_state_map.yaml']),
        ('share/' + package_name + '/launch', ['launch/state_led.launch.xml']),
    ],
```

```python
    entry_points={
        'console_scripts': [
            'led_server=pinky_led.led_server:main',
            'state_led=pinky_led.state_led_node:main',
        ],
    },
```

- [ ] **Step 5: Build**

```bash
source /opt/ros/jazzy/setup.bash
cd aba_controller/libi_drive_controller/ros_ws
colcon build --symlink-install --packages-select pinky_led
```
Expected: `Finished <<< pinky_led`, no errors.

Note: the build machine does not need `rpi_ws281x` — `colcon build` only installs files and does not import `pinkyled.py`. Importing it (i.e. actually running the node) requires the Pi.

- [ ] **Step 6: Verify the installed layout**

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 pkg executables pinky_led
ls install/pinky_led/share/pinky_led/config install/pinky_led/share/pinky_led/launch
```
Expected: executables list contains both `led_server` and `state_led`; the share directories contain `led_state_map.yaml` and `state_led.launch.xml`.

- [ ] **Step 7: Hardware verification — Pi only, NOT verifiable on the dev machine**

Run on the driving Pi, with `led_server` **stopped**:

```bash
ros2 launch pinky_led state_led.launch.xml state_topic:=<실제_토픽명>
# in another shell, drive each state manually:
ros2 topic pub -1 /<실제_토픽명> std_msgs/String "data: 'CHARGING'"
```

- [ ] All 8 states produce visibly distinct output (photograph each — INSTRUCTION.md 검수 기준: "8종 상태 전부에 대해 LED 출력이 확인될 것")
- [ ] A transition is reflected immediately, not at the end of the previous pattern cycle
- [ ] Editing `led_state_map.yaml` and relaunching changes colour/period with no code edit
- [ ] Lowering `brightness` visibly dims the strip (night-patrol case)
- [ ] Stopping the publisher for `state_timeout_sec` switches to the white fast blink, and resuming recovers
- [ ] `Ctrl-C` clears the strip (the `finally: node.led.clear()` path)
- [ ] Running for 1h+ shows no stutter, drift, or memory growth
- [ ] Confirm what actually happens if `led_server` and `state_led` are started together, and record it in the README

**Do not mark this task complete until Step 7 has actually run on hardware.** Steps 1–6 only prove the package builds and installs.

- [ ] **Step 8: Git**

```bash
git add aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/pinky_led/state_led_node.py \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/launch/state_led.launch.xml \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/README.md \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/package.xml \
        aba_controller/libi_drive_controller/ros_ws/src/pinky_pro/pinky_led/setup.py
git commit -m "feat(pinky_led): add state-driven LED node, launch file and README"
```

---

## Requirements coverage (INSTRUCTION.md 3단계)

| # | 요구사항 | 커버 |
|---|---|---|
| 1 | FSM 상태 토픽 구독 → LED 색상·패턴 출력 | Task 3 (model) + Task 4 (subscription, node) |
| 2 | 상태 → 색상·패턴 매핑 테이블 기반 | Task 2 (`led_state_map.yaml`, `LedStateConfig`) |
| 3 | 색상 매핑 하드코딩 금지 · YAML 분리 | Task 2 (`test_changing_only_the_yaml_changes_the_output`) |
| 4 | 빨강은 ERROR 전용 | Task 2 (`test_red_is_reserved_for_error_only`) |
| 5 | 밝기 파라미터 (야간 조도) | Task 1 (`brightness` in `render`), Task 2 (global knob), Task 4 Step 7 |
| 6 | 상태 미수신 시 별도 패턴 | Task 3 (`test_stale_feed_falls_back_to_no_signal_and_recovers`) |
| 7 | 색각 이상 고려 — 패턴 병용 | Task 2 (`test_every_state_is_distinguishable_without_colour`) |
| 8 | 초안 매핑 표 그대로 반영 | Task 2 (`test_shipped_config_matches_the_instruction_draft_mapping`) |
| 9 | 검수 기준 (8종 출력 / 즉시 반영 / 파일만 고쳐 변경) | Task 3 (`test_transition_restarts_the_pattern_clock_immediately`), Task 2 (YAML-only change), Task 4 Step 7 (8종 육안 확인) |

Every requirement has a task. Requirements 1, 5, and 9's hardware halves are honestly deferred to Task 4 Step 7 rather than claimed by a unit test.

---

## Deferred / open decisions

1. **미션 PC 의 `ROS_DOMAIN_ID` 와 상태 토픽 이름** — `libi_modes` 는 새 도메인에서 돌고, 주행 Pi(도메인 88)까지는 FMS 쪽 `ros2 domain_bridge` 설정으로 중계된다 (`aba_fms_service/config/domain_bridge_pinky1.yaml` 이 이미 `reversed: True` 로 역방향 중계를 하는 것과 같은 패턴). 도메인 번호와 토픽명이 아직 정해지지 않아 `state_topic` 을 파라미터로만 두었고 기본값 `fsm_state` 는 **자리표시자**다. 확정되면 브릿지 YAML 에 이 토픽을 86→88 로 추가해야 한다.
2. **`led_server.py` 를 계속 운영할지** — `rpi_ws281x` 점유가 배타적이라 `state_led` 와 공존할 수 없다. 수동 제어를 실운영에서도 써야 한다면 `state_led` 가 LED 를 소유하고 `led_server` 를 그 앞단 프록시로 바꾸는 설계가 추가로 필요하다. 그 경우 `led_server.py` 를 수정해야 하므로 별도 합의가 필요하다.
3. **bringup 자동 기동** — Task 4 는 `pinky_led` 안에 자체 launch 파일을 두는 쪽을 택했다. `pinky_bringup/launch/bringup_robot.launch.xml` 에 `<include>` 를 넣어 자동 기동시킬지는 그 패키지 담당자의 확인이 필요하다.
4. **하드웨어 전용 검증** — Task 4 Step 7 전체(8종 육안 확인, 즉시 반영, 야간 밝기, 타임아웃 복구, 장시간 안정성, 두 노드 동시 실행 시 실제 거동)는 실물 Pi 에서만 가능하다. 개발 머신에는 `rpi_ws281x` 가 없다.
5. **`num_pixels` 실측** — YAML 기본값 `8` 은 `pinkyled.LED` 의 기본 인자를 따랐다. 실제 스트립 픽셀 수가 다르면 이 값을 맞춰야 한다.
