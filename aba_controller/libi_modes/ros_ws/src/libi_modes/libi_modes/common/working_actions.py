import logging
import math
import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


class CommandDrivenAction(py_trees.behaviour.Behaviour):
    """Executes one dispatched command, or fails instantly so a sibling can claim it.

    Siblings sit under a Selector, so returning FAILURE when `active_command` is not ours
    is how dispatch works — the Selector walks on to the next handler.

    Clears active_command once the command finishes so the adapter's next command can be
    picked up. Sequencing across commands belongs to the task adapter; this leaf only ever
    knows about the one command in front of it.
    """

    def __init__(self, driver, handles: set, name: str):
        super().__init__(name=name)
        self.driver = driver
        self.handles = handles
        self._started = False

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ACTIVE_COMMAND, access=Access.WRITE)

    def initialise(self):
        self._started = False

    def update(self) -> Status:
        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND) not in self.handles:
            self._abandon()
            return Status.FAILURE
        if not self._started:
            self.driver.start()
            self._started = True
        result = self.driver.poll()
        if result in ("success", "failure"):
            return self._release(Status.SUCCESS if result == "success" else Status.FAILURE)
        return Status.RUNNING

    def _release(self, status: Status) -> Status:
        """명령 슬롯을 비우고 상태를 돌려준다. 안 비우면 다음 명령을 못 받는다.

        하위 클래스가 "끝났을 때 할 일"을 여기에 얹는다(자기 상태 정리, 상태 전이 예약).
        끝나는 자리가 여기 하나뿐이라, 얹은 일이 성공/실패 어느 쪽으로도 빠지지 않는다.
        """
        self.blackboard.set(Keys.ACTIVE_COMMAND, None)
        self._started = False
        return status

    def _abandon(self) -> None:
        """슬롯을 뺏겼다 — **열어 둔 원격 세션을 닫는다.**

        `terminate(INVALID)` 만으로는 안 된다. 그건 이 leaf 가 tick 에서 통째로 빠질 때만
        오는데(상태 전이, 앞 형제가 가로챔), `active_command` 가 바뀌는 건 그 둘 다
        아니다 — 이 leaf 는 여전히 tick 되고, 제 "내 명령 아님" 분기로 얌전히 FAILURE 를
        돌려주며 손만 놨다. 그 사이 원격 세션은 아무도 안 닫는다.

        실측 2026-07-28, `/fleet_cmd` echo — `stop` 이 **단 한 번도** 안 나갔다::

            follow_admin-1-...     추종 시작
            navigate ...           관제가 주행을 배차 (active_command 를 덮음)
            follow_admin-2-...     두 번째 추종
                                   ← 그 사이 어디에도 stop 이 없다

        결과: libi_perception 의 제어 루프가 계속 20Hz 로 `/cmd_vel` 을 밀고,
        `/libi/follow_bt_snapshot` 이 `Following[TRACKING]` 을 영원히 내보내
        관제 화면에서 `FollowExec` 은 회색인데 그 밑만 파랗게 남았다.

        슬롯(`active_command`)은 **건드리지 않는다.** 남의 명령을 여기서 지우면 그 명령을
        처리할 형제가 영영 못 받는다.
        """
        if self._started:
            self.driver.stop()
        self._started = False

    def terminate(self, new_status):
        if self._started and new_status == Status.INVALID:
            self.driver.stop()
        self._started = False


