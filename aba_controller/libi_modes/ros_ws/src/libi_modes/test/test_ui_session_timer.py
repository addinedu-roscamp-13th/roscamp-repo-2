import py_trees
from py_trees.common import Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys
from libi_modes.common.ui_session_timer import UiSessionTimer


class FakeClock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


def _reader(timer):
    client = timer.attach_blackboard_client(name="test-read")
    client.register_key(key=Keys.UI_LAST_TOUCH_AT, access=py_trees.common.Access.WRITE)
    client.register_key(key=Keys.INTERACTING_REMAINING, access=py_trees.common.Access.READ)
    return client


def test_remaining_counts_down_and_resets():
    clk = FakeClock()                          # t=100
    timer = UiSessionTimer(20.0, clock=clk)
    timer.setup()
    client = _reader(timer)

    client.set(Keys.UI_LAST_TOUCH_AT, 100.0)   # 진입을 유발한 터치 ≈ 진입 시각
    timer.tick_once()                          # INTERACTING 진입(t=100) → _entered_at=100
    clk.t = 105.0                              # 5초 경과
    timer.tick_once()
    assert timer.status == Status.RUNNING
    assert abs(bb.get(client, Keys.INTERACTING_REMAINING, default=-1) - 15.0) < 0.01

    client.set(Keys.UI_LAST_TOUCH_AT, 105.0)   # 재터치 → 리셋
    clk.t = 106.0
    timer.tick_once()
    assert abs(bb.get(client, Keys.INTERACTING_REMAINING, default=-1) - 19.0) < 0.01

    clk.t = 130.0                              # 무터치 20초 초과
    timer.tick_once()
    assert timer.status == Status.SUCCESS
    assert bb.get(client, Keys.INTERACTING_REMAINING, default=-1) == 0.0


def test_entry_latches_when_timestamp_missing_or_stale():
    """레이스 회귀: `ui_last_touch_at`(Float64)와 `ui_touch` 전이(fleet_cmd)는 순서보장 없는
    별도 토픽이라, 전이가 먼저 처리되면 진입 시점에 UI_LAST_TOUCH_AT 이 아직 0.0(첫 터치 전)
    이거나 과거값일 수 있다. 진입 시각을 바닥값으로 latch 하므로, 그래도 즉시 타임아웃되지
    않고 세션이 유지된다. (fix 전에는 elapsed = 100 - 0 = 100 >= 20 → 진입 즉시 SUCCESS.)"""
    clk = FakeClock()                          # t=100
    timer = UiSessionTimer(20.0, clock=clk)
    timer.setup()
    client = _reader(timer)

    client.set(Keys.UI_LAST_TOUCH_AT, 0.0)     # 스탬프 아직 미도착
    timer.tick_once()                          # 진입(t=100) → _entered_at=100
    assert timer.status == Status.RUNNING       # 즉시 타임아웃 X
    assert abs(bb.get(client, Keys.INTERACTING_REMAINING, default=-1) - 20.0) < 0.01

    clk.t = 110.0                              # 늦게 도착한 실제 터치가 진입보다 최신이면 그걸 씀
    client.set(Keys.UI_LAST_TOUCH_AT, 108.0)
    timer.tick_once()
    assert abs(bb.get(client, Keys.INTERACTING_REMAINING, default=-1) - 18.0) < 0.01
