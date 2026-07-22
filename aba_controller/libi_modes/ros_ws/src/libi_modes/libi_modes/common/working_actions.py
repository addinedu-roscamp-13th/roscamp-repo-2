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