class NavigationExec(CommandDrivenAction):
    """navigate() — delegated to Nav2, held RUNNING until the robot actually arrives.

    ## Why this does not just use the driver's result

    Same trap `ReturnNavigation` documents: `/fleet_cmd_result` for a `goal` is the answer
    to "did you take my order", not "did you get there" — `ros_bridge.send_nav_goal()` is
    fire-and-forget. Ending on that result made a 20-second drive look like a 0.2-second
    behaviour, which cost us four real things:

      - the panel showed `AwaitingCommand` while the robot was driving, so the tree could
        not tell anyone what the robot was doing
      - `CommandTimeout` guarded the acknowledgement round-trip, not the drive
      - a drive that ended without arriving (nav2 ABORTED, or a preempted goal reporting
        the *previous* goal's completion) looked like success, so nothing re-drove it —
        measured: a robot stood still for 6m40s
      - `stop_request` had no drive left inside the tree to stop

    So arrival is judged the same way docking is: against a real position, not an ack.

    ## Retargeting instead of restarting

    `nav_target` changes mid-drive by design — fleet_node grants the next node once the
    robot is within `arrive_radius`, while nav2 is still closing the last few centimetres.
    A new target is therefore the normal case, not an error, and it must flow straight
    through to a new `goal`: that hand-off is what keeps motion smooth instead of stopping
    at every node. The leaf stays RUNNING across it — one continuous drive, many targets.

    ## Failure

    `arrive_timeout_sec` since the last target change with no arrival → FAILURE, which
    clears `active_command` and lets the dispatch Selector fall through to
    `AwaitingCommand`; `CommandTimeout` then carries the robot to ERROR if no new command
    comes. Never hang silently — that is the failure mode this class exists to remove.
    """

    def __init__(self, driver, arrive_tolerance: float, arrive_resend_sec: float,
                 arrive_timeout_sec: float, name: str | None = None, now_fn=time.monotonic):
        super().__init__(driver, handles={"navigate"}, name=name or "NavigationExec")
        self.arrive_tolerance = arrive_tolerance
        self.arrive_resend_sec = arrive_resend_sec
        self.arrive_timeout_sec = arrive_timeout_sec
        self._now = now_fn
        self._target = None
        self._target_at = 0.0
        self._sent_at = None
        #: nav2 에 goal 을 냈고 **아직 안 끊었다.** `_started` 와 별개로 둔다.
        #
        # `_started` 는 "이 leaf 가 지금 일을 쥐고 있나"이고, 이건 "바깥 세계에 내가 남긴
        # 것이 있나"다. 도착 판정(`_release`)이 `_started` 를 내리는 순간 둘이 갈라지는데,
        # 그때가 정확히 사고 지점이었다:
        #
        #   도착 판정은 **내 pose 가 5cm 안에 들어왔나**만 본다(arrive_tolerance_m, params.yaml).
        #   nav2 는 같은 순간 아직 자기 goal 을 들고 최종 접근·목표 회전·회복 중일 수 있다.
        #   그 뒤 상태가 IDLE 로 바뀌면 `terminate(INVALID)` 가 오는데 `_started` 가 이미
        #   False 라 **stop 을 안 보낸다.** nav2 goal 은 살아 있고 바퀴는 돈다.
        #   → 사용자 신고: "대기상태인데 움직이는 경우도 있었어" (2026-07-28)
        #
        # ⚠️ 도착할 때 바로 끊지 않는 이유: `PatrolNavigation` 도 도착에서 `_release` 를
        #    탄다(`_arrived_status()` 가 RUNNING). 거기서 끊으면 순회가 **노드마다** 죽은
        #    듯 서고, 다음 노드 허가가 올 때까지 정지해 있는다. 끊는 것은 이 leaf 가
        #    **진짜 끝날 때**(무효화)여야 한다.
        self._goal_outstanding = False

    def setup(self, **kwargs):
        super().setup(**kwargs)
        self.blackboard.register_key(key=Keys.NAV_TARGET, access=Access.READ)
        self.blackboard.register_key(key=Keys.ROBOT_POSE, access=Access.READ)

    def initialise(self):
        super().initialise()
        self._target = None
        self._sent_at = None

    # ── 도착·대기 때 무엇을 돌려줄지 (PatrolNavigation 이 갈아끼운다) ────────
    #
    # 몰고 가는 방식은 배달이든 순회든 같다 — "관제가 허가한 노드로 가라". 다른 건
    # **도착했을 때뿐**이다: 배달은 그 다리가 끝나고, 순회는 한 노드를 지났을 뿐이다.
    # 그 차이만 훅으로 두고 주행 로직은 한 벌만 유지한다.

    def _idle_status(self) -> Status:
        """줄 명령이 없을 때. FAILURE 라야 dispatch Selector 가 다음 처리기로 간다."""
        return Status.FAILURE

    def _arrived_status(self) -> Status:
        return Status.SUCCESS

    def update(self) -> Status:
        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND) not in self.handles:
            # 슬롯은 **건드리지 않는다.** 남의 명령(perform_action 등)을 여기서 지우면
            # 뒤에 있는 ArmExec 이 그 명령을 영영 못 받는다.
            # 다만 내보낸 주행은 끊는다 — `_abandon` 참고. 안 끊으면 옛 nav2 목표가
            # 새 명령과 같은 `/cmd_vel` 을 다툰다(중재자 없음).
            self._target = None
            self._sent_at = None
            self._abandon()
            return self._idle_status()

        target = bb.get(self.blackboard, Keys.NAV_TARGET)
        if target is None:
            # 좌표 없는 navigate 는 providers 가 막지만, 그래도 여기서 끝낸다 —
            # 실행할 수 없는 명령을 붙들고 RUNNING 으로 있으면 아무도 못 알아챈다.
            return self._give_up()

        now = self._now()
        if target != self._target:      # 새 목적지 — 그대로 이어서 보낸다
            self._target = target
            self._target_at = now
            self._sent_at = None        # 아직 안 보냈다

        # 도착 판정을 **보내기 전에** 한다. 이미 그 자리에 서 있는 노드를 허가받는 일이
        # 있는데(관제가 로봇이 있는 칸을 다음 노드로 줄 때), 그때 goal 을 한 번 내고
        # 곧바로 성공하면 nav2 가 헛돌고 직전 주행만 끊긴다.
        if self._arrived(target):
            return self._release(self._arrived_status())

        if self._sent_at is None or now - self._sent_at >= self.arrive_resend_sec:
            # 처음이거나, 같은 목적지인데 아직 못 갔다 — 주행이 도착 없이 끝났을 수 있다.
            self.driver.start()
            self._started = True
            self._goal_outstanding = True
            self._sent_at = now
        elif self.driver.poll() == "failure":
            return self._give_up()      # 실행 층이 거부했다 (링크 끊김 등)

        if now - self._target_at >= self.arrive_timeout_sec:
            return self._give_up()
        return Status.RUNNING

    def _arrived(self, target) -> bool:
        """도착했나. 위치를 모르면 **아직 아니다** — 모르는 걸 도착으로 치지 않는다."""
        pose = bb.get(self.blackboard, Keys.ROBOT_POSE)
        if not pose:
            return False
        return math.hypot(pose["x"] - target["x"], pose["y"] - target["y"]) <= self.arrive_tolerance

    def _release(self, status: Status) -> Status:
        """주행 진행 상태까지 같이 버린다 — 남으면 다음 주행이 옛 목적지를 이어받는다.

        ⚠️ 여기서 nav2 goal 을 끊지 **않는다.** 순회는 도착에서도 이 경로를 타므로
        (`PatrolNavigation._arrived_status()` = RUNNING) 여기서 끊으면 노드마다 선다.
        끊는 것은 `_cancel_goal` 이고, 부르는 자리는 이 leaf 가 진짜 끝날 때다.
        """
        self._target = None
        self._sent_at = None
        return super()._release(status)

    def _give_up(self) -> Status:
        # 실패한 주행은 **지금** 끊는다. 도착과 다르다 —
        #   도착: nav2 도 같은 목표에 닿았으니, 최종 접근을 마치도록 두고
        #         `terminate(INVALID)` 때 끊는다(안 그러면 순회가 노드마다 선다).
        #   실패: 못 갔다는 뜻이라 nav2 목표가 **확실히 stale** 하다. 남기면 다음 명령과
        #         `/cmd_vel` 을 다투고, 아무도 안 끊으면 IDLE 에서도 계속 돈다.
        # 여기서 안 내리면 플래그가 leaf 재진입 뒤까지 살아남아, 나중의 INVALID 가
        # **형제가 방금 낸 목표**를 대신 취소할 수 있다(stop 은 id 별이 아니라 전역 취소다).
        self._cancel_goal()
        return self._release(Status.FAILURE)

    def _cancel_goal(self) -> None:
        """nav2 에 남긴 goal 을 끊는다. 남긴 게 없으면 아무것도 안 한다."""
        if self._goal_outstanding:
            self.driver.stop()
            self._goal_outstanding = False

    def _abandon(self) -> None:
        self._cancel_goal()
        super()._abandon()

    def terminate(self, new_status):
        # `_started` 가 아니라 `_goal_outstanding` 을 본다. 도착 판정이 `_started` 를
        # 내린 뒤에도 nav2 는 자기 goal 을 들고 있을 수 있고, 그 상태로 상태 전이가
        # 오면 아무도 안 끊어 **대기 상태에서 바퀴가 돌았다**(`_goal_outstanding` 주석).
        if new_status == Status.INVALID:
            self._cancel_goal()
        super().terminate(new_status)


#: 안내 중 감시 세션을 다시 알리는 주기(초). `follow_node` 의 고아 판정
#: (`GUIDE_ORPHAN_SEC`)보다 넉넉히 짧아야 살아 있는 안내가 고아로 안 잡힌다.
WATCH_RENEW_SEC = 15.0


