"""MinDwell — 상태를 최소 시간 붙잡아 관찰 가능하게 만드는 leaf.

여기서 지키는 건 두 가지다:
  ① 정상 이탈을 실제로 늦춘다 (그래야 LED·패널에 보인다)
  ② **한 번 통과한 뒤 다시 갇히지 않는다** — 이게 진짜 함정이다. 아래 참조.
"""
import py_trees
import pytest
from py_trees.common import Status

from libi_modes.blackboard import Keys
from libi_modes.branches import charging, security_patrol
from libi_modes.common.min_dwell import MinDwell


class FakeClock:
    """수동으로 감는 시계. 테스트가 실제로 기다리면 느려지고 불안정해진다."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class InstantDriver:
    """곧바로 성공을 보고하는 드라이버 — 문제를 일으켰던 실제 조건."""

    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1

    def poll(self):
        return "success"

    def stop(self):
        pass


PARAMS = {
    "battery": {"ready": 40, "charged": 80, "low": 15},
    "min_dwell_sec": 3.0,
}


# ── leaf 자체 ─────────────────────────────────────────────────────────────────

def test_holds_running_until_the_time_passes(seed, tick):
    clock = FakeClock()
    leaf = MinDwell(3.0, clock=clock)
    seed(**{Keys.CURRENT_MODE: "CHARGING"})
    assert tick(leaf) == Status.RUNNING
    clock.advance(2.9)
    assert tick(leaf) == Status.RUNNING
    clock.advance(0.1)
    assert tick(leaf) == Status.SUCCESS


def test_stays_success_after_it_first_passes(seed, tick):
    """`initialise()` 로 시간을 쟀다면 여기서 무너진다.

    py_trees 는 직전 status 가 RUNNING 이 **아니면** 매 tick `initialise()` 를 부른다.
    그래서 SUCCESS 를 한 번 돌려준 다음 tick 에 타이머가 되감기고, 상태는 3초를 영원히
    못 넘겨 갇힌다. 기준을 current_mode 변화에 두는 이유가 이것이다.
    """
    clock = FakeClock()
    leaf = MinDwell(3.0, clock=clock)
    seed(**{Keys.CURRENT_MODE: "CHARGING"})
    tick(leaf)
    clock.advance(3.0)
    assert tick(leaf) == Status.SUCCESS
    for _ in range(5):
        assert tick(leaf) == Status.SUCCESS


def test_restarts_when_the_state_changes(seed, tick):
    clock = FakeClock()
    leaf = MinDwell(3.0, clock=clock)
    client = seed(**{Keys.CURRENT_MODE: "CHARGING"})
    tick(leaf)
    clock.advance(5.0)
    assert tick(leaf) == Status.SUCCESS

    client.set(Keys.CURRENT_MODE, "IDLE")          # 다른 상태로 나갔다가
    assert tick(leaf) == Status.RUNNING
    clock.advance(5.0)
    assert tick(leaf) == Status.SUCCESS
    client.set(Keys.CURRENT_MODE, "CHARGING")      # 다시 들어오면 처음부터
    assert tick(leaf) == Status.RUNNING


def test_passes_when_the_mode_is_not_known_yet(tick):
    """부팅 직후 — 모르는 상태를 붙잡아 두지 않는다."""
    assert tick(MinDwell(3.0, clock=FakeClock())) == Status.SUCCESS


def test_zero_seconds_is_a_no_op(seed, tick):
    seed(**{Keys.CURRENT_MODE: "CHARGING"})
    assert tick(MinDwell(0.0, clock=FakeClock())) == Status.SUCCESS


# ── CHARGING ─────────────────────────────────────────────────────────────────

def test_charging_does_not_leave_on_the_first_tick(seed, tick, read):
    """증상 그대로: 이미 충전된 로봇을 CHARGING 으로 보내면 즉시 IDLE 로 빠졌다."""
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 100.0})
    assert tick(charging.create(PARAMS)) != Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_charging_still_leaves_once_the_dwell_is_over(seed, read):
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 100.0})
    root = charging.create(PARAMS)
    tree = py_trees.trees.BehaviourTree(root=root)
    tree.setup(timeout=15)
    tree.tick()
    assert read(Keys.CURRENT_MODE) == "CHARGING"

    # 브랜치는 실제 시계를 쓴다(주입 지점이 없다). 이 테스트에서만 잠깐 기다린다.
    import time
    time.sleep(PARAMS["min_dwell_sec"] + 0.05)
    tree.tick()
    assert read(Keys.CURRENT_MODE) == "IDLE"


def test_charging_below_threshold_does_not_wait_at_all(seed, tick, read):
    """원래 동작 보존 — 문턱 아래면 **첫 tick 에** 그냥 실패하고 기다린다.

    MinDwell 을 BatteryCheck 앞에 두면 여기서 RUNNING 이 나온다. 나갈 이유가 없을 때는
    붙잡을 이유도 없다.
    """
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 20.0})
    assert tick(charging.create(PARAMS)) == Status.FAILURE
    assert read(Keys.CURRENT_MODE) == "CHARGING"


def test_charging_fault_is_not_delayed(seed, tick, read):
    """⚠️ 고장은 지연 밖에 있어야 한다. 이게 깨지면 안전 문제다."""
    seed(**{Keys.CURRENT_MODE: "CHARGING", Keys.BATTERY_PERCENT: 100.0,
            Keys.FAULT: True})
    assert tick(charging.create(PARAMS)) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "ERROR"


# ── SECURITY_PATROL ──────────────────────────────────────────────────────────

def test_security_patrol_does_not_leave_on_the_first_tick(seed, tick, read):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 100.0})
    root = security_patrol.create(PARAMS, InstantDriver())
    assert tick(root) != Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "SECURITY_PATROL"


def test_security_patrol_stop_request_still_works_during_the_dwell(seed, tick, read):
    """운영자 정지는 지연에 막히면 안 된다 — watchdog 는 병렬로 계속 돈다."""
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 100.0,
            Keys.LAST_COMMAND: "stop_request"})
    root = security_patrol.create(PARAMS, InstantDriver())
    assert tick(root) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "IDLE"


@pytest.mark.parametrize("seconds", [0.0, 3.0])
def test_security_patrol_battery_low_is_not_delayed(seed, tick, read, seconds):
    seed(**{Keys.CURRENT_MODE: "SECURITY_PATROL", Keys.BATTERY_PERCENT: 10.0})
    params = dict(PARAMS, min_dwell_sec=seconds)
    assert tick(security_patrol.create(params, InstantDriver())) == Status.SUCCESS
    assert read(Keys.CURRENT_MODE) == "RETURNING"
