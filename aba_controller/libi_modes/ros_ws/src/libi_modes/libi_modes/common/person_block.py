"""주행 중 사람이 앞을 막는 것을 판정한다. **ROS 를 모른다.**

## 왜 크기만 보나

노트 그대로다 — "특정 bbox 픽셀이면 정지". 화면상 좌우 위치는 안 본다.
크기는 `sqrt(area)` 픽셀(320 원본 기준)이고, 추종이 후진을 시작하는 지점
(`libi_perception/config.py` 의 `TARGET_SIZE + DIST_DEADZONE = 199`)에 10 을 더한 값이
정지 임계다.

## 왜 재개에 유예가 있나

검출이 한 프레임 깜빡였다고 로봇이 앞뒤로 들썩이면 못 쓴다. 유예 안에 다시 보이면
지속 시간을 처음부터 다시 세지 않는다 — 사람이 어른거리는 동안 영원히 기다리게 된다.

## 왜 직진해야 다시 무장하나

차단을 알린 뒤 로봇은 되돌아가고 돌아서 새 길로 간다. 그동안 옆에 있는 사람이 계속
크게 보이므로, 그대로 두면 곧바로 또 멈춘다. 노트 A-4 가 "yaw 회전을 한두 번 하고서야
cam 탐색하면서 주행" 이라고 적은 것이 이것이다.
"""
from __future__ import annotations

DRIVE, HALT, BLOCK = "drive", "halt", "block"


class PersonBlockPolicy:
    """`update(size, moving_straight, now) -> 동작`.

    `size` 는 `sqrt(area)` 픽셀(320 기준). `None` 은 "안 보인다".
    `moving_straight` 는 로봇이 실제로 직진 중인가.
    """

    def __init__(self, stop_size: float, sustain_sec: float, resume_grace_sec: float,
                 rearm_timeout_sec: float | None = None):
        self.stop_size = float(stop_size)
        self.sustain_sec = float(sustain_sec)
        self.resume_grace_sec = float(resume_grace_sec)
        #: 차단을 알린 뒤 **직진이 안 돌아와도** 이만큼 지나면 다시 무장한다.
        #: 없으면 `sustain_sec` 의 **두 배**다 — 복귀 주행(회전 후 전진)이 끝나기 전에
        #: 다시 무장하면, 그 사이 아직 화면에 남은 사람 때문에 복귀가 스스로 멈춘다.
        #: (정상 복귀는 직진이 돌아오는 순간 어차피 즉시 재무장한다 — 이 시간은
        #: **아예 못 움직일 때**만 쓰이는 최후 방어선이다.)
        #:
        #: ⚠️ 이게 없으면 안전 구멍이 난다(2026-08-03 실기). 알린 뒤 로봇은 복귀 주행을
        #: 해야 하는데, **사람이 코앞이라 nav2 가 경로 자체를 못 만들면**
        #: (`Failed to create plan with tolerance of: 0.100000`) 로봇이 못 움직인다.
        #: 직진이 영영 안 돌아오니 무장도 영영 안 되고, 그 사이 판정은 DRIVE 를 돌려
        #: **214px(임계 170) 로 코앞에 서 있는 사람에게도 정지를 안 건다.**
        self.rearm_timeout_sec = (2.0 * float(sustain_sec) if rearm_timeout_sec is None
                                  else float(rearm_timeout_sec))
        self._blocked_since: float | None = None
        self._clear_since: float | None = None
        #: BLOCK 을 알린 시각. 알린 직후 한 유예(resume_grace_sec) 동안은 아직 그
        #: 자리에 있다고 보고 HALT 를 유지한다 — 그 다음부터 무시(DRIVE)한다.
        self._reported_at: float | None = None
        #: 차단을 한 번 알린 뒤 직진이 돌아올 때까지 내려간다.
        self._armed = True
        self._reported = False

    @property
    def armed(self) -> bool:
        return self._armed

    def seconds_to_block(self, now: float):
        """차단을 알리기까지 **남은 초**. 세고 있지 않으면 `None`.

        화면이 "사람 때문에 N초 뒤 재계획" 을 그대로 띄우게 하려고 낸다 — 관제에서
        재계획이 **사람 때문인지 지연 때문인지** 구분이 안 돼 디버깅이 어려웠다
        (사용자 요구 2026-08-03). 판정에 쓰는 값 그 자체라 화면이 따로 계산하지 않는다.

        `None` 인 경우: 아직 사람이 안 보임 / 이미 알림(재무장 대기) / 비켜서 타이머가
        버려짐. 그 셋은 화면에서 "카운트다운 없음" 으로 같게 보이면 된다.
        """
        if not self._armed or self._reported or self._blocked_since is None:
            return None
        return max(0.0, self.sustain_sec - (now - self._blocked_since))

    def reset(self) -> None:
        self._blocked_since = None
        self._clear_since = None
        self._reported_at = None
        self._armed = True
        self._reported = False

    def update(self, size, moving_straight: bool, now: float) -> str:
        if not self._armed:
            # 직진이 돌아오지 않아도 시간이 지나면 다시 무장한다 — 위 주석의 안전 구멍.
            timed_out = (self._reported_at is not None
                         and now - self._reported_at >= self.rearm_timeout_sec)
            if moving_straight or timed_out:
                # 직진이 돌아왔다 — 다시 무장하고 이번 틱부터 새로 센다.
                # (아래 근접 판정으로 그대로 흘러가 바로 반영한다.)
                self._armed = True
                self._blocked_since = None
                self._clear_since = None
                self._reported_at = None
                self._reported = False
            else:
                # 알린 직후 잠깐(유예)까지는 계속 막혀 있다고 보고, 그 뒤로는 무시한다.
                if now - self._reported_at <= self.resume_grace_sec:
                    return HALT
                return DRIVE

        near = size is not None and float(size) >= self.stop_size
        if not near:
            # 비켰다 — 유예를 넘겨야 타이머를 버린다.
            if self._blocked_since is not None:
                if self._clear_since is None:
                    self._clear_since = now
                elif now - self._clear_since >= self.resume_grace_sec:
                    self._blocked_since = None
                    self._clear_since = None
            if self._blocked_since is None:
                return DRIVE
            return HALT

        self._clear_since = None
        if self._blocked_since is None:
            self._blocked_since = now
        if not self._reported and now - self._blocked_since >= self.sustain_sec:
            self._reported = True
            self._reported_at = now
            self._armed = False
            return BLOCK
        return HALT