class GuideExec(NavigationExec):
    """guide() — 목적지까지 몰되, **요청자가 따라오는지 보면서** 몬다.

    ## 왜 NavigationExec 을 그대로 못 쓰나

    주행 자체는 똑같다(목적지로 goal, 도착은 실좌표로 판정). 다른 건 하나뿐이다 —
    **안내는 혼자 도착하면 실패다.** 안내받는 사람을 두고 먼저 가버리면 목적지에 닿아도
    아무것도 안내하지 못한 것이다. 그래서 요청자가 안 보이면 멈추고 기다린다.

    ## 세 갈래

        보인다              → 평소대로 몬다
        잠깐 안 보인다       → **멈춘다**(nav 취소) 그리고 기다린다. 다시 보이면 이어서 몬다
        오래 안 보인다       → FAILURE (안내 종료)

    잠깐 안 보이는 걸 곧바로 실패로 치지 않는 이유는 회복 BT 가 Hold 를 맨 앞에 두는
    이유와 같다 — 사람이 서가 뒤로 한 발 들어가는 것만으로 안내가 끊기면 못 쓴다.

    ## 멈추는 방법

    `mission_stop` 을 실행 층에 한 번 보낸다 → `fleet_link` → `ros_bridge.cancel_nav()`.
    보내지 않고 그냥 RUNNING 으로 있으면 **nav2 는 계속 달린다** — 화면만 "기다리는 중"이고
    로봇은 사람을 두고 가버린다. 다시 보이면 `_sent_at=None` 으로 되돌려 goal 을 새로 낸다.

    ## 감시가 아예 안 돌 때 (requester_visible=None)

    감시가 없는 것은 요청자가 보인다는 뜻이 아니다. 등록자를 확인해야 하는 길잡이에서는
    안전하게 ``미확인`` 으로 처리한다. 명령 접수 시각부터 grace 를 준 뒤 nav2 를 취소한다.
    """

    def __init__(self, driver, arrive_tolerance: float, arrive_resend_sec: float,
                 arrive_timeout_sec: float, coast_sec: float, wait_sec: float,
                 stop_driver=None, watch_driver=None, far_area_min: float = 0.0,
                 near_area_max: float = 0.0, junctions=None,
                 junction_hold_sec: float = 0.0, result_fn=None,
                 name: str | None = None, now_fn=time.monotonic):
        super().__init__(driver, arrive_tolerance, arrive_resend_sec, arrive_timeout_sec,
                         name=name or "GuideExec", now_fn=now_fn)
        self.handles = {"guide"}
        #: α-β 예측으로 **계속 가는** 시간. 서가·기둥에 잠깐 가리는 것을 흡수한다.
        #: 이 안에서는 nav2 목표를 건드리지 않는다.
        self.coast_sec = coast_sec
        #: 코스팅이 끝난 뒤 **서서 기다리는** 시간. 이 동안 로봇은 정지해 있고,
        #: 다 지나면 회복 BT 에 바퀴를 넘긴다.
        self.wait_sec = wait_sec
        #: 없으면 멈출 수단이 없다 — 그 경우 멈추는 척하지 않고 로그로 드러낸다.
        self.stop_driver = stop_driver
        #: 뒷캠 감시 세션을 켜고 끈다. 없으면 감시가 안 돌아 요청자 가시성이 None 이 되고,
        #: 그러면 아래 판단이 전부 "감시 없음 → 그냥 주행"으로 흘러간다.
        self.watch_driver = watch_driver
        #: 요청자가 이보다 작게 보이면(= 멀면) 멈춰 기다린다. 0 이면 끔.
        self.far_area_min = far_area_min
        #: 앞을 막은 사람이 이보다 크게 보이면(= 가까우면) 멈춘다. 0 이면 끔(기본).
        self.near_area_max = near_area_max
        #: 갈림길 좌표 집합. 여기 닿으면 잠깐 서서 확인한다.
        self.junctions = junctions
        self.junction_hold_sec = junction_hold_sec
        #: `result_fn(cmd_id, ok, msg)` — 이 홉의 결과를 `/fleet_cmd_result` 로 올린다.
        #: 주입 안 하면(시험·팔 없는 배포) 아무 데도 안 알린다.
        self.result_fn = result_fn
        self._halted = False
        self._watching = False
        self._junction_until = None
        self._junction_done = set()
        #: 이 leaf 가 안내를 맡은 시각. 요청자를 **한 번도 못 본** 안내에서 유예를 재는
        #: 기준이다 — 근거는 `_lost_for` 의 ⚠️ 주석(명령 접수 시각은 우리가 밀어 올린다).
        self._guard_since = None
        #: 뒷캠 감시를 켠 시각. 이보다 오래된 목격은 **앞캠 등록** 것이라 안 친다
        #: (`_back_cam_seen_at`).
        self._watch_since = None
        #: 지금 회복 회전을 허가해 뒀나. `follow_node` 는 기본적으로 길잡이의 `/cmd_vel`
        #: 을 삼키므로(`_publish_for_role`), 회전이 필요할 때만 켜 준다.
        self._rotate_allowed = False
        #: `mission_stop` 의 **결과가 돌아왔나.** 회전 허가의 전제다 — `_stop_settled`.
        self._stop_acked = False
        #: 감시 세션을 마지막으로 재발행한 시각. 살아 있다는 신호다 — 위 주석.
        self._watch_renewed_at = None
        #: 이번 소실에서 **어느 홉 id 로** 알렸나. 20Hz 로 같은 결과를 쏟으면 FMS 가
        #: 같은 홉을 반복해서 닫으므로 한 번만 보낸다.
        #: ⚠️ bool 이 아니라 **id** 를 들고 있는 이유: 소실 중에도 FMS 가 새 홉을 내릴 수
        #:    있는데(늦게 도착한 명령), 그때 래치가 bool 이면 새 홉은 영영 못 닫는다.
        #:    id 가 바뀌면 **새 사건**으로 본다(codex 검토 2026-08-02 P1).
        self._lost_reported_id = None

    def setup(self, **kwargs):
        super().setup(**kwargs)
        self.blackboard.register_key(key=Keys.REQUESTER_VISIBLE, access=Access.READ)
        self.blackboard.register_key(key=Keys.REQUESTER_SEEN_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.REQUESTER_AREA, access=Access.READ)
        self.blackboard.register_key(key=Keys.GUIDE_SEARCH_FAILED, access=Access.READ)
        self.blackboard.register_key(key=Keys.GUIDE_CMD_ID, access=Access.READ)
        self.blackboard.register_key(key=Keys.COMMAND_RECEIVED_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
        # 유지 시간(HOLD_UNTIL)을 뚫으려면 이 표시가 필요하다 — `_release` 주석.
        self.blackboard.register_key(key=Keys.COMMANDED_MODE, access=Access.WRITE)

    def _release(self, status: Status) -> Status:
        """안내가 끝났음을 **스스로 알린다.**

        배달은 FMS 가 다리 완료를 알고 `task_done` 을 보내주지만, 길잡이를 시킨 건 패널이고
        FMS 는 로봇이 도착했는지 모른다. 그래서 여기서 안 내보내면 `active_command` 만 비고
        dispatch Selector 가 `AwaitingCommand` 로 떨어져 **WORKING 에 그대로 남는다** —
        120초 뒤 `CommandTimeout` 이 ERROR 로 보낼 때까지. (화면은 그 ERROR 도 "안내 종료"로
        보여준다.)

        ⚠️ `LAST_COMMAND` 에 `task_done` 을 쓰는 방법은 **안 통한다.** 두 번 막힌다:
          1. 이 leaf 가 SUCCESS 를 내면 `Parallel(SuccessOnOne)` 이 거기서 끝나 같은 tick 에
             `CommandListener` 가 안 돈다.
          2. 다음 tick 에는 `Topics2BB` 가 provider 값(None)으로 덮어써 쓴 값이 사라진다.
        `NEXT_MODE` 는 Sequence 의 마지막 `RequestTransition` 이 **같은 tick 에** 읽으므로
        둘 다 피한다. (test_guide_exec 의 브랜치 통합 시험 두 개가 이걸 붙들고 있다 —
        LAST_COMMAND 방식으로 되돌리면 바로 빨개진다.)

        성공도 실패도 PATROL 이다 — `CommandListener` 의 task_done/task_failed 매핑과 같다.
        """
        self._release_watch()
        # ⚠️ [2026-08-02] **`COMMANDED_MODE` 도 같이 세운다 — 안 세우면 유지 시간에 막힌다.**
        #
        #   실측 로그(Pi, 02:25:45):
        #       [WARN] follow_admin 실패: 추종 실패 — 대상을 놓쳤습니다
        #       [WARN] 전이 요청이 적용되지 않았다: WORKING -> PATROL
        #   `RequestTransition` 까지 도달은 했는데 `_held()` 가 거부했다. 패널이 세션을
        #   켤 때 `state_io.apply_pending()` 이 `HOLD_UNTIL = now + manual_hold_sec`
        #   (params.yaml 기준 **300초**)를 찍어 두기 때문이다. 세션이 그 안에 끝나면
        #   — 흔한 일이다 — 전이가 통째로 막힌다. 그리고 `request_transition.py` 머리말대로
        #   WORKING 브랜치에서 한 번 막힌 전이는 **미뤄지는 게 아니라 유실된다.**
        #
        #   `_held()` 는 `COMMANDED_MODE == target` 이면 뚫어 준다. 그 예외의 뜻은
        #   "사람/관제가 시킨 일의 결과는 유지 시간이 막지 않는다" 이고, 세션 종료는
        #   정확히 그것이다 — 배달의 `task_done` 과 같은 자리다. 그래서 여기서 표시한다.
        self.blackboard.set(Keys.NEXT_MODE, "PATROL")
        self.blackboard.set(Keys.COMMANDED_MODE, "PATROL")
        # ⚠️ [2026-08-02] **실패로 끝나도 SUCCESS 를 돌려준다. 안 그러면 전이가 유실된다.**
        #
        #   실측(Pi, 1785641373.92):
        #       [WARN] 전이 요청이 적용되지 않았다: WORKING -> PATROL
        #              (패널 유지 시간 중이거나, 브랜치가 RequestTransition 에 닿지 못했다)
        #   경고문의 뒷쪽이 맞았다. WorkingBranch 는
        #       Sequence[ IsMode, undock, Parallel(SuccessOnOne)[Dispatch, Watchdog],
        #                 RequestTransition ]
        #   이고, 여기서 FAILURE 를 내면 dispatch Selector 가 **뒤로 흘러
        #   `Running("AwaitingCommand")` 에 닿는다.** 그러면 Parallel 이 RUNNING 을
        #   유지해 Sequence 가 `RequestTransition` 까지 **영영 못 간다** — 방금 세운
        #   `NEXT_MODE=PATROL` 이 블랙보드에 남은 채 로봇은 WORKING 에 갇힌다.
        #   배달이 멀쩡했던 이유는 `CommandListener`(watchdog 쪽)가 `task_done` 으로
        #   SUCCESS 를 내 Parallel 을 끝내 주기 때문이다. 길잡이엔 그게 없다.
        #
        #   그래서 **성공이든 실패든 SUCCESS 로 닫는다.** 여기서 SUCCESS 의 뜻은
        #   "안내를 잘 마쳤다" 가 아니라 **"이 leaf 의 일이 끝났다"** 이고, 끝난 뒤
        #   갈 곳은 어느 쪽이든 PATROL 로 이미 같다(`_COMMAND_MAP` 의
        #   task_done/task_failed 매핑과 같은 규칙).
        #
        #   ⚠️ [2026-08-02] **그래서 성공과 실패를 밖에서 구별할 방법이 없다.**
        #      예전 주석은 "실패 사실은 화면에 phase 로 따로 나간다" 고 적었는데
        #      **그런 통로가 없다**(2026-08-02 전수 확인):
        #        · `_give_up` 은 `Keys.ERROR_CODE` 를 안 쓴다 — `mission_stop` 은
        #          nav2 를 끊는 것이지 결과 보고가 아니다
        #        · `/libi/fsm_state` 가 싣는 건 상태·배터리·도킹뿐이고, 패널은
        #          `error_code` 조차 버린다(`RobotController.cpp` `Q_UNUSED`)
        #      패널은 "WORKING 을 벗어났다" 하나만 보고 **전부 완료로 적는다.**
        #      동작은 문제없다(양쪽 다 PATROL 로 가고 등록도 지워진다) — 기록만
        #      부정확하다. 구별이 필요해지면 결과 신호를 새로 내야 한다.
        #
        #   ⚠️ `FollowExec._release` 도 같은 이유로 SUCCESS 를 돌려준다. 둘 중 하나만
        #      고치면 나머지가 조용히 갇힌다.
        return super()._release(Status.SUCCESS)

    def initialise(self):
        super().initialise()
        self._halted = False
        self._junction_until = None
        self._junction_done = set()
        self._guard_since = None
        self._watch_since = None
        self._rotate_allowed = False
        self._stop_acked = False

    def _recover_at(self) -> float:
        """회복 BT 에 바퀴를 넘기는 시각(소실 기준 초). 코스팅 + 대기다."""
        return self.coast_sec + self.wait_sec

    def _lost_for(self) -> float:
        """요청자가 안 보이거나 감시가 없는 지 몇 초인가."""
        visible = bb.get(self.blackboard, Keys.REQUESTER_VISIBLE)
        if visible:
            return 0.0
        seen_at = self._back_cam_seen_at()
        if not seen_at:
            # 감시는 도는데 **한 번도 못 봤다**. 시작부터 아무도 없었다는 뜻이라
            # 유예를 줄 기준 시각이 없다 — 이 leaf 가 안내를 맡은 시각을 기준으로 삼는다.
            #
            # ⚠️ [2026-08-02] **`COMMAND_RECEIVED_AT` 을 쓰면 안 된다 — 우리가 계속 밀어
            #    올린다.** `providers._on_cmd` 는 `/fleet_cmd` 로 **어떤 메시지가 오든**
            #    맨 앞에서 `_command_received_at = monotonic()` 을 찍는다(providers.py:207).
            #    그런데 이 leaf 자신이 같은 토픽으로 `goal`(재전송) · `guide_watch` ·
            #    `mission_stop` 을 낸다. 그게 도로 구독돼 기준 시각이 **매번 지금으로
            #    갱신되므로**, 감시가 죽어 요청자를 한 번도 못 본 안내에서 `lost` 가
            #    0.5초 유예를 영영 못 넘긴다 — **아무도 안 멈춘다.**
            #
            #    `test_no_watcher_fails_safe_after_grace` 가 이걸 못 잡은 이유는 그 시험이
            #    `COMMAND_RECEIVED_AT` 을 95.0 으로 **고정**해 두기 때문이다. 실기에서는
            #    그 값이 움직이는 과녁이다.
            #
            #    그래서 이 leaf 가 안내를 잡은 시각(`_guard_since`)을 쓴다. 이건 우리가
            #    `initialise`/첫 tick 에 한 번만 찍고 아무도 안 건드린다.
            #    (여기 값은 **포기 시각(`_recover_at`)** 을 재는 데만 쓰인다. 출발 여부는
            #     아래 `_never_confirmed` 가 시간과 무관하게 막는다.)
            seen_at = self._guard_since or self._now()
        return max(0.0, self._now() - seen_at)

    def _back_cam_seen_at(self) -> float:
        """**뒷캠으로** 요청자를 마지막으로 본 시각. 앞캠 목격은 안 친다.

        ⚠️ [2026-08-02] **앞캠에서 본 것을 뒷캠 안내의 확인으로 쓰면 안 된다.**

          등록은 **앞캠**이다 — 이용자가 패널을 누르고 있으니 로봇 앞에 있다
          (`GuideScreen.qml` 의 `publishWatch("front")`). 그 등록 감시도 role 이
          WATCH 라 `follow_node._publish_requester` 가 `requester_visible=True` 를
          발행하고, providers 가 `REQUESTER_SEEN_AT` 을 찍는다.

          그 값을 그대로 믿으면, 출발할 때 뒷캠에 **아무도 없어도** "이미 확인함"이
          되어 로봇이 그냥 떠난다. 이용자는 아직 로봇 앞에 서 있는데.

          안내의 전제는 "뒤에 따라온다" 이므로 확인도 **뒷캠에서 다시** 받아야 한다.
          `guide_watch`(뒷캠 전환)를 보낸 시각보다 오래된 목격은 앞캠 것이므로 버린다.

        ⚠️ 무효화를 여기(BT)에서 하는 이유: 이건 **안내의 규칙**이지 카메라의 규칙이
           아니다. `follow_node` 에서 카메라 전환 때 False 를 쏘게 하면 추종에도
           없던 부작용이 생기고, 서비스를 하나 더 건드린다(사용자 확인: "BT로").
        """
        seen_at = bb.get(self.blackboard, Keys.REQUESTER_SEEN_AT) or 0.0
        if not seen_at:
            return 0.0
        if self._watch_since is not None and seen_at < self._watch_since:
            return 0.0          # 뒷캠으로 바꾸기 **전**의 목격 — 앞캠 것이다
        return seen_at

    def _never_confirmed(self) -> bool:
        """요청자를 **뒷캠으로** 한 번도 확인하지 못했나.

        ⚠️ 이 경우에는 코스팅을 주지 않는다. 코스팅(`coast_sec`)의 뜻은 "보고 있던
        사람이 서가 뒤로 한 발 들어갔다" 이지 "누구를 안내하는지 아직 모른다" 가 아니다.
        확인 전에 유예만큼이라도 굴리면, 아무도 없는데 로봇이 먼저 출발한다.

        확인되면 `_back_cam_seen_at()` 이 값을 돌려주고 다시는 여기로 안 온다 —
        그 뒤의 소실은 평소대로 유예를 받는다.

        사용자 스펙(2026-08-02): "뒷캠에 바로 보이면 바로 그냥 출발" — 카운트다운 없이
        **첫 뒷캠 검출 즉시** 출발한다. 그러니 이 검사가 곧 출발 조건이다.
        """
        if bb.get(self.blackboard, Keys.REQUESTER_VISIBLE):
            return False
        return not self._back_cam_seen_at()

    def update(self) -> Status:
        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND) not in self.handles:
            self._release_watch()
            self._halted = False
            self._guard_since = None
            return super().update()

        # 우리가 안내를 맡은 시각. 여기서 한 번만 찍고 아무도 안 건드린다 — 요청자를
        # 한 번도 못 본 경우의 유예 기준이다(`_lost_for` 주석).
        if self._guard_since is None:
            self._guard_since = self._now()

        # 감시 세션을 켠다. 이게 없으면 requester_visible 발행자가 없어 아래 판단이
        # 전부 "감시 없음 → 그냥 주행"으로 흘러간다.
        if not self._watching and self.watch_driver is not None:
            self.watch_driver.start()
            self._watching = True
            self._watch_renewed_at = self._now()
            # 이 시각보다 **앞선** 목격은 앞캠 등록 것이다 — `_back_cam_seen_at` 참고.
            self._watch_since = self._now()
        elif (self._watching and self.watch_driver is not None
              and self._now() - (self._watch_renewed_at or 0.0) >= WATCH_RENEW_SEC):
            # ⚠️ [2026-08-02] **살아 있다는 신호를 주기적으로 보낸다.**
            #
            #   `follow_node` 는 이제 GUIDE 세션이 있는 동안 패널의 `watch` 요청을
            #   거절한다(길잡이 감시를 패널 lease 가 10초마다 덮어 회복 트리를 리셋하던
            #   버그를 막으려고). 그런데 GUIDE 세션은 **lease 면제**다
            #   (`session.py:80` — BT 가 열고 BT 가 닫는다는 전제).
            #
            #   그 둘이 겹치면 **잠금**이 된다: `fsm_node` 가 중간에 죽으면 GUIDE 세션을
            #   닫을 주체가 사라지고, 그 세션은 영원히 남아 패널이 길잡이 등록 화면을
            #   **다시는 못 연다.** 재부팅 말고는 길이 없다.
            #
            #   그래서 이쪽이 주기적으로 재발행해 "아직 내가 안내 중" 을 알린다. 그러면
            #   `follow_node` 가 **끊긴 지 오래된 GUIDE 세션은 넘겨줄 수 있다**(그쪽
            #   `_guide_orphaned` 참고). 패널이 자기 watch 를 갱신하는 것과 같은 규칙이다.
            #
            #   ⚠️ 재발행은 세션을 **재시작시키지 않는다** — `follow_node._on_cmd` 의
            #      "같은 역할이면 인자만 갱신" 분기를 탄다. 그 분기가 없으면 이 재발행이
            #      회복 트리를 주기적으로 리셋한다(정확히 막으려던 그 버그).
            self.watch_driver.start({"camera": "back",
                                     "allow_rotate": bool(self._rotate_allowed)})
            self._watch_renewed_at = self._now()

        lost = self._lost_for()
        # ⚠️ [2026-08-02] **회복 BT 가 "다 훑고도 못 찾았다" 고 말하면 그때 끝낸다.**
        #
        #   예전에는 `guide_lost_timeout_sec`(시계)가 이 자리에 있었다. 회복 트리가
        #   실제로 언제 끝나는지 아무도 안 알려줬기 때문이다 — 감시 세션은
        #   `follow_node._active_id` 를 안 세우므로 `FollowSession.poll()` 의 실패가
        #   `tick()` 의 `if self._active_id is None: return` 에 걸려 **결과가 발행되지
        #   않았다.** 그래서 "회복이 끝났다" 와 "N 초가 지났다" 가 다른 사건인데 하나로
        #   뭉개졌고, 트리 타임라인을 고칠 때마다 params 숫자를 손으로 맞춰야 했다
        #   (실제로 어긋나 있었다 — `control_loop.py` 주석은 45초, params 는 40).
        #
        #   이제 `follow_node._publish_guide_search_failed` 가 매 tick 사실을 낸다.
        #   ⚠️ `None`(감시 없음·발행 끊김)은 **끝난 것으로 치지 않는다** —
        #      `providers._fresh_guide_search_failed` 의 판정 근거 주석 참고.
        if bb.get(self.blackboard, Keys.GUIDE_SEARCH_FAILED) is True:
            self._halt()
            return self._give_up()
        # ⚠️ [2026-08-02] **뒷캠으로 한 번도 확인 못 했으면 유예에서 바로 포기한다.**
        #
        #   사용자 스펙: "처음에 안내 시작하고 안 보이면 알아서 순찰 모드".
        #   회복이 끝나기를 기다릴 이유가 없다 — **회복할 대상이 애초에 없기 때문이다.**
        #   회복 탐색은 "보던 사람을 다시 찾는 것" 이지 "아직 안 온 사람을 기다리는 것"
        #   이 아니다. 그 구간을 세워 두면 이용자는 로봇이 왜 안 가는지 모른 채 두 배를
        #   기다린다.
        #
        #   확인된 뒤의 소실은 이 분기로 안 온다(`_never_confirmed` 가 False) —
        #   그쪽은 정상대로 유예 → 회복 → 회복 실패 신호를 밟는다.
        if self._never_confirmed() and lost >= self._recover_at():
            self._halt()
            return self._give_up()
        # ── 소실 뒤 세 단계 (사용자 스펙 2026-08-02) ────────────────────────
        #
        #   [0, coast_sec)                 α-β 로 **계속 간다** — 잠깐 가린 것
        #   [coast_sec, coast+wait)        **정지하고 기다린다**
        #   [coast+wait, …)                회복 BT 에 바퀴를 넘긴다
        #
        # ⚠️ [2026-08-02] 예전에는 1·2 단계가 통째로 뒤집혀 있었다. `lost_grace_sec`
        #    (20초)까지 **계속 주행**하고 그 뒤에야 `_halt()` 를 불렀다 — 즉 사람을
        #    놓친 채로 20초를 더 갔다. params 주석은 "이 구간 동안 로봇은 서서
        #    기다린다" 라고 적혀 있었는데 코드는 반대였다.
        if self._never_confirmed() or lost >= self.coast_sec:
            # **정지가 먼저다.** `_halt()` 가 `mission_stop` 을 내 nav2 를 끊고, 그 뒤에야
            # 바퀴를 회복 트리에 넘긴다. 뒤집으면 둘이 같이 `/cmd_vel` 을 민다.
            self._halt()
            # 놓쳤다는 사실을 FMS 에 알린다(홉을 실패로 닫는다) — `_report_lost` 주석.
            self._report_lost()
            # 대기까지 다 지난 **놓쳤다고 판정한** 경우에만 회복 회전을 허가한다.
            #
            # ⚠️ **확인 전(`_never_confirmed`)에는 절대 허가하지 않는다.**
            #    그때 로봇은 아직 출발도 안 했고, 이용자는 로봇 **앞**에서 뒤로 걸어오는
            #    중이다. 거기서 돌면 사람을 쫓아 도는 꼴이 된다.
            #
            #    ⚠️ 시간 조건만으로는 이걸 못 막는다 — `_lost_for()` 는 뒷캠 목격이
            #       없으면 `_guard_since`(안내를 맡은 시각)부터 재므로, 출발 전에도
            #       조건이 성립한다. 주석은 "확인 전엔 안 준다" 인데 코드는 주고 있었다
            #       (리뷰 지적 2026-08-02). **두 조건을 같이 본다.**
            # ⚠️ **nav2 취소가 처리된 뒤에만** 넘긴다 — `_stop_settled` 주석.
            if (not self._never_confirmed()
                    and lost >= self._recover_at() and self._stop_settled()):
                self._allow_rotate(True)
            # 목적지는 그대로 두고 기다린다. 다시 보이면 아래에서 goal 을 새로 낸다.
            return Status.RUNNING

        # 여기까지 왔으면 요청자가 보인다 — 회전 허가를 거둔다. 안 거두면 `follow_node`
        # 가 계속 `/cmd_vel` 을 낼 수 있고, nav2 와 두 주체가 같은 토픽을 민다.
        self._allow_rotate(False)
        # 다시 보인다 = 다음 소실은 **새 사건**이다. 래치를 풀어 그때 또 보고할 수 있게 한다.
        self._lost_reported_id = None

        area = bb.get(self.blackboard, Keys.REQUESTER_AREA)
        if self._too_far(area) or self._too_near(area):
            self._halt()
            return Status.RUNNING

        if self._junction_hold_active():
            self._halt()
            return Status.RUNNING

        if self._halted:
            self._halted = False
            self._sent_at = None     # 취소된 주행을 다시 낸다
        return super().update()

    # ── 거리 게이트 ──────────────────────────────────────────────────────
    def _too_far(self, area) -> bool:
        """보이지만 너무 멀다. `VISIBLE` 만 보면 10m 뒤에 있어도 계속 간다."""
        return self.far_area_min > 0 and area is not None and area < self.far_area_min

    def _too_near(self, area) -> bool:
        """앞을 막을 만큼 가깝다. **기본은 꺼져 있다**(near_area_max=0).

        조종하지 않고 **멈추기만** 한다. 우회는 nav2 가 costmap 으로 하고, 여기서
        같이 조종하면 제어 주체가 둘이 되어 어느 쪽이 이겼는지 로그로 못 가린다.
        """
        return self.near_area_max > 0 and area is not None and area > self.near_area_max

    # ── 갈림길 확인 ──────────────────────────────────────────────────────
    def _junction_hold_active(self) -> bool:
        if self.junction_hold_sec <= 0 or not self.junctions:
            return False
        now = self._now()
        if self._junction_until is None:
            target = bb.get(self.blackboard, Keys.NAV_TARGET)
            key = (round(target["x"], 3), round(target["y"], 3)) if target else None
            # 같은 갈림길에서 두 번 서지 않는다 — goal 을 다시 내면 같은 목적지가
            # 그대로 남아 있어, 제한이 없으면 그 자리에서 영원히 선다.
            if key is None or key in self._junction_done:
                return False
            if not self.junctions.contains(target):
                return False
            self._junction_done.add(key)
            self._junction_until = now + self.junction_hold_sec
        if now >= self._junction_until:
            self._junction_until = None
            return False
        return True

    def _stop_settled(self) -> bool:
        """`mission_stop` 이 **실제로 처리됐나.**

        ⚠️ [2026-08-02, codex 지적 P1] `_halt()` 직후 곧바로 회전을 허가하면, nav2 취소가
           아직 처리되지 않은 짧은 창에서 nav2 와 회복 트리가 **같이 `/cmd_vel` 을 민다.**
           중재자가 없으므로 마지막에 도착한 메시지가 이긴다.

        로봇의 `robot_agent` 는 `mission_stop` 을 큐를 우회해 인라인 실행하고
        `run_and_reply` 로 결과를 돌려준다(`robot_agent/app/core/fleet_link.py:476-478`).
        그러니 그 결과를 기다릴 수 있다 — 새 핸드셰이크를 만들 필요가 없다.

        ⚠️ `poll()` 은 결과를 **꺼내 버리므로**(pop) 한 번만 성공을 돌려준다. 래치해 둔다.
        ⚠️ 결과가 영영 안 오면 회전은 **영영 안 열린다.** 그것이 안전한 쪽이다 —
           취소됐는지 모르는 채로 도는 것보다 안 도는 편이 낫다. 안 돌아도 `Hold` 와
           카메라 전환은 그대로 돌고, 회복 트리가 다 훑으면 `guide_search_failed` 가
           안내를 끝낸다.
        """
        if self._stop_acked:
            return True
        if self.stop_driver is None:
            return False
        if self.stop_driver.poll() == "success":
            self._stop_acked = True
        return self._stop_acked

    def _allow_rotate(self, allow: bool) -> None:
        """회복 회전 허가를 `follow_node` 에 알린다. **바뀔 때만** 보낸다.

        ## 왜 신호가 필요한가

        길잡이 주행은 nav2 가 하고, `follow_node` 는 길잡이의 `/cmd_vel` 을 **삼킨다**
        (`_publish_for_role` — 두 주체가 같은 토픽을 밀면 중재자가 없다). 그래서 회복
        트리가 돌아도 회전 구간이 그냥 기다림이 된다.

        사용자 스펙(2026-08-02)은 **길잡이도 회복 때 회전한다** 이다. 그러려면 nav2 가
        정말 멈춘 뒤에만 바퀴를 넘겨야 하는데, 두 프로세스라 그 사실을 공유할 길이
        없었다. `guide_watch` 통로가 이미 있으므로 거기에 실어 보낸다 — 새 토픽을
        만들지 않는다.

        ⚠️ **허가(True)는 반드시 `_halt()` 다음이다.** 먼저 허가하면 `mission_stop` 이
           나가기 전에 회전이 열려, 아주 짧게나마 nav2 와 회복 트리가 같이 `/cmd_vel`
           을 민다. 반대로 **해제(False)는 `super().update()`(goal 재발행) 전**이어야
           같은 이유로 겹치지 않는다. 지금 `update()` 가 그 순서를 지킨다.

        ⚠️ 매 tick 보내면 안 된다. `guide_watch` 는 세션 명령이라 재발행이 lease 를
           갱신하는 부수효과가 있고, 20Hz 로 쏘면 `/fleet_cmd` 가 그걸로 찬다.
        """
        if allow == self._rotate_allowed:
            return
        self._rotate_allowed = allow
        if self.watch_driver is None:
            return
        self.watch_driver.start({"camera": "back", "allow_rotate": bool(allow)})

    def _release_watch(self):
        if self._watching and self.watch_driver is not None:
            self.watch_driver.stop()
        self._watching = False
        self._watch_since = None
        # 유예 기준 시각도 같이 되감는다. `_release_watch` 는 이 leaf 의 **모든 종료
        # 경로**(_release · terminate · 남의 명령으로 손 뗌)가 지나가는 자리라, 여기서
        # 지우면 다음 안내가 옛 기준으로 유예를 재는 일이 없다.
        self._guard_since = None
        # 세션이 닫히면 허가도 없던 일이 된다. 안 지우면 다음 안내가 **회전이 이미
        # 허가된 상태**로 시작해, 출발 전에 바퀴가 돌 수 있다.
        self._rotate_allowed = False
        self._stop_acked = False
        self._watch_renewed_at = None
        self._lost_reported_id = None

    def _report_lost(self):
        """이 홉을 **실패로 닫는다** — FMS 가 예약을 풀고 로봇을 붙잡게.

        길잡이가 교통관제를 타면서 `guide` 는 fleet_node 가 허가한 홉의 연속이 됐다.
        요청자를 놓친 순간 그 홉은 더 못 간다 — 그런데 아무 말도 안 하면 FMS 는 계속
        "가는 중" 으로 알고 **그 로봇 몫으로 잡아 둔 노드·간선을 안 놓는다.** 다른
        로봇이 그 길을 못 쓴다.

        ⚠️ **같은 홉 id 로는 한 번만** 보고한다(`_lost_reported_id`). 20Hz 로 같은 id 를
           쏟으면 FMS 가 같은 홉을 반복해서 닫고, 그때마다 재배차가 튄다.

        ⚠️ `ok=false` 는 "실패했다" 이지 "안내가 끝났다" 가 아니다. 회복 BT 가 사람을
           다시 찾으면 FMS 가 **새 홉**을 내려준다(새 id). 안내 자체의 끝은
           `guide_search_failed` 가 정한다.
        """
        if self.result_fn is None:
            return
        cmd_id = bb.get(self.blackboard, Keys.GUIDE_CMD_ID)
        if not cmd_id or cmd_id == self._lost_reported_id:
            return
        self._lost_reported_id = cmd_id
        self.result_fn(cmd_id, False, "requester_lost")

    def _halt(self):
        if self._halted:
            return                   # 취소는 한 번만 — 매 tick 보내면 실행 층이 막힌다
        self._halted = True
        self._sent_at = None
        # 새 정지를 내는 참이다 — 옛 ack 는 이 정지의 것이 아니다.
        self._stop_acked = False
        if self.stop_driver is None:
            logging.getLogger(__name__).error(
                "GuideExec: 요청자를 놓쳤는데 정지 수단(stop_driver)이 없어 nav 를 취소하지 못했다 — "
                "로봇은 계속 달린다. registry.build_branches(drivers={'guide_stop': ...}) 로 꽂아라.")
            return
        self.stop_driver.start()

    def terminate(self, new_status):
        # 안내가 어떻게 끝나든(성공·실패·상위 취소) 취소 상태를 남기지 않는다.
        self._halted = False
        # 감시 세션도 **여기서** 닫는다. `_release()` 에만 두면 안 된다 —
        # 그건 이 leaf 가 스스로 끝냈을 때만 돈다. 패널이 IDLE 전이를 걸거나
        # 상위가 브랜치를 바꾸면 leaf 는 `_release()` 없이 INVALID 로 terminate 되고,
        # 그러면 뒷캠 감시 세션이 **열린 채 남는다**(카메라가 back 에 물린 채,
        # `/libi/requester_visible` 도 계속 발행). 화면상으로는 안내가 끝나 있어서
        # 에러도 안 난다. `FollowExec` 의 `_abandon` 과 같은 종류의 구멍이었다.
        #   (2026-07-28 codex 지적 — guide.py:132-141 이 mission_stop 과 별개로
        #    IDLE 전이를 요청하므로 이 경로는 실제로 밟힌다)
        # `_release_watch()` 는 `_watching` 을 보고 한 번만 돌아서 중복 호출이 안전하다.
        self._release_watch()
        super().terminate(new_status)


