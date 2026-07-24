import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class UiSessionTimer(py_trees.behaviour.Behaviour):
    """RUNNING while a visitor is using the touch panel; SUCCESS + next_mode=PATROL once
    `timeout_sec` have passed with no new touch.

    Also owns the interlock. Both locks are taken, not just drive: a visitor standing at
    the panel is inside the arm's working radius, so locking only the base would still let
    the arm swing into them.

    terminate() MUST release both. A missed release presents as "the state changed but the
    robot won't move", which is painful to trace back to here.
    """

    def __init__(self, timeout_sec: float, clock=time.monotonic, name: str | None = None):
        super().__init__(name=name or f"UiSessionTimer[{timeout_sec}s]")
        self.timeout_sec = timeout_sec
        self.clock = clock
        self._entered_at = 0.0

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.UI_LAST_TOUCH_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.DRIVE_LOCK, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.ARM_LOCK, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.INTERACTING_REMAINING, access=Access.WRITE)

    def initialise(self):
        self.blackboard.set(Keys.DRIVE_LOCK, True)
        self.blackboard.set(Keys.ARM_LOCK, True)
        # INTERACTING 진입 자체를 암묵적 '터치'로 latch 한다. ui_last_touch_at(Float64)와
        # ui_touch 전이(fleet_cmd)는 **순서보장 없는 별도 토픽**이라, 전이가 먼저 처리되면
        # UI_LAST_TOUCH_AT 이 아직 0.0 이라 elapsed 가 거대 → 진입하자마자 타임아웃되어
        # PATROL 로 튕긴다. 진입 시각을 바닥값으로 깔면 늦게 온 스탬프도 즉시 만료를 못 만든다.
        self._entered_at = self.clock()

    def update(self) -> Status:
        # 실제 터치 스탬프와 진입 시각 중 더 최신을 쓴다 — 진입 직후엔 진입 시각이(0.0 대신),
        # 이후 방문자가 만지면 그 터치가 세션을 연장한다.
        last_touch = max(
            bb.get(self.blackboard, Keys.UI_LAST_TOUCH_AT, default=0.0), self._entered_at
        )
        elapsed = self.clock() - last_touch
        remaining = max(0.0, self.timeout_sec - elapsed)
        self.blackboard.set(Keys.INTERACTING_REMAINING, remaining)
        if elapsed >= self.timeout_sec:
            self.blackboard.set(Keys.NEXT_MODE, "PATROL")
            return Status.SUCCESS
        return Status.RUNNING

    def terminate(self, new_status):
        self.blackboard.set(Keys.DRIVE_LOCK, False)
        self.blackboard.set(Keys.ARM_LOCK, False)
        self.blackboard.set(Keys.INTERACTING_REMAINING, 0.0)
