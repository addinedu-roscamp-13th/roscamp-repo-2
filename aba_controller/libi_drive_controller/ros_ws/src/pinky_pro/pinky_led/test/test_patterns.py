"""색 계산 — solid/blink 두 가지와 감마 보정.

## 여기서 지키는 것

애니메이션(breathing·flow)을 없앤 뒤로 그릴 그림은 상태당 **최대 2장**(ON/OFF)이다.
그래서 `render()` 에 시간 인자가 없다 — 이게 렌더 루프를 없앨 수 있었던 이유다.
`elapsed` 가 다시 생기면 20 Hz 루프도 같이 돌아온다.
"""
import inspect

from pinky_led import patterns

WHITE = (255, 255, 255)
BLUE = (0, 114, 178)        # #0072B2 청색


def test_solid_lights_every_pixel_the_same():
    assert patterns.render((10, 20, 30), num_pixels=4, gamma=1.0) == [(10, 20, 30)] * 4


def test_blink_off_is_completely_dark():
    """소등 구간에 중간 밝기를 쓰면 '깜빡임'이 '숨쉬기'로 보인다."""
    assert patterns.render(WHITE, on=False, num_pixels=3) == [(0, 0, 0)] * 3


def test_render_takes_no_clock():
    """시간 인자가 없다는 것 자체가 계약이다 — 있으면 렌더 루프가 필요해진다."""
    params = inspect.signature(patterns.render).parameters
    assert "elapsed" not in params and "period_sec" not in params


def test_only_two_patterns_exist():
    """세 번째 패턴은 대개 애니메이션이고, 그러면 루프가 돌아온다 — 늘리기 전에 멈춰 세운다."""
    assert patterns.PATTERNS == (patterns.SOLID, patterns.BLINK)


# ── 감마 ─────────────────────────────────────────────────────────────────────

def test_gamma_makes_low_levels_dimmer_than_linear():
    """PWM 은 선형인데 눈은 아니다. 15% 를 그대로 쓰면 IDLE 이 거의 안 보인다."""
    linear = patterns.render(WHITE, level=0.15, gamma=1.0, num_pixels=1)[0]
    corrected = patterns.render(WHITE, level=0.15, gamma=2.2, num_pixels=1)[0]
    assert corrected[0] < linear[0]


def test_gamma_does_not_touch_the_hue():
    """색상값에 감마를 걸면 팔레트의 색상비가 틀어져 파랑이 보라로 보인다.

    밝기에만 걸어야 어떤 휘도에서도 같은 색으로 읽힌다.
    """
    full = patterns.render(BLUE, level=1.0, gamma=2.2, num_pixels=1)[0]
    dim = patterns.render(BLUE, level=0.5, gamma=2.2, num_pixels=1)[0]
    assert full == BLUE, "최대 휘도에서는 팔레트 색 그대로여야 한다"
    # 8비트 양자화 때문에 아주 어두운 구간에서는 비가 조금 흔들린다 — 그건 감마 탓이 아니다.
    assert abs(dim[1] / dim[2] - BLUE[1] / BLUE[2]) < 0.03


# ── 휘도 손잡이 ──────────────────────────────────────────────────────────────

def test_full_brightness_is_the_colour_itself():
    assert patterns.render((230, 159, 0), num_pixels=1)[0] == (230, 159, 0)


def test_zero_level_is_off_not_a_negative_channel():
    assert patterns.render((255, 0, 0), level=0.0, num_pixels=1)[0] == (0, 0, 0)


def test_brightness_zero_turns_everything_off():
    """야간 조도 파라미터의 극단값 — 전역 brightness 하나로 전체가 꺼져야 한다."""
    assert patterns.render(WHITE, brightness=0.0, num_pixels=3) == [(0, 0, 0)] * 3


def test_global_brightness_multiplies_the_state_level():
    """야간 감광은 전역 손잡이 하나로 — 상태마다 값을 고치지 않는다."""
    bright = patterns.render(WHITE, level=1.0, brightness=1.0, gamma=1.0, num_pixels=1)[0]
    night = patterns.render(WHITE, level=1.0, brightness=0.3, gamma=1.0, num_pixels=1)[0]
    assert night[0] < bright[0]


def test_channels_stay_within_range():
    frame = patterns.render(WHITE, level=1.0, brightness=1.0, num_pixels=2)
    assert all(0 <= c <= 255 for pixel in frame for c in pixel)
