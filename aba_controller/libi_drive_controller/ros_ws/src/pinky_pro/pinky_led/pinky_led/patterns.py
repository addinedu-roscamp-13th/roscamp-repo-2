"""순수 LED 색 계산 — ROS 도 하드웨어도 I/O 도 없다.

## 패턴은 solid 와 blink 둘뿐이다

예전엔 breathing 과 flow 도 있었다. 지우고 나서 좋아진 게 두 가지다:

**① CPU.** 애니메이션은 매 프레임 값이 달라지므로 **렌더 루프가 필수**다. 20 Hz 타이머가
항상 돌았고, LED 가 대부분의 시간을 보내는 solid 상태에서도 초당 20번 깨어나 아무 일도
안 하고 돌아갔다. 같은 Pi 에서 nav2 와 YOLO 가 도는 걸 생각하면 공짜가 아니다.
solid/blink 만 남으니 그릴 그림이 상태당 최대 2장이라 **루프 자체가 없어졌다.**

**② 읽기.** 깜빡임에 "주의"라는 뜻을 주려면 깜빡이는 상태가 적어야 한다. 애니메이션이
섞이면 사람이 "빠른 흐름"과 "느린 깜빡임"을 구별하느라 화면을 오래 봐야 한다.

    없음(solid)  →  1.5s 깜빡임  →  0.5s 깜빡임
    평소             복귀 중         고장

3배씩 벌어져 있어서 곁눈으로도 구분된다. **깜빡이는 상태를 3개 이상 만들지 말 것** —
그 순간 이 축이 무너진다. 새 정보는 색이나 휘도로 표현한다.

## 감마 보정

PWM 은 선형인데 사람 눈은 아니다. 15% 를 그대로 쓰면 IDLE 이 거의 안 보인다.

    out = color * (level * brightness) ** gamma

색상값에는 감마를 걸지 않는다 — 걸면 팔레트의 색상비가 틀어져 파랑이 보라로 보이는
식으로 어긋난다. **밝기에만** 건다.
"""

SOLID = "solid"
BLINK = "blink"

#: YAML 이 쓸 수 있는 패턴. 여기 없는 이름은 설정 로드 단계에서 거절된다.
PATTERNS = (SOLID, BLINK)

OFF = (0, 0, 0)


def apply_gamma(gain, gamma):
    """밝기 배수에 감마를 적용한다. 0 이하는 그대로 0(완전 소등)."""
    if gain <= 0.0:
        return 0.0
    return gain ** gamma


def render(color, *, on=True, level=1.0, brightness=1.0, gamma=2.2, num_pixels=8):
    """픽셀별 RGB 목록.

    color      — 그 상태의 색 (r, g, b) 0-255
    on         — 깜빡임의 점등 구간인가. False 면 **완전 소등**(중간 밝기를 쓰지 않는다)
    level      — 그 상태의 상대 휘도 (YAML, 상태별)
    brightness — 전역 휘도 손잡이 (YAML, 하나. 야간에 낮춘다)
    gamma      — 감마 보정 지수 (YAML)
    """
    if num_pixels <= 0:
        raise ValueError(f"num_pixels must be > 0, got {num_pixels}")
    if not on:
        return [OFF] * num_pixels
    gain = apply_gamma(level * brightness, gamma)
    scaled = tuple(max(0, min(255, int(round(channel * gain)))) for channel in color)
    return [scaled] * num_pixels