class ArmExec(CommandDrivenAction):
    """perform_action() — delegated to the arm.

    The grasp/place subtree that belongs inside here is not designed yet; `driver` is the
    seam it will plug into without this branch changing.
    """

    def __init__(self, driver, name: str | None = None):
        super().__init__(driver, handles={"perform_action"}, name=name or "ArmExec")


class UnwiredDriver:
    """Stand-in for a driver that was never supplied.

    Reports the command as FAILED and logs it, rather than either reporting "running"
    forever (a hung robot with no clue why) or raising (an exception here would unwind
    through the tick and out of rclpy.spin(), killing the whole mission node — taking
    PATROL, RETURNING and the ERROR handling down with one unwired command).

    Failing the command instead lets CommandDrivenAction clear active_command, the
    dispatch Selector fall through, and CommandTimeout carry the robot to ERROR through
    the ordinary path: stopped and diagnosable, not dead.

    Not raising at build time either — a robot deployed without libi_perception must
    still get a working mission tree.
    """

    def __init__(self, what: str):
        self.what = what
        self._reported = False

    def _report_once(self):
        if self._reported:
            return              # the tree ticks at 20 Hz; one line is enough
        self._reported = True
        logging.getLogger(__name__).error(
            "%s was dispatched but no driver is wired for it — failing the command. "
            "Pass one through registry.build_branches(drivers={'follow': ...}).",
            self.what,
        )

    def start(self):
        self._report_once()

    def poll(self):
        self._report_once()
        return "failure"

    def stop(self):
        pass        # tearing down something never started is a no-op, not an error


