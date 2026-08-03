import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys

#: 이 상태로 가는 전이는 유지 시간에 막히지 않는다. 고장은 기다릴 수 없다.
#
#  [2026-07-30] RETURNING 추가. 유지 시간이 2초일 때는 무엇을 막든 티가 안 났지만,
#  사람이 정한 상태가 오래 남도록 늘리면(params.yaml manual_hold_sec) **저전압 복귀도
#  그만큼 미뤄진다.** 배터리는 기다려 주지 않는다 — 관제가 IDLE 로 세워 둔 로봇이라도
#  15% 아래로 떨어지면 충전소로 보내야 한다.
#
#  [2026-07-31] CHARGING 추가 — **실측 버그.** 패널로 RETURNING 을 강제하면
#  `state_io.apply_pending` 이 `hold_until = now + manual_hold_sec`(300초)를 찍는다.
#  도킹은 그 안에 끝나므로 `SetNextMode("CHARGING")` 뒤의 이 leaf 가 FAILURE 를 내고,
#  루트 `Sequence(memory=False)` 가 실패한다. 다음 tick 에 `IsMode("RETURNING")` 이 여전히
#  참이라 `ReturnAndWatch` 가 통째로 다시 초기화되고 — **완벽히 도킹된 로봇이 ①단계
#  주행부터 다시 시작한다.** 충전소에서 빠져나와 입구로 되돌아간다.
#
#  도킹은 물리적으로 일어난 사실이다. 유지 시간은 "로봇이 사람의 결정을 스스로 되돌리는
#  것"을 막으려고 있는 것이지, 이미 일어난 일을 못 본 척하라는 뜻이 아니다.
_ALWAYS_ALLOWED = ("ERROR", "RETURNING", "CHARGING")

#  ⚠️ **INTERACTING 에서 나가는 것도 막지 않는다** (2026-08-03 실측).
#
#  패널에서 상태를 한 번 수동 지정하면 `apply_pending` 이 `HOLD_UNTIL = now + 300` 을
#  건다. 그 뒤 방문자가 화면을 만지면 `ui_touch` 는 **명령 유래라 유지 시간을 뚫고**
#  INTERACTING 으로 들어간다. 그런데 20초 뒤 `UiSessionTimer` 가 내는 복귀는 BT 자율
#  전이라 `COMMANDED_MODE` 가 없다 — 남은 280초 동안 **로봇이 INTERACTING 에 갇힌다.**
#  실측 로그: `전이 요청이 적용되지 않았다: INTERACTING -> PATROL (패널 유지 시간 중…)`
#
#  갇히면 순찰도 배차도 전부 멈춘다. 그리고 이 복귀는 사람의 결정을 **되돌리는 것이
#  아니라 사람이 정해 둔 상태로 돌아가는 것**이라, 유지 시간이 지킬 대상이 아니다.
_HOLD_EXEMPT_SOURCES = ("INTERACTING",)


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

    `next_mode` 를 지우지 않으므로, **같은 브랜치가 다음 tick 에도 이 leaf 까지 도달한다면**
    유지가 끝난 뒤 그 전이가 그대로 일어난다.

    ⚠️ [2026-07-30] **그 도달이 보장되는 건 IDLE·CHARGING·ERROR 뿐이다.** PATROL·WORKING 은
       exit 조건이 `Parallel` 안에 있어서(`watchdog.exit_watchdog` 의 trailing `Running()`),
       조건이 안 뜬 tick 은 `Parallel` 이 RUNNING 이 되고 루트 `Sequence` 가 이 leaf 앞에서
       멈춘다. 즉 그 브랜치에서 한 번 막힌 전이는 **미뤄지는 게 아니라 유실된다.**
       그래서 명령 유래 전이는 아래 `_held` 에서 아예 막지 않는다 — 명령이 매칭된 tick 은
       반드시 이 leaf 까지 오므로, 그 tick 에 통과시키는 것만이 유일하게 확실한 지점이다.
       (유실 자체는 main.py `_tick()` 이 경고로 드러낸다.)

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
        self.blackboard.register_key(key=Keys.COMMANDED_MODE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.HOLD_UNTIL, access=Access.READ)

    def _held(self, target: str) -> bool:
        if target in _ALWAYS_ALLOWED:
            return False
        if bb.get(self.blackboard, Keys.CURRENT_MODE) in _HOLD_EXEMPT_SOURCES:
            return False               # 세션 종료는 언제나 나갈 수 있다 — 위 주석 참고
        # 명령 유래(task_assigned·ui_touch·task_done·task_failed·stop_request)는 뚫는다.
        # 유지 시간의 목적은 "로봇이 스스로 사람의 결정을 되돌리는 것"을 막는 것이고,
        # 관제·패널 명령은 그 사람의 결정 자체다. 막으면 배차·터치·복귀가 조용히 사라진다
        # (실측 2026-07-30: manual_hold_sec=300 에서 task_assigned 두 건이 유실).
        if bb.get(self.blackboard, Keys.COMMANDED_MODE) == target:
            return False
        until = bb.get(self.blackboard, Keys.HOLD_UNTIL)
        now = (self.clock or time.monotonic)()
        return until is not None and now < until

    def update(self) -> Status:
        target = bb.get(self.blackboard, Keys.NEXT_MODE)
        if not target:
            return Status.FAILURE
        if self._held(target):
            return Status.FAILURE      # next_mode 는 남겨 둔다 — 클래스 주석의 ⚠️ 참고
        self.blackboard.set(Keys.CURRENT_MODE, target)
        self.blackboard.set(Keys.NEXT_MODE, None)
        self.blackboard.set(Keys.COMMANDED_MODE, None)
        return Status.SUCCESS
