"""야간 침입 추종 — **배선만** 한다. 추종 알고리즘은 한 줄도 새로 안 짠다.

## 왜 기존 `FollowExec` 을 안 쓰나

그 잎은 `WORKING` 브랜치 전용이고, 세션이 끝나면 `NEXT_MODE = "PATROL"` 을
**하드코딩**한다(`working_actions.py:1004`) — 추종이 끝나면 **주간 순찰로 떨어져
야간 근무를 이탈한다.** 그리고 `CommandDrivenAction` 이라 `active_command` 를 보므로
`SECURITY_PATROL` 의 좁은 명령표를 열어야 하는데, 그 좁음은 "패널 터치가 야간 근무를
뺏을 수 없게" 한 의도적 설계다(`branches/security_patrol.py:17`).

**드라이버는 죄가 없다.** `drivers["follow"]` 는 `/fleet_cmd{follow_admin}` 을 내고
결과를 폴링하는 `FleetCmdDriver` 일 뿐이다. 같은 드라이버를 부르는 **새 잎**을 만들면
위 두 문제를 통째로 우회한다.
"""
import time
from dataclasses import dataclass

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys

_IDLE, _CHASING, _RELEASE, _BACKOFF = "idle", "chasing", "release", "backoff"


@dataclass
class ChasePolicy:
    """숫자 근거는 설계문서 §9·§10. 임의로 고른 값이 없다.

    ⚠️ `sustain_sec` 은 AI 서버의 `TRIGGER_SEC`(1.0)보다 **길어야 한다.** 두 프로세스는
       서로 신호를 안 주고받고 같은 숫자를 각자 본다 — 이 차이가 **등록이 추종보다 먼저**
       일어남을 보장하는 유일한 장치다. 같으면 owner 없이 세션이 열려 곧바로 실패한다.

    ⚠️ `lose_sec` 은 `SEARCH_PEEK_ANGLE / ANGULAR_Z_SEARCH + 0.5` 유도값이다
       (libi_perception/config.py). **9.49 이상으로 올리면** 인지 쪽 탐색이 훑기·180°
       회전 단계까지 진입해 **로봇이 밤에 제자리에서 빙빙 돈다.**
    """
    trigger_size: float = 100.0
    sustain_sec: float = 1.5
    lose_sec: float = 5.0
    max_chase_sec: float = 60.0
    release_grace_sec: float = 1.0
    failure_backoff_sec: float = 10.0