# ── BT leaf ────────────────────────────────────────────────────────────────
# 정책(위)은 py_trees 를 모른다. 아래만 트리에 붙는다.

import time                                                   # noqa: E402

import py_trees                                               # noqa: E402
from py_trees.common import Access, Status                    # noqa: E402

from libi_modes import blackboard as bb                       # noqa: E402
from libi_modes.blackboard import Keys                        # noqa: E402

#: `/libi/camera_select` 재발행 간격(초). 송출기(`camera_sender.py`)의
#: `DEFAULT_SELECT_EXPIRY_SEC`(3.0s) 의 절반 이하여야 한다 — 그보다 뜸하면 만료
#: 워치독이 "none" 으로 떨어뜨린 뒤에야 다음 요청이 도착해 카메라가 깜빡인다.
_CAMERA_RENEW_SEC = 1.0


class PersonBlockGuard(py_trees.behaviour.Behaviour):
    """주행 중 앞을 막는 사람을 보고 정지·차단 보고까지 한다.

    ## ⚠️ 절대 SUCCESS 를 돌려주지 않는다

    이 leaf 는 `WorkingBranch` 의 `Parallel(SuccessOnOne)` 아래 `CommandDispatch`·
    watchdog 과 **나란히** 붙는다. 거기서 SUCCESS 를 내면 **병렬 전체가 끝나** 주행이
    통째로 끊긴다. watchdog 은 일부러 그걸 쓰지만 이 leaf 는 감시자이므로 항상 RUNNING
    이다. `test_never_returns_success_even_when_blocking` 이 이걸 붙들고 있다.

    ## 왜 명령 Selector 안이 아닌가

    Selector 에 넣으면 첫 매치가 이기는 규칙 때문에 `navigate` 를 가로챈다. 감시는
    명령이 아니다.

    ## 목적지는 보고하지 않는다 (Story 13)

    커밋 노드가 이번 다리의 목적지면 정지는 하되(사람이 실제로 앞을 막고 있으니)
    FMS 에 정점 차단은 알리지 않는다 — 어차피 그리로 가는 길이라 우회할 이유가 없다.
    판정은 `Keys.COMMITTED_IS_DESTINATION`(FMS 가 `/fleet_cmd` args 로 실어 보낸
    `is_destination`)으로 한다.

    ## 왜 `navigate` 중에만 도나 (Task 17)

    카메라는 앞을 볼 사람이 있어야 도는데, 감시가 볼 프레임은 **앞캠이 실제로 켜져
    있어야** 나온다. 길잡이·추종은 자기 세션이 이미 카메라를 쥐고 있고(각자의
    근접 게이트도 따로 있다 — `GuideExec._too_near`), 배달 주행(`navigate`)만 아무도
    카메라를 켜 주지 않는다 — 그래서 `active_command == "navigate"` 일 때만 이 leaf가
    `camera_driver` 로 `"front"` 를 직접 요청한다. `follow_node` 의 세션(`watch`)을
    여는 방식은 **안 된다** — `SessionManager` 는 세션을 하나만 살리므로, 열면 길잡이의
    뒷캠 세션을 덮어써 안내가 깨진다(옵시디언 인터페이스 노트 2026-08-03).

    ⚠️ 이 게이트가 사람 차단 판정 자체도 같이 가린다 — `guide`/`follow_admin` 중에는
    `stop_driver`/`block_fn` 도 안 부른다. 그 두 모드는 각자의 정지·회복 로직이 이미
    있어(`GuideExec`, 추종 회복 BT) 이 leaf 가 끼어들 자리가 아니다.

    ## 참고

    `libi_perception/keepout_mask.py` 의 정책/노드 분리, 그리고
    `~/previous_project/pingdergarten` 의 `map_boundary_monitor.py` 가 정립한 monitor
    컨벤션(매 tick RUNNING · `initialise()` 로 re-arm)을 따른다.
    """

    def __init__(self, policy: PersonBlockPolicy, stop_driver=None, block_fn=None,
                 nav_leaf=None, camera_driver=None, name: str = "PersonBlockGuard",
                 now_fn=time.monotonic, require_command="navigate"):
        super().__init__(name=name)
        self.policy = policy
        #: 이 값이 `ACTIVE_COMMAND` 와 같을 때만 동작한다. `None` 이면 **게이트 없음** —
        #: 브랜치 자체가 게이트인 곳(`PatrolBranch`·`SecurityPatrolBranch`)에 쓴다.
        #: WorkingBranch 는 한 브랜치가 navigate/guide/follow/arm 을 다 처리하므로
        #: 여기서 갈라야 하지만, 순찰 브랜치는 순찰밖에 안 한다.
        self.require_command = require_command
        #: nav2 를 끊는 수단. 없으면 멈추는 척하지 않고 로그로 드러낸다.
        self.stop_driver = stop_driver
        #: `block_fn(node)` — FMS 에 차단을 알린다. 없으면 아무 데도 안 알린다.
        self.block_fn = block_fn
        #: 정지해 있는 동안 `NavigationExec` 의 도착 시계를 멈춰 준다(task-7b).
        self.nav_leaf = nav_leaf
        #: `camera_driver("front")` — `/libi/camera_select` 를 직접 요청한다(세션을 열지
        #: 않는다). 없으면 앞캠이 안 켜져 `FRONT_PERSON_SIZE` 가 계속 0(=None)일 뿐,
        #: 이 leaf 자체는 여전히 안전하게 동작한다(로그 없이 조용히 감지만 안 된다 —
        #: `stop_driver`/`block_fn` 과 달리 안전 기능이 아니라 입력 공급이라 에러 로그를
        #: 안 남긴다).
        self.camera_driver = camera_driver
        self._now = now_fn
        self._halted = False
        #: 마지막으로 카메라를 요청한 시각. `_CAMERA_RENEW_SEC` 보다 촘촘히 보내지 않는다
        #: — `GuideExec._watch_renewed_at` 과 같은 rate-limit 패턴.
        self._camera_requested_at = None
        #: 차단을 알린 횟수. 화면이 "늘어났나" 만 보고 기록을 남긴다 — 레벨로 보면
        #: 임계 근처 깜빡임이 그대로 로그가 된다(2026-08-03 실측).
        #: `initialise()` 로 **리셋하지 않는다** — 브랜치를 드나들 때마다 0으로 돌아가면
        #: 화면이 "늘었다" 를 못 알아본다.
        self._block_seq = 0

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.FRONT_PERSON_SIZE, access=Access.READ)
        self.blackboard.register_key(key=Keys.MOVING_STRAIGHT, access=Access.READ)
        self.blackboard.register_key(key=Keys.COMMITTED_NODE, access=Access.READ)
        self.blackboard.register_key(key=Keys.COMMITTED_IS_DESTINATION, access=Access.READ)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.READ)
        self.blackboard.register_key(key=Keys.PERSON_BLOCKED, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.PERSON_BLOCK_IN, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.PERSON_BLOCK_SEQ, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.PERSON_BLOCK_NODE, access=Access.WRITE)
        self.blackboard.set(Keys.PERSON_BLOCK_SEQ, self._block_seq)
        self.blackboard.set(Keys.PERSON_BLOCK_NODE, None)
        self._set_blocked(False)

    def initialise(self):
        # 브랜치에 다시 들어오면 처음부터 센다.
        self.policy.reset()
        self._halted = False
        self._camera_requested_at = None
        self._set_blocked(False)

    def update(self) -> Status:
        if (self.require_command is not None
                and bb.get(self.blackboard, Keys.ACTIVE_COMMAND) != self.require_command):
            # 길잡이·추종·팔·대기 — 카메라도 정지 판정도 이 leaf 의 일이 아니다.
            # 정책을 reset 해 둔다: 안 하면 배달 사이 guide 로 넘어갔다 온 뒤 낡은
            # blocked_since 가 남아 새 배달을 즉시 차단으로 판정할 수 있다.
            self.policy.reset()
            self._halted = False
            self._camera_requested_at = None
            self._set_blocked(False)
            return Status.RUNNING

        self._request_camera()

        size = bb.get(self.blackboard, Keys.FRONT_PERSON_SIZE)
        straight = bool(bb.get(self.blackboard, Keys.MOVING_STRAIGHT))
        action = self.policy.update(size, straight, self._now())

        if action == DRIVE:
            self._halted = False
            if self.nav_leaf is not None:
                self.nav_leaf.pause_arrive_timer(False)
        elif action == HALT:
            self._halt()
        elif action == BLOCK:
            self._halt()
            self._report_block()
        self._set_blocked(self._halted)
        # 남은 카운트다운을 그대로 내보낸다 — 화면이 "N초 뒤 재계획" 을 띄운다.
        self.blackboard.set(Keys.PERSON_BLOCK_IN, self.policy.seconds_to_block(self._now()))
        return Status.RUNNING

    def _set_blocked(self, value: bool) -> None:
        """패널이 "왜 섰나" 를 알 수 있게 blackboard 에 남긴다.

        `_halted` 를 그대로 쓴다 — 정지 수단이 없어 실제로는 못 멈춘 경우에도 True 다.
        화면에 "정지"라고 뜨는데 로봇이 달리면 그 불일치가 곧 결함 신호다. 반대로
        False 로 감추면 아무도 모른다.
        """
        self.blackboard.set(Keys.PERSON_BLOCKED, bool(value))
        if not value:
            # 정지가 아니면 카운트다운도 없다. 안 지우면 화면에 옛 숫자가 남는다.
            self.blackboard.set(Keys.PERSON_BLOCK_IN, None)

    def _request_camera(self):
        """`/libi/camera_select` 에 `"front"` 를 주기 재발행한다(rate-limited).

        매 tick(5Hz) 그대로 내보내면 토픽이 찬다 — `GuideExec._allow_rotate` 가 같은
        이유로 "바뀔 때만" 보내는 것과 달리, 여기는 만료 워치독을 살려 둬야 하므로
        "값이 바뀌지 않아도 주기적으로" 다시 보낸다(`_publish_camera` 의 규칙과 같다).
        """
        if self.camera_driver is None:
            return
        now = self._now()
        if (self._camera_requested_at is not None
                and now - self._camera_requested_at < _CAMERA_RENEW_SEC):
            return
        self._camera_requested_at = now
        self.camera_driver("front")

    def _halt(self):
        if self._halted:
            return                      # 취소는 한 번만 — 매 tick 보내면 실행 층이 막힌다
        self._halted = True
        if self.nav_leaf is not None:
            self.nav_leaf.pause_arrive_timer(True)
        if self.stop_driver is None:
            self.logger.error(
                "PersonBlockGuard: 앞을 막았는데 정지 수단(stop_driver)이 없어 nav 를 "
                "취소하지 못했다 — 로봇은 계속 달린다")
            return
        self.stop_driver.start()

    def _report_block(self):
        node = bb.get(self.blackboard, Keys.COMMITTED_NODE)
        if node is None or self.block_fn is None:
            return
        if bb.get(self.blackboard, Keys.COMMITTED_IS_DESTINATION, False):
            return                      # 목적지 자체는 차단을 알리지 않는다 (Story 13)
        self.block_fn(int(node))
        # 화면 기록용 — **실제로 알린 뒤에만** 센다. 위 조기 반환들(노드 모름·목적지)에서
        # 세면 화면이 "재탐색 요청" 이라고 쓰는데 아무 데도 안 나간 상태가 된다.
        self._block_seq += 1
        self.blackboard.set(Keys.PERSON_BLOCK_SEQ, self._block_seq)
        self.blackboard.set(Keys.PERSON_BLOCK_NODE, int(node))

    def terminate(self, new_status):
        # 여기서 명시적으로 "none" 을 보내지 않는다 — 매 tick 재요청을 멈추기만 하면
        # 송출기의 만료 워치독(`DEFAULT_SELECT_EXPIRY_SEC`, `_CAMERA_RENEW_SEC` 의 절반
        # 이상)이 저절로 "none" 으로 떨어뜨린다. 그 워치독이 정확히 이 상황(발행자가
        # 말없이 멈춤)을 위해 있는 것이라 새로 만들 이유가 없다(camera_select.py 머리말).
        self._halted = False
        self._camera_requested_at = None
        # 브랜치를 떠나면서 이걸 안 끄면 패널에 "정지" 배지가 영원히 남는다.
        self._set_blocked(False)
