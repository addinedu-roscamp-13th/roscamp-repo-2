"""패널 전이 유지 시간 — 사람이 누른 상태가 곧바로 사라지지 않게 한다.

여기서 지키는 것:
  ① 유지 중에는 BT 가 스스로 상태를 못 바꾼다
  ② 유지가 끝나면 그 전이가 **버려지지 않고** 그대로 일어난다
  ③ ERROR 는 유지 시간을 뚫는다 (고장은 못 기다린다)
  ④ 유지 시간 0 이면 예전과 똑같이 동작한다
"""
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.branches import charging, idle, patrol
from libi_modes.common.request_transition import RequestTransition

from test.fakes import PARAMS as FAKE_PARAMS
from test.fakes import FakeDriver

PARAMS = {"battery": {"ready": 40, "charged": 80, "low": 15}}


def _undock_gate():
    """`patrol.create` 가 필수로 받는 도킹 탈출 게이트 대역 (test_follow_exec `_gate` 와 같다).

    노드는 부모를 하나만 가지므로 **호출할 때마다 새로 만든다** — 하나를 돌려 쓰면
    두 번째 트리에 붙일 때 첫 트리에서 떨어져 나간다.
    """
    from libi_modes.common import undock
    return undock.create(FakeDriver(), distance_m=0.06, timeout_sec=8.0,
                         retry_max=3, now_fn=lambda: 0.0)


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


# ── 명령 유래 전이는 유지 시간을 뚫는다 (2026-07-30 회귀) ──────────────────────
#
# 실측: `manual_hold_sec` 이 2.0 → 300.0 이 된 뒤 관제가 배차해도 로봇이 IDLE 에 남았다.
# 유지 시간의 목적은 "로봇이 스스로 사람의 결정을 되돌리는 것"을 막는 것이지, 사람이 보낸
# 명령을 막는 것이 아니다. 그리고 PATROL·WORKING 에서 한 번 막힌 요청은 다음 tick 에
# 재시도할 통로가 없어 **미뤄지는 게 아니라 유실된다** (request_transition.py 클래스 주석).

def test_dispatch_breaks_through_the_hold(seed, tick, read, monkeypatch):
    """배차: IDLE 에서 task_assigned → WORKING. 유지 중이어도 통해야 한다."""
    clock = FakeClock()
    monkeypatch.setattr("libi_modes.common.request_transition.time.monotonic", clock)
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.BATTERY_PERCENT: 60.0,
            Keys.LAST_COMMAND: "task_assigned", Keys.HOLD_UNTIL: clock() + 999.0})
    assert tick(idle.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"


def test_panel_touch_breaks_through_the_hold(seed, tick, read, monkeypatch):
    """패널 터치: PATROL 에서 ui_touch → INTERACTING. 유지 중이어도 통해야 한다."""
    clock = FakeClock()
    monkeypatch.setattr("libi_modes.common.request_transition.time.monotonic", clock)
    seed(**{Keys.CURRENT_MODE: "PATROL", Keys.BATTERY_PERCENT: 60.0,
            Keys.ROBOT_POSE: None, Keys.LAST_COMMAND: "ui_touch",
            Keys.HOLD_UNTIL: clock() + 999.0})
    assert tick(patrol.create(FAKE_PARAMS, FakeDriver(),
                              undock_gate=_undock_gate())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "INTERACTING"


def test_autonomous_transition_is_still_held(seed, tick, read):
    """자율 전이는 여전히 막힌다 — 명령 표시가 **다른 목표**로 남아 있어도 마찬가지다."""
    clock = FakeClock()
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.NEXT_MODE: "IDLE",
            Keys.COMMANDED_MODE: "WORKING", Keys.HOLD_UNTIL: clock() + 999.0})
    assert tick(RequestTransition(clock=clock)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_marker_is_cleared_on_apply(seed, tick, read):
    """⚠️ 표시가 남으면 우연히 같은 목표를 노린 **자율** 전이까지 유지 시간을 뚫는다.
    적용하는 순간 지운다 (남은 tick 경계 청소는 main.py `_tick()` 이 맡는다)."""
    clock = FakeClock()
    seed(**{Keys.CURRENT_MODE: "IDLE", Keys.NEXT_MODE: "WORKING",
            Keys.COMMANDED_MODE: "WORKING", Keys.HOLD_UNTIL: clock() + 999.0})
    assert tick(RequestTransition(clock=clock)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "WORKING"
    assert read(Keys.COMMANDED_MODE) is None
