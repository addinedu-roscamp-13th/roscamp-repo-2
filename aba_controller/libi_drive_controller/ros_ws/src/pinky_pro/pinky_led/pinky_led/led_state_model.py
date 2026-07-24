"""순수 런타임 모델: 상태 메시지 → 보여줄 픽셀 프레임.

ROS 핸들도 LED 핸들도 들고 있지 않아서, 두절 폴백과 "전이는 즉시 반영" 규칙을
하드웨어 없이 단위 시험할 수 있다. rclpy 배선과 실제 LED 객체는 state_led_node.py 가 쥔다.

## 왜 시계를 안 받게 됐나

예전엔 노드가 20 Hz 로 `frame(now)` 를 부르고, 모델이 경과 시간으로 애니메이션
(breathing·flow)을 계산했다. 그러려면 **렌더 루프가 필수**다 — solid 상태에서도
초당 20번 깨어난다.

지금 패턴은 solid 와 blink 둘뿐이고, blink 는 ON/OFF 두 그림밖에 없다. 그래서 시간을
받아 계산할 이유가 없다: 노드가 "지금 ON 이야/OFF 야"만 알려주면 된다.
계산을 최적화한 게 아니라 **계산할 것 자체가 없어졌다.**
"""
from pinky_led import patterns
from pinky_led.state_led_config import NO_SIGNAL


class LedStateModel:
    def __init__(self, config):
        self.config = config
        self._state = None
        self._stale = True          # 아직 아무것도 못 받았다 = 두절과 같다

    @property
    def active_state(self):
        return self._state

    def on_state(self, state):
        """상태 메시지 하나를 넣는다. 실제로 **바뀌었으면** True.

        같은 상태가 20 Hz 로 재발행돼도 False 를 돌려주므로, 노드는 바뀔 때만 그린다.
        """
        # 두절에서 돌아온 것도 **변화**다. 안 그러면 토픽이 같은 상태로 복귀했을 때
        # LED 가 두절 표시(빨강 깜빡임)에 갇힌다 — 로봇은 멀쩡한데 고장으로 보인다.
        recovered = self._stale
        self._stale = False
        if state == self._state and not recovered:
            return False
        self._state = state
        return True

    def mark_stale(self):
        """상태 토픽이 끊겼다고 표시한다. 표시가 실제로 바뀌었으면 True."""
        if self._stale:
            return False
        self._stale = True
        return True

    def current_style(self):
        """지금 보여줄 스타일. 두절이면 두절 표시로 대체한다."""
        if self._stale:
            return self.config.styles[NO_SIGNAL]
        return self.config.style_for(self._state)

    def frame(self, style, *, on=True):
        """그 스타일의 픽셀 프레임. `on=False` 는 깜빡임의 소등 구간이다."""
        return patterns.render(
            style.color,
            on=on,
            level=style.level,
            brightness=self.config.brightness,
            gamma=self.config.gamma,
            num_pixels=self.config.num_pixels,
        )