class FollowExec(CommandDrivenAction):
    """follow_admin — delegated to libi_perception.

    The follower's internals (PID tracking action, recovery BT, and the switch between
    them) live entirely in libi_perception and are opaque here. This leaf only drives the
    injected driver's start()/poll()/stop(), so the follow logic can be retuned or
    restructured without libi_modes noticing.
    """

    def __init__(self, driver=None, name: str | None = None):
        super().__init__(driver or UnwiredDriver("follow_admin"),
                         handles={"follow_admin"}, name=name or "FollowExec")

    def setup(self, **kwargs):
        super().setup(**kwargs)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)
        # 유지 시간(HOLD_UNTIL)을 뚫으려면 이 표시가 필요하다 — `_release` 주석.
        self.blackboard.register_key(key=Keys.COMMANDED_MODE, access=Access.WRITE)

    def _release(self, status: Status) -> Status:
        """추종이 끝났음을 **스스로 알린다** — `GuideExec._release` 와 같은 이유다.

        추종을 시킨 건 패널이고, FMS 는 세션이 언제 끝났는지 모른다. 배달처럼 `task_done`
        을 대신 보내줄 주체가 없어서, 여기서 안 내보내면 `active_command` 만 비고 dispatch
        Selector 가 `AwaitingCommand` 로 떨어져 **WORKING 에 그대로 남는다** — 120초 뒤
        `CommandTimeout` 이 ERROR 로 보낼 때까지.

        실측 2026-07-28: 그래서 **관리자 추종이 두 번째부터 안 됐다.** 패널은 "WORKING 을
        벗어남"으로 추종 종료를 판정하는데(RobotController.cpp fsmStateReceived) 그 일이
        영영 안 일어나 `m_following` 이 true 로 남았고, 다음 시도가 화면 안에서
        "이미 추종 중입니다" 로 막혀 HTTP 조차 나가지 않았다.

        `LAST_COMMAND` 에 `task_done` 을 쓰는 방법이 왜 안 통하는지는 `GuideExec._release`
        에 적혀 있다 — 여기도 똑같이 `NEXT_MODE` 를 쓴다.
        """
        # ⚠️ [2026-08-02] **`COMMANDED_MODE` 도 같이 세운다 — 안 세우면 유지 시간에 막힌다.**
        #
        #   실측 로그(Pi, 02:25:45):
        #       [WARN] follow_admin 실패: 추종 실패 — 대상을 놓쳤습니다
        #       [WARN] 전이 요청이 적용되지 않았다: WORKING -> PATROL
        #   `RequestTransition` 까지 도달은 했는데 `_held()` 가 거부했다. 패널이 세션을
        #   켤 때 `state_io.apply_pending()` 이 `HOLD_UNTIL = now + manual_hold_sec`
        #   (params.yaml 기준 **300초**)를 찍어 두기 때문이다. 세션이 그 안에 끝나면
        #   — 흔한 일이다 — 전이가 통째로 막힌다. 그리고 `request_transition.py` 머리말대로
        #   WORKING 브랜치에서 한 번 막힌 전이는 **미뤄지는 게 아니라 유실된다.**
        #
        #   `_held()` 는 `COMMANDED_MODE == target` 이면 뚫어 준다. 그 예외의 뜻은
        #   "사람/관제가 시킨 일의 결과는 유지 시간이 막지 않는다" 이고, 세션 종료는
        #   정확히 그것이다 — 배달의 `task_done` 과 같은 자리다. 그래서 여기서 표시한다.
        self.blackboard.set(Keys.NEXT_MODE, "PATROL")
        self.blackboard.set(Keys.COMMANDED_MODE, "PATROL")
        super()._release(status)
        # CommandDispatch 의 마지막 `AwaitingCommand` 은 항상 RUNNING 이다. recovery
        # 실패를 그대로 돌려주면 Selector 가 그 fallback 으로 넘어가 RequestTransition
        # 에 닿지 못해 NEXT_MODE 가 남고 WORKING 에 갇힌다. 추종 종료 자체는 성공으로
        # 닫아 같은 tick 에 예약한 PATROL 전이를 적용한다.
        return Status.SUCCESS
