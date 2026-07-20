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
