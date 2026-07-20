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
