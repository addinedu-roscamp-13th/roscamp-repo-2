import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class UiSessionTimer(py_trees.behaviour.Behaviour):
    """RUNNING while a visitor is using the touch panel; SUCCESS + next_mode=PATROL once
    `timeout_sec` have passed with no new touch.

    ⚠️ **드라이브·팔 잠금은 이 트리에서 강제되지 않는다.** 예전 주석은 이 노드가 인터록을
    "소유한다"고 적었지만, `drive_lock`/`arm_lock` 을 읽는 production 코드는 레포 전체에
    하나도 없다(2026-07-26 전수 확인: py/ts/tsx/cpp/qml, 읽는 곳은 테스트 2개뿐).

    그리고 BT 안에 검사를 넣어도 죽은 코드다 — `Priorities` 는 Selector 라 한 tick 에
    브랜치 하나만 돌고, 이 노드는 InteractingBranch 안에만 있다. 락이 켜져 있는 동안
    다른 브랜치의 액션 leaf 는 애초에 tick 되지 않는다. "응대 중엔 움직이지 않는다"는
    **브랜치 우선순위가 이미 보장**하고 있고, 이 두 키는 그 사실의 표시일 뿐이다.

    진짜 교차 프로세스 인터록(FMS 가 /fleet_cmd 로 직접 미는 주행까지 막기)이 필요하면
    로봇 쪽 fleet_link 에서 막아야 한다 — blackboard 불리언은 그 프로세스에 닿지 않는다.

    terminate() 는 두 키를 반드시 되돌린다. 표시값이라도 남아 있으면 화면·테스트가
    "응대 중"으로 계속 읽는다.
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
        self.blackboard.register_key(key=Keys.INTERACTING_REMAINING, access=Access.WRITE)

    def initialise(self):
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
        self.blackboard.set(Keys.INTERACTING_REMAINING, 0.0)
