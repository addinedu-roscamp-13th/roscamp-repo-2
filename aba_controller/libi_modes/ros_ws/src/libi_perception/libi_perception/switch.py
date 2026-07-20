"""Thin switch between the two follow behaviours.

Replaces the `transitions`-backed ControlFSM. INSTRUCTION.md rules out a separate FSM
library, and this is a 3-state selector — not a state machine worth a dependency.
Same states, same triggers, same rejection of illegal transitions; the only visible
difference is that the raised exception is InvalidTransition rather than
transitions.MachineError.

It exists to answer one question per tick: does the tracking action run, or the recovery
BT? Everything with real structure lives in those two, not here.
"""


class InvalidTransition(RuntimeError):
    """Raised when a trigger is not legal from the current state."""


TRACKING, SEARCHING, ENDED = 'TRACKING', 'SEARCHING', 'ENDED'

_TRANSITIONS = {
    ('lost', TRACKING): SEARCHING,
    ('reacquired', SEARCHING): TRACKING,
    ('search_failed', SEARCHING): ENDED,
    ('restart', ENDED): TRACKING,
}


class FollowSwitch:
    """TRACKING -> tracking action, SEARCHING -> recovery BT, ENDED -> neither."""

    def __init__(self):
        self.state = TRACKING

    def _fire(self, trigger):
        try:
            self.state = _TRANSITIONS[(trigger, self.state)]
        except KeyError:
            raise InvalidTransition(f"{trigger!r} is not valid from {self.state!r}") from None

    def lost(self):
        self._fire('lost')

    def reacquired(self):
        self._fire('reacquired')

    def search_failed(self):
        self._fire('search_failed')

    def restart(self):
        self._fire('restart')
