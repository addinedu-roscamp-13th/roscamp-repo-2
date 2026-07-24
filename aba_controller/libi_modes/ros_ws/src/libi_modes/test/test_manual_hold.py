"""패널 전이 유지 시간 — 사람이 누른 상태가 곧바로 사라지지 않게 한다.

여기서 지키는 것:
  ① 유지 중에는 BT 가 스스로 상태를 못 바꾼다
  ② 유지가 끝나면 그 전이가 **버려지지 않고** 그대로 일어난다
  ③ ERROR 는 유지 시간을 뚫는다 (고장은 못 기다린다)
  ④ 유지 시간 0 이면 예전과 똑같이 동작한다
"""
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.branches import charging
from libi_modes.common.request_transition import RequestTransition

PARAMS = {"battery": {"ready": 40, "charged": 80, "low": 15}}


class FakeClock:
    def __init__(self):
        self.now = 100.0        # 0 이 아닌 값 — 0 을 falsy 로 잘못 다루면 여기서 걸린다

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ── leaf ─────────────────────────────────────────────────────────────────────

def test_transition_is_deferred_while_held(seed, tick, read):
    clock = FakeClock()
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.NEXT_MODE: "IDLE",
            Keys.HOLD_UNTIL: clock() + 2.0})
    assert tick(RequestTransition(clock=clock)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_deferred_transition_is_not_lost(seed, tick, read):
    """미루는 것이지 버리는 게 아니다 — next_mode 가 남아 있어야 유지 후에 일어난다."""
    clock = FakeClock()
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.NEXT_MODE: "IDLE",
            Keys.HOLD_UNTIL: clock() + 2.0})
    leaf = RequestTransition(clock=clock)
    tick(leaf)
    assert read(Keys.NEXT_MODE) == "IDLE"          # 지워지지 않았다

    clock.advance(2.0)
    assert tick(leaf) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"
    assert read(Keys.NEXT_MODE) is None


def test_error_breaks_through_the_hold(seed, tick, read):
    """⚠️ 고장은 유지 시간과 무관하게 즉시 반영돼야 한다."""
    clock = FakeClock()
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.NEXT_MODE: "ERROR",
            Keys.HOLD_UNTIL: clock() + 999.0})
    assert tick(RequestTransition(clock=clock)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


def test_no_hold_behaves_exactly_as_before(seed, tick, read):
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.NEXT_MODE: "IDLE"})
    assert tick(RequestTransition(clock=FakeClock())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_expired_hold_does_not_block(seed, tick, read):
    clock = FakeClock()
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.NEXT_MODE: "IDLE",
            Keys.HOLD_UNTIL: clock() - 0.01})
    assert tick(RequestTransition(clock=clock)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


# ── 브랜치 전체 (증상 그대로) ─────────────────────────────────────────────────

def test_charging_stays_put_while_held(seed, tick, read, monkeypatch):
    """이미 충전된 로봇을 패널에서 CHARGING 으로 보냈을 때 — 예전엔 즉시 IDLE 이었다."""
    clock = FakeClock()
    monkeypatch.setattr("libi_modes.common.request_transition.time.monotonic", clock)
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 100.0,
            Keys.HOLD_UNTIL: clock() + 2.0})
    assert tick(charging.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "CHARGING"

    clock.advance(2.0)
    assert tick(charging.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_charging_fault_still_wins_during_the_hold(seed, tick, read, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("libi_modes.common.request_transition.time.monotonic", clock)
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 100.0,
            Keys.FAULT: True, Keys.HOLD_UNTIL: clock() + 999.0})
    assert tick(charging.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"
