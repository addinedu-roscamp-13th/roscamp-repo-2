"""색 매핑 YAML — 무엇을 강제로 지키는가.

## 이 파일이 지키는 규칙 셋

    ① 색   = 가용성   파랑 = 가용 / 초록 = 이용 불가 / 빨강 = 위험 (Okabe-Ito, 색각 이상 대비)
    ② 깜빡 = 주의     없음 → 1.5s → 0.5s. **깜빡이는 상태는 최대 2개**
    ③ 휘도 = 활성도   약함 = 쉬는 중 / 밝음 = 사람이 관여 중

②를 코드로 막아 두는 이유: 저배터리 경고·부팅 표시 같은 걸 넣을 때 깜빡임에 손대고
싶어진다. 세 개가 깜빡이는 순간 "깜빡임 = 주의"라는 뜻이 흐려지는데, **그걸 아무도
못 알아챈 채 배포된다.** 그래서 로더가 거절한다.
"""
from pathlib import Path

import pytest

from pinky_led import patterns
from pinky_led.state_led_config import (
    ConfigError, NO_SIGNAL, REQUIRED_STATES, load, parse,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "led_state_map.yaml"


def _cfg():
    return load(CONFIG_PATH)


def _doc(states=None, **root):
    base = {
        "brightness": 1.0, "num_pixels": 8, "gamma": 2.2, "state_timeout_sec": 5.0,
        "states": states if states is not None else {
            name: {"color": [1, 2, 3], "pattern": "solid",
                   "period_sec": 0.0, "level": 1.0}
            for name in list(REQUIRED_STATES) + [NO_SIGNAL]
        },
    }
    base.update(root)
    return {"led_state": base}


# ── 배포되는 설정 ────────────────────────────────────────────────────────────

def test_shipped_config_covers_all_eight_states_plus_no_signal():
    cfg = _cfg()
    for name in list(REQUIRED_STATES) + [NO_SIGNAL]:
        assert name in cfg.styles, name


def test_at_most_two_states_blink():
    """원칙 ②. 이게 깨지면 사람이 무엇이 급한지 알 수 없다."""
    blinkers = [n for n in _cfg().blinking_states if n != NO_SIGNAL]
    assert len(blinkers) <= 2, blinkers


def test_the_blink_periods_are_three_times_apart():
    """1.5s 와 0.5s. 1.0s 를 끼우면 곁눈으로 구분이 안 된다."""
    cfg = _cfg()
    periods = sorted({cfg.styles[n].period_sec for n in cfg.blinking_states})
    assert periods == [0.5, 1.5], periods


def test_error_blinks_fastest():
    """가장 급한 게 가장 빨라야 한다 — 반대면 사람이 반대로 읽는다.

    NO_SIGNAL 은 일부러 ERROR 와 같은 그림이라 비교 대상이 아니다.
    """
    cfg = _cfg()
    others = [cfg.styles[n].period_sec
              for n in cfg.blinking_states if n not in ("ERROR", NO_SIGNAL)]
    assert others, "비교할 다른 깜빡임이 하나는 있어야 이 시험이 의미가 있다"
    assert all(cfg.styles["ERROR"].period_sec < p for p in others)


def test_danger_colour_is_only_used_by_danger_states():
    """빨강 = 위험·접근 주의. 충전/고장/두절만 쓴다 — 가용(파랑)이나 이용 불가(초록)
    상태가 빨강을 쓰면 '빨강 = 위험'이라는 학습이 무너진다.

    충전과 고장은 같은 빨강을 공유하되 패턴(solid vs blink)으로 갈린다 — 색은 가용성을,
    패턴은 급함을 나타낸다는 새 설계에 맞춘 것이다.
    """
    cfg = _cfg()
    danger_color = cfg.styles["ERROR"].color
    danger_states = {"CHARGING", "ERROR", NO_SIGNAL}
    for name, style in cfg.styles.items():
        if name in danger_states:
            continue
        assert style.color != danger_color, f"{name} 가 위험색(빨강)을 쓴다: {style.color}"


def test_solid_states_have_no_period():
    """solid 에 주기가 남아 있으면 '언젠가 깜빡이게 할 값'으로 오해된다."""
    for name, style in _cfg().styles.items():
        if style.pattern == patterns.SOLID:
            assert style.period_sec == 0.0, name


def test_idle_is_the_dimmest():
    """원칙 ③ — 쉬는 중이 가장 어둡다."""
    cfg = _cfg()
    idle = cfg.styles["IDLE"].level
    assert all(idle <= s.level for n, s in cfg.styles.items() if n != "IDLE")


def test_night_patrol_is_dimmer_than_day_patrol_with_the_same_colour():
    """같은 일(순찰)이므로 색은 같고, 야간이라 어둡다 — 색이 아니라 휘도로 가른다."""
    cfg = _cfg()
    day, night = cfg.styles["PATROL"], cfg.styles["SECURITY_PATROL"]
    assert day.color == night.color
    assert night.level < day.level


def test_no_signal_looks_like_trouble():
    """관제가 로봇을 못 보는 상황은 사람이 가 봐야 하는 상황이다 — 고장과 다르지 않다."""
    cfg = _cfg()
    assert cfg.styles[NO_SIGNAL].signature() == cfg.styles["ERROR"].signature()


# ── 로더가 거절해야 하는 것 ──────────────────────────────────────────────────

def test_parse_rejects_a_third_blinking_state():
    """②를 잊고 늘리는 걸 코드가 막는다."""
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 0.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    for name in ("ERROR", "RETURNING", "CHARGING"):
        states[name] = {"color": [1, 2, 3], "pattern": "blink",
                        "period_sec": 0.5, "level": 1.0}
    with pytest.raises(ConfigError, match="깜빡이는 상태"):
        parse(_doc(states))


def test_parse_rejects_blink_without_a_period():
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 0.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    states["ERROR"] = {"color": [1, 2, 3], "pattern": "blink",
                       "period_sec": 0.0, "level": 1.0}
    with pytest.raises(ConfigError, match="period_sec"):
        parse(_doc(states))


def test_parse_rejects_solid_with_a_period():
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 0.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    states["IDLE"]["period_sec"] = 2.0
    with pytest.raises(ConfigError, match="period_sec"):
        parse(_doc(states))


def test_parse_rejects_unknown_pattern():
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 0.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    states["IDLE"]["pattern"] = "breathing"     # 없앤 패턴이다
    with pytest.raises(ConfigError, match="pattern"):
        parse(_doc(states))


def test_parse_rejects_missing_state():
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 0.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    del states["WORKING"]
    with pytest.raises(ConfigError, match="WORKING"):
        parse(_doc(states))


def test_parse_rejects_bad_gamma():
    with pytest.raises(ConfigError, match="gamma"):
        parse(_doc(gamma=0.0))


def test_gamma_defaults_when_absent():
    """예전 설정 파일에는 gamma 키가 없다 — 그것 때문에 로봇이 안 뜨면 안 된다."""
    doc = _doc()
    del doc["led_state"]["gamma"]
    assert parse(doc).gamma == 2.2


def test_changing_only_the_yaml_changes_the_output():
    """색을 바꾸는 데 코드 수정도 재빌드도 필요 없다는 계약."""
    states = {
        name: {"color": [1, 2, 3], "pattern": "solid", "period_sec": 0.0, "level": 1.0}
        for name in list(REQUIRED_STATES) + [NO_SIGNAL]
    }
    states["PATROL"]["color"] = [9, 9, 9]
    assert parse(_doc(states)).styles["PATROL"].color == (9, 9, 9)
