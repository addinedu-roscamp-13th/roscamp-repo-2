import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys

#: 이 상태로 가는 전이는 유지 시간에 막히지 않는다. 고장은 기다릴 수 없다.
_ALWAYS_ALLOWED = ("ERROR",)


class RequestTransition(py_trees.behaviour.Behaviour):
    """Applies blackboard.next_mode to current_mode, then clears next_mode.

    Always the LAST child of a branch's root Sequence, never inside a Parallel — inside a
    Parallel the action child can finish first and the transition never runs, leaving the
    robot repeating the same state forever.

    Clearing next_mode is load-bearing: leaving it set would re-fire the same transition
    on the following tick.

    ## 유지 시간 (hold_until)

    관제 패널에서 상태를 바꿔도 **같은 tick 에 원래대로 돌아가** 화면에 아무 일도 안
    일어난 것처럼 보이는 경우가 있었다:

        CHARGING 로 전이   →  배터리가 이미 40% 이상       →  곧바로 IDLE
        SECURITY_PATROL    →  순찰 랩이 즉시 끝났다고 보고  →  곧바로 IDLE

    그래서 `state_io` 가 패널 전이를 적용할 때 `hold_until` 을 찍어 두고, 그 시각까지는
    여기서 **BT 가 스스로 하는 전이를 미룬다.** 누른 상태가 사람 눈에 보일 만큼 남는다.

    미루는 것이지 버리는 것이 아니다 — `next_mode` 를 지우지 않으므로 유지 시간이 지나면
    그 전이가 그대로 일어난다. 그동안 다른 이탈 조건이 새 값을 쓰면 새 값이 이긴다.

    ⚠️ ERROR 는 막지 않는다. 고장은 유지 시간과 무관하게 즉시 반영돼야 한다.
    ⚠️ 패널 전이 자체는 이 leaf 를 거치지 않는다 (`state_io.apply_pending`). 그래서 유지
       중에도 사람은 언제든 다른 상태로 바꿀 수 있다 — 로봇만 스스로 못 나간다.
    """

    def __init__(self, name: str | None = None, clock=None):
        super().__init__(name=name or "RequestTransition")
        # 기본값을 `clock=time.monotonic` 으로 쓰면 **import 시점에 묶여** 나중에 바꿔치기가
        # 안 된다. 브랜치가 이 leaf 를 인자 없이 만들기 때문에 시계를 주입할 자리가 없어져,
        # 테스트가 실제로 2초를 기다려야 한다. 호출할 때 푼다.
        self.clock = clock

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.HOLD_UNTIL, access=Access.READ)

    def _held(self, target: str) -> bool:
        if target in _ALWAYS_ALLOWED:
            return False
        until = bb.get(self.blackboard, Keys.HOLD_UNTIL)
        now = (self.clock or time.monotonic)()
        return until is not None and now < until

    def update(self) -> Status:
        target = bb.get(self.blackboard, Keys.NEXT_MODE)
        if not target:
            return Status.FAILURE
        if self._held(target):
            return Status.FAILURE      # next_mode 는 남겨 둔다 — 유지가 끝나면 다시 시도
        self.blackboard.set(Keys.CURRENT_MODE, target)
        self.blackboard.set(Keys.NEXT_MODE, None)
        return Status.SUCCESS
