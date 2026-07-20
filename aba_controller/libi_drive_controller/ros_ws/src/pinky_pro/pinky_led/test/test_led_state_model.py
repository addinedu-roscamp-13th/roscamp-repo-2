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
