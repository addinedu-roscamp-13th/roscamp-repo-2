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
            return Status.FAILURE
        if not self._started:
            self.driver.start()
            self._started = True
        result = self.driver.poll()
        if result in ("success", "failure"):
            self.blackboard.set(Keys.ACTIVE_COMMAND, None)
            self._started = False
            return Status.SUCCESS if result == "success" else Status.FAILURE
        return Status.RUNNING

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
            self._target = None
            self._sent_at = None
            self._started = False
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
        """명령 슬롯을 비우고 상태를 돌려준다. 안 비우면 다음 명령을 못 받는다."""
        self.blackboard.set(Keys.ACTIVE_COMMAND, None)
        self._started = False
        self._target = None
        self._sent_at = None
        return status

    def _give_up(self) -> Status:
        return self._release(Status.FAILURE)


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

    libi_perception 이 없거나 감시를 안 켠 로봇에서는 판단 근거가 없다. 그때는 **그냥
    주행한다** — 근거 없이 멈춰 서 있는 것보다 낫고, 감시 없는 배포에서 길잡이가 통째로
    죽는 것도 막는다. 화면에는 요청자 감시가 없다는 게 phase 로 드러나지 않으므로,
    감시 없는 배포에서 이 브랜치를 쓸지는 운영 결정이다.
    """

    def __init__(self, driver, arrive_tolerance: float, arrive_resend_sec: float,
                 arrive_timeout_sec: float, lost_grace_sec: float, lost_timeout_sec: float,
                 stop_driver=None, watch_driver=None, far_area_min: float = 0.0,
                 near_area_max: float = 0.0, junctions=None,
                 junction_hold_sec: float = 0.0,
                 name: str | None = None, now_fn=time.monotonic):
        super().__init__(driver, arrive_tolerance, arrive_resend_sec, arrive_timeout_sec,
                         name=name or "GuideExec", now_fn=now_fn)
        self.handles = {"guide"}
        self.lost_grace_sec = lost_grace_sec
        self.lost_timeout_sec = lost_timeout_sec
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
        self._halted = False
        self._watching = False
        self._junction_until = None
        self._junction_done = set()

    def setup(self, **kwargs):
        super().setup(**kwargs)
        self.blackboard.register_key(key=Keys.REQUESTER_VISIBLE, access=Access.READ)
        self.blackboard.register_key(key=Keys.REQUESTER_SEEN_AT, access=Access.READ)
        self.blackboard.register_key(key=Keys.REQUESTER_AREA, access=Access.READ)
        self.blackboard.register_key(key=Keys.NEXT_MODE, access=Access.WRITE)

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
        self.blackboard.set(Keys.NEXT_MODE, "PATROL")
        return super()._release(status)

    def initialise(self):
        super().initialise()
        self._halted = False
        self._junction_until = None
        self._junction_done = set()

    def _lost_for(self) -> float:
        """요청자가 안 보인 지 몇 초인가. 보이거나 감시가 없으면 0."""
        visible = bb.get(self.blackboard, Keys.REQUESTER_VISIBLE)
        if visible is None or visible:
            return 0.0
        seen_at = bb.get(self.blackboard, Keys.REQUESTER_SEEN_AT) or 0.0
        if not seen_at:
            # 감시는 도는데 **한 번도 못 봤다**. 시작부터 아무도 없었다는 뜻이라
            # 유예를 줄 기준 시각이 없다 — 명령 접수 시각을 기준으로 삼는다.
            seen_at = bb.get(self.blackboard, Keys.COMMAND_RECEIVED_AT) or self._now()
        return max(0.0, self._now() - seen_at)

    def update(self) -> Status:
        if bb.get(self.blackboard, Keys.ACTIVE_COMMAND) not in self.handles:
            self._release_watch()
            self._halted = False
            return super().update()

        # 감시 세션을 켠다. 이게 없으면 requester_visible 발행자가 없어 아래 판단이
        # 전부 "감시 없음 → 그냥 주행"으로 흘러간다.
        if not self._watching and self.watch_driver is not None:
            self.watch_driver.start()
            self._watching = True

        lost = self._lost_for()
        if lost >= self.lost_timeout_sec:
            self._halt()
            return self._give_up()
        if lost >= self.lost_grace_sec:
            self._halt()
            # 목적지는 그대로 두고 기다린다. 다시 보이면 아래에서 goal 을 새로 낸다.
            return Status.RUNNING

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

    def _release_watch(self):
        if self._watching and self.watch_driver is not None:
            self.watch_driver.stop()
        self._watching = False

    def _halt(self):
        if self._halted:
            return                   # 취소는 한 번만 — 매 tick 보내면 실행 층이 막힌다
        self._halted = True
        self._sent_at = None
        if self.stop_driver is None:
            logging.getLogger(__name__).error(
                "GuideExec: 요청자를 놓쳤는데 정지 수단(stop_driver)이 없어 nav 를 취소하지 못했다 — "
                "로봇은 계속 달린다. registry.build_branches(drivers={'guide_stop': ...}) 로 꽂아라.")
            return
        self.stop_driver.start()

    def terminate(self, new_status):
        # 안내가 어떻게 끝나든(성공·실패·상위 취소) 취소 상태를 남기지 않는다.
        self._halted = False
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