class IntruderChase(py_trees.behaviour.Behaviour):
    """침입자가 보이면 순찰을 세우고 추종 세션을 연다.

    `Selector` 의 **첫 자식**이다. `FAILURE` 를 돌려주면 뒤의 `PatrolNavigation` 이 돈다.
    """

    def __init__(self, policy, follow_driver=None, nav_stop_driver=None,
                 now_fn=time.monotonic, name="IntruderChase"):
        super().__init__(name=name)
        self.policy = policy
        self.follow_driver = follow_driver
        self.nav_stop_driver = nav_stop_driver
        self._now = now_fn
        self._state = _IDLE
        self._seen_since = None
        self._chase_start = None
        self._last_seen = None
        self._release_at = None
        self._backoff_until = 0.0

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.FRONT_PERSON_SIZE, access=Access.READ)

    def initialise(self):
        """⚠️ **아무것도 지우지 않는다.**

        이 잎은 추종 중이 아닐 때 `FAILURE` 를 돌려주는데, py_trees 는 상태가 `RUNNING`
        이 아니면 **매 tick `initialise()` 를 다시 부른다**(실측 2026-08-03: FAILURE 3틱
        = initialise 3번). 여기서 `_seen_since` 를 지우면 지속시간이 **영영 안 쌓여
        추종이 시작되지 않는다.**

        브랜치 이탈 시 정리는 `terminate(INVALID)` 가 맡는다 — 실측으로 `FAILURE` 상태인
        잎에도 `INVALID` 가 도달한다.
        """

    def update(self) -> Status:
        now = self._now()
        size = bb.get(self.blackboard, Keys.FRONT_PERSON_SIZE) or 0.0
        seen = float(size) >= self.policy.trigger_size

        if self._state == _CHASING:
            return self._chasing(now, seen)
        if self._state == _RELEASE:
            if now - self._release_at >= self.policy.release_grace_sec:
                self._state = _BACKOFF if now < self._backoff_until else _IDLE
                return Status.FAILURE
            return Status.RUNNING           # nav2 를 아직 못 내보낸다 — 아래 주석
        if self._state == _BACKOFF:
            if now < self._backoff_until:
                return Status.FAILURE
            self._state = _IDLE

        if not seen:
            self._seen_since = None
            return Status.FAILURE
        if self._seen_since is None:
            self._seen_since = now
            return Status.FAILURE
        if now - self._seen_since < self.policy.sustain_sec:
            return Status.FAILURE
        return self._start(now)

    def terminate(self, new_status):
        """⚠️ **이 잎은 `CommandDrivenAction` 이 아니다** — 그 클래스가 공짜로 주는
        `terminate → driver.stop()` 을 못 물려받는다. 여기서 직접 해야 한다.

        안 하면 관제 「정지」·배터리 저하로 브랜치가 통째로 빠져도 `follow_stop` 이
        안 나가고, `follow_admin` 세션은 **lease 면제**라(`libi_perception/session.py:68`)
        스스로 안 끝난다 → 제어 루프가 20Hz 로 `/cmd_vel` 을 계속 민다.
        실측 사고 기록: `working_actions.py` `_abandon` 독스트링(2026-07-28).

        ⚠️ **`INVALID` 가 아니면 아무것도 안 한다.** py_trees 는 `FAILURE` 를 돌려줄
        때마다 `terminate(FAILURE)` 를 부른다(실측). 여기서 `_state` 를 지우면
        **백오프가 매 tick 사라져** owner 없는 상태에서 트리거→실패→재트리거가 무한
        반복한다 — 백오프를 넣은 이유 자체가 무효가 된다.
        """
        if new_status != Status.INVALID:
            return
        if self._state in (_CHASING, _RELEASE):
            self._stop_follow()
        self._state = _IDLE
        self._seen_since = None
        self._backoff_until = 0.0

    # ── 내부 ─────────────────────────────────────────────────────────────

    def _start(self, now):
        # 순서가 중요하다: nav2 목표를 먼저 취소해 `/cmd_vel` 을 비운 뒤 추종을 연다.
        # 반대로 하면 두 발행자가 서로 다른 속도를 낸다.
        if self.nav_stop_driver is not None:
            self.nav_stop_driver.start()
        if self.follow_driver is not None:
            self.follow_driver.start()
        self._state = _CHASING
        self._chase_start = now
        self._last_seen = now
        self._seen_since = None
        return Status.RUNNING

    def _chasing(self, now, seen):
        if seen:
            self._last_seen = now
        result = self.follow_driver.poll() if self.follow_driver else "running"
        if result in ("success", "failure"):
            # 세션이 스스로 끝났다. `failure` 면 백오프를 건다 — owner 가 없는 상태에서는
            # 트리거→실패→재트리거가 무한 반복해 **순찰이 5초마다 끊겨 마비된다.**
            return self._release(now, backoff=(result == "failure"))
        if now - self._last_seen >= self.policy.lose_sec:
            return self._release(now, backoff=False)
        if now - self._chase_start >= self.policy.max_chase_sec:
            return self._release(now, backoff=False)
        return Status.RUNNING

    def _release(self, now, *, backoff):
        """`follow_stop` 을 내고 **한 틱 더 붙잡는다.**

        `FleetCmdDriver.stop()` 은 발행만 하고 결과를 안 기다린다. 같은 틱에 `FAILURE` 를
        돌려주면 곧바로 `PatrolNavigation` 이 새 nav 목표를 내는데, `follow_node` 는 아직
        세션을 안 닫았을 수 있다 — 그 순간 `/cmd_vel` 발행자가 둘이 된다.
        BT 5Hz · follow_node 20Hz 라 1초면 충분하다.
        """
        self._stop_follow()
        self._state = _RELEASE
        self._release_at = now
        if backoff:
            self._backoff_until = now + self.policy.release_grace_sec \
                + self.policy.failure_backoff_sec
        return Status.RUNNING

    def _stop_follow(self):
        if self.follow_driver is not None:
            self.follow_driver.stop()


class CameraSelectRenew(py_trees.behaviour.Behaviour):
    """앞캠을 켜 둔다 — `/libi/camera_select` 에 `"front"` 를 주기 재발행한다.

    ## 왜 필요한가 — 빠뜨리면 야간 기능 전체가 조용히 죽는다

    야간 순찰 중 앞캠을 켜 두던 **유일한 통로**가 `PersonBlockGuard._request_camera`
    (`person_block.py:241-255`)였다. 이 설계는 야간에서 그 감시자를 빼므로, 그 20줄만
    떼어 여기로 옮긴다. 안 옮기면 `camera_select` 가 만료되어 프레임이 AI 서버로
    **아예 안 가고**, 감지·녹화·추종이 전부 죽는다 — 에러 한 줄 없이.

    ## 발행자 규칙과의 관계

    `camera_select` 발행자는 `follow_node` 하나라는 규칙이 있지만(`follow_node.py:239`),
    `PersonBlockGuard` 는 이미 그 규칙의 **기존 예외**다(`person_block.py:142` — 세션을
    열지 않고 직접 요청한다). 이 잎은 그 예외를 이름만 바꿔 잇는 것이지 새 예외가 아니다.

    추종이 시작되면 `follow_node` 가 세션 소유자로서 자기도 발행하는데, **둘 다 `"front"`
    를 내므로 결과가 같다**(길잡이는 뒷캠이라 겹치면 문제지만 여기는 아니다).
    """

    def __init__(self, camera_driver=None, renew_sec=1.0,
                 now_fn=time.monotonic, name="CameraSelectRenew"):
        super().__init__(name=name)
        self.camera_driver = camera_driver
        self.renew_sec = renew_sec
        self._now = now_fn
        self._sent_at = None

    def setup(self, **kwargs):
        return True

    def initialise(self):
        # 브랜치에 다시 들어오면 즉시 한 번 보낸다.
        self._sent_at = None

    def update(self) -> Status:
        if self.camera_driver is None:
            return Status.RUNNING
        now = self._now()
        if self._sent_at is None or now - self._sent_at >= self.renew_sec:
            self._sent_at = now
            self.camera_driver("front")
        return Status.RUNNING
