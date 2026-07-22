"""상태 메시지 → 보여줄 스타일. 시계 없이 판정한다.

## 왜 시계가 없나

예전 모델은 `frame(now)` 였고 경과 시간으로 애니메이션을 그렸다. 그러려면 노드가
계속 물어봐야 하고, 그게 20 Hz 렌더 루프였다. 지금은 상태가 **바뀔 때만** 그리므로
모델이 시간을 알 이유가 없다.
"""
from pathlib import Path

from pinky_led.led_state_model import LedStateModel
from pinky_led.state_led_config import NO_SIGNAL, load

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "led_state_map.yaml"


def _model():
    return LedStateModel(load(CONFIG_PATH))


def test_before_any_message_it_shows_the_no_signal_style():
    """부팅 직후 스트립이 검은 채로 남으면 '꺼진 로봇'과 구별되지 않는다."""
    assert _model().current_style().state == NO_SIGNAL


def test_a_received_state_is_shown():
    m = _model()
    assert m.on_state("PATROL") is True
    assert m.current_style().state == "PATROL"


def test_republishing_the_same_state_reports_no_change():
    """FSM 이 같은 상태를 20 Hz 로 재발행해도 다시 그리지 않게 하는 신호다.

    이걸 True 로 돌려주면 노드가 매번 스트립을 다시 쓰고, 깜빡임 위상까지 초기화돼
    LED 가 켜진 채로 굳는다.
    """
    m = _model()
    m.on_state("PATROL")
    assert m.on_state("PATROL") is False


def test_a_real_change_reports_change():
    m = _model()
    m.on_state("PATROL")
    assert m.on_state("WORKING") is True


def test_unknown_state_falls_back_instead_of_crashing():
    """모르는 문자열에 죽으면 LED 가 아니라 로봇이 멈춘다."""
    m = _model()
    m.on_state("맛있는_감자")
    assert m.current_style().state == NO_SIGNAL


def test_going_stale_switches_the_display():
    m = _model()
    m.on_state("PATROL")
    assert m.mark_stale() is True
    assert m.current_style().state == NO_SIGNAL


def test_staying_stale_reports_no_further_change():
    """이미 두절 표시 중인데 또 그리면 타이머만 헛돈다."""
    m = _model()
    m.on_state("PATROL")
    m.mark_stale()
    assert m.mark_stale() is False


def test_a_message_after_a_gap_recovers():
    m = _model()
    m.on_state("PATROL")
    m.mark_stale()
    assert m.on_state("PATROL") is True, "두절에서 돌아온 것도 변화다"
    assert m.current_style().state == "PATROL"


# ── 프레임 ───────────────────────────────────────────────────────────────────

def test_frame_on_and_off_differ_for_a_blinking_state():
    m = _model()
    m.on_state("ERROR")
    style = m.current_style()
    assert m.frame(style, on=True) != m.frame(style, on=False)


def test_frame_off_is_fully_dark():
    m = _model()
    m.on_state("ERROR")
    frame = m.frame(m.current_style(), on=False)
    assert set(frame) == {(0, 0, 0)}


def test_frame_covers_every_pixel():
    m = _model()
    m.on_state("PATROL")
    assert len(m.frame(m.current_style())) == m.config.num_pixels
