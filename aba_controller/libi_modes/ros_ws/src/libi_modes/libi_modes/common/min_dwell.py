import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class MinDwell(py_trees.behaviour.Behaviour):
    """RUNNING until `seconds` have passed since the branch's state was entered, then
    SUCCESS. Placed in front of an exit condition so a state cannot be entered and left
    within the same tick.

    ## 왜 필요한가

    한 tick 안에 들어갔다 나오는 상태는 **아무 관찰자에게도 보이지 않는다** — LED 도,
    관제 패널도, 감사 로그도 그 상태를 못 본다. 실제로 두 군데서 그랬다:

        CHARGING          진입 즉시 BatteryCheck(>=40) 통과 → IDLE   (배터리가 이미 높다)
        SECURITY_PATROL   랩 드라이버가 곧바로 success → SetNextMode(IDLE)

    상태 기계는 맞게 돌았지만 사람이 볼 수가 없었다. 그건 상태 표시가 있으나 마나라는 뜻이다.

    ## 왜 initialise() 로 시간을 재지 않나

    `initialise()` 는 직전 status 가 RUNNING 이 아니면 매 tick 불린다. 이 leaf 가 한 번
    SUCCESS 를 돌려주면 다음 tick 에 다시 initialise 되어 타이머가 **되감긴다** — 영원히
    1초를 못 넘기고 상태가 갇힌다. 그래서 시간의 기준을 `current_mode` **변화**에 둔다.
    상태가 그대로면 기준 시각도 그대로고, 상태가 바뀌면 그때 다시 잰다.

    ## 고장 시 어느 쪽으로 기우나

    `current_mode` 를 아직 모르면(부팅 직후) SUCCESS 를 돌려준다 — 모르는 상태를 붙잡아
    두는 것보다 통과시키는 편이 안전하다. 이 leaf 는 지연을 넣는 장치지 안전 장치가 아니다.
    """

    def __init__(self, seconds: float, clock=time.monotonic, name: str | None = None):
        super().__init__(name=name or f"MinDwell[{seconds}s]")
        self.seconds = seconds
        self.clock = clock
        self._mode = None
        self._since = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.CURRENT_MODE, access=Access.READ)

    def update(self) -> Status:
        mode = bb.get(self.blackboard, Keys.CURRENT_MODE)
        if mode is None:
            return Status.SUCCESS
        if mode != self._mode:
            self._mode = mode
            self._since = self.clock()
        if self.clock() - self._since >= self.seconds:
            return Status.SUCCESS
        return Status.RUNNING
