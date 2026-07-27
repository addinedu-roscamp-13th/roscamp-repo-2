"""복귀 진입부 5단계와, 그 실패를 흡수하는 데코레이터.

## 왜 흡수 데코레이터가 필요한가

이 시퀀스는 `Parallel(SuccessOnOne)` 안에 들어간다. py_trees 의 `Parallel` 은
**정책과 무관하게 자식 하나가 FAILURE 를 내면 즉시 실패한다.** 그러면 형제인
`FaultDetected` 가 SUCCESS 를 낼 tick 조차 없이 브랜치가 죽어 ERROR 전이가 영영
일어나지 않는다.

기존 `ReturnNavigation` 이 재시도 소진 시 "FAILURE 대신 fault + RUNNING" 을 유지하도록
일부러 짜여 있던 이유가 그것이다. 5단계로 쪼개면 그 보호가 단계마다 사라지므로,
각 단계를 이 데코레이터로 감싼다.

## 왜 회전에 새 /cmd_vel 발행자를 만들지 않나

같은 x·y 에 목표 yaw 만 다른 `goal` 을 보내면 nav2 가 제자리 회전으로 처리한다.
이 시스템에는 `/cmd_vel` 중재자(twist_mux)가 없어서 **발행자를 늘리는 것 자체가
위험**이다 — 마지막에 도착한 명령이 이기므로, 발행자가 하나 늘 때마다 조용한
경합 창이 하나 늘어난다.

## 2단계·5단계는 ArUco 로 갈아끼울 자리다

`FaceParking` 은 지금 좌표만으로 각도를 낸다. nav2 가 방금 그 AMCL 로 입구까지
왔으므로 같은 추정으로 각도만 내는 건 더 쉬운 문제다. 마커가 정말 필요해지는 곳은
**마지막 몇 cm**(`AlignDock`)다 — 거기서만 AMCL 오차가 충전 단자 폭보다 커진다.
"""
import math
import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys


def wrap_angle(a: float) -> float:
    """(-pi, pi] 로 감는다. 안 감으면 179° 와 -179° 가 358° 차이로 보인다."""
    return (a + math.pi) % (2 * math.pi) - math.pi


class AbsorbFailure(py_trees.decorators.Decorator):
    """자식의 FAILURE 를 RUNNING 으로 바꾸고 재시도한다.

    재시도를 다 쓰면 `fault` 를 세우고 **그래도 RUNNING** 을 돌려준다 — FAILURE 는
    `Parallel` 을 죽이고, 그러면 형제 `FaultDetected` 가 그 fault 를 ERROR 전이로
    바꿀 기회조차 없다.
    """

    def __init__(self, child, retry_max: int, name: str | None = None):
        super().__init__(name=name or f"Absorb[{child.name}]", child=child)
        self.retry_max = int(retry_max)
        self._tries = 0

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.FAULT, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.DOCK_RETRY_COUNT, access=Access.WRITE)

    def initialise(self):
        self._tries = 0

    def update(self):
        status = self.decorated.status
        if status != Status.FAILURE:
            return status
        self._tries += 1
        self.blackboard.set(Keys.DOCK_RETRY_COUNT, self._tries)
        if self._tries >= self.retry_max:
            self.blackboard.set(Keys.FAULT, True)
        return Status.RUNNING


class _GoalStep(py_trees.behaviour.Behaviour):
    """좌표로 몰고, **실좌표 거리**로 도착을 판정한다.

    명령 수락 응답(ack)을 도착으로 쓰지 않는다 — `send_nav_goal()` 은 완료를 기다리지
    않으므로 ack 는 "주문 받았다"는 뜻일 뿐 도착과 상관이 없다. 그걸 도착으로 치면
    20초짜리 주행이 0.2초짜리 동작으로 보이고, 도착 없이 끝난 주행을 아무도 다시
    몰아주지 않는다.
    """

    def __init__(self, name, driver, target, tolerance, resend_sec, timeout_sec, now_fn):
        super().__init__(name=name)
        self.driver = driver
        self.target = target                 # (x, y) — 콜러블도 허용
        self.tolerance = float(tolerance)
        self.resend_sec = float(resend_sec)
        self.timeout_sec = float(timeout_sec)
        self._now = now_fn
        self._sent_at = None
        self._started_at = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ROBOT_POSE, access=Access.READ)

    def initialise(self):
        self._sent_at = None
        self._started_at = None

    def _xy(self):
        t = self.target() if callable(self.target) else self.target
        if t is None:
            return None
        return (t["x"], t["y"]) if isinstance(t, dict) else (t[0], t[1])

    def update(self):
        xy = self._xy()
        if xy is None:
            return Status.FAILURE
        now = self._now()
        if self._started_at is None:
            self._started_at = now

        pose = bb.get(self.blackboard, Keys.ROBOT_POSE)
        if pose and math.hypot(pose["x"] - xy[0], pose["y"] - xy[1]) <= self.tolerance:
            return Status.SUCCESS

        if self._sent_at is None or now - self._sent_at >= self.resend_sec:
            # nav2 주행은 도착 없이 끝날 수 있다(ABORTED, 선점). 다시 몰아 줄 주체가
            # 필요하고, 도착 여부를 아는 건 여기뿐이다.
            self.driver.start()
            self._sent_at = now
        elif self.driver.poll() == "failure":
            return Status.FAILURE            # 실행 층이 거부했다(링크 끊김 등)

        if now - self._started_at >= self.timeout_sec:
            return Status.FAILURE
        return Status.RUNNING


class _YawStep(py_trees.behaviour.Behaviour):
    """제자리 yaw 회전. 같은 좌표에 목표 yaw 만 다른 goal 을 보낸다.

    `driver.start(target_yaw)` 를 부른다 — 회전용 드라이버가 현재 x·y 에 그 yaw 를
    붙여 실행 층으로 내보낸다.
    """

    def __init__(self, name, driver, yaw_fn, tolerance_rad, timeout_sec, now_fn):
        super().__init__(name=name)
        self.driver = driver
        self.yaw_fn = yaw_fn
        self.tolerance_rad = float(tolerance_rad)
        self.timeout_sec = float(timeout_sec)
        self._now = now_fn
        self._sent = False
        self._started_at = None
        self._target = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ROBOT_POSE, access=Access.READ)

    def initialise(self):
        self._sent = False
        self._started_at = None
        self._target = None

    def update(self):
        pose = bb.get(self.blackboard, Keys.ROBOT_POSE)
        now = self._now()
        if self._started_at is None:
            self._started_at = now
        if self._target is None:
            # 목표 각도는 **한 번만** 정한다. 매 tick 다시 계산하면, 돌면서 pose 가
            # 바뀌는 만큼 목표도 따라 움직여 영원히 안 닿는다(TurnAround 가 특히 그렇다).
            self._target = self.yaw_fn(pose)
            if self._target is None:
                return Status.FAILURE

        if pose is not None and pose.get("yaw") is not None:
            if abs(wrap_angle(self._target - pose["yaw"])) <= self.tolerance_rad:
                return Status.SUCCESS

        if not self._sent:
            self.driver.start(self._target)
            self._sent = True
        elif self.driver.poll() == "failure":
            return Status.FAILURE

        if now - self._started_at >= self.timeout_sec:
            return Status.FAILURE
        return Status.RUNNING


class AlreadyDocked(py_trees.behaviour.Behaviour):
    """이미 도킹돼 있으면 복귀 주행을 통째로 건너뛴다.

    부팅 상태가 `RETURNING` 이라 **충전소에 놓인 채 켜면** 5단계가 그대로 돈다 —
    입구까지 나갔다가 되돌아온다. 아무 이득 없이 배터리만 쓰고, 15% 미만에서 그러면
    도달 못 할 수도 있다.

    `is_docked` 는 실제 확인 신호이므로, 그게 참이면 복귀는 이미 끝난 것이다.
    """

    def __init__(self, name: str = "AlreadyDocked"):
        super().__init__(name=name)

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.IS_DOCKED, access=Access.READ)

    def update(self):
        return Status.SUCCESS if bb.get(self.blackboard, Keys.IS_DOCKED) else Status.FAILURE


class AlignDock(py_trees.behaviour.Behaviour):
    """정렬 후 **실제 도킹 확인**을 기다린다. 정렬 동작 자리는 뒷캠 ArUco 로 교체한다.

    ## 지금 하는 일 — `is_docked` 를 기다린다

    정렬을 위한 미세 이동은 아직 없다(그 자리가 ArUco 몫이다). 하지만 **도킹 확인까지
    빼면 안 된다.** 즉시 SUCCESS 를 돌려주면 `SetNextMode("CHARGING")` 이 뒤따라
    로봇이 충전소에 닿지도 않은 채 "충전 중"이 된다 — 화면은 멀쩡한데 배터리는
    계속 떨어진다.

    `is_docked` 는 실제 확인 신호다(`dock_confirm.py` 가 주차장 정점 반경으로 판정하고,
    정밀 주차가 붙으면 그쪽이 신호 주체가 된다 — 그때도 이 leaf 는 안 바뀐다).

    확인이 안 오면 timeout 후 FAILURE → `AbsorbFailure` 가 재시도하고, 소진되면
    fault 를 세운다.
    """

    def __init__(self, timeout_sec: float, now_fn, name: str = "AlignDock"):
        super().__init__(name=name)
        self.timeout_sec = float(timeout_sec)
        self._now = now_fn
        self._started_at = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.IS_DOCKED, access=Access.READ)

    def initialise(self):
        self._started_at = None

    def update(self):
        if bb.get(self.blackboard, Keys.IS_DOCKED):
            return Status.SUCCESS
        now = self._now()
        if self._started_at is None:
            self._started_at = now
        if now - self._started_at >= self.timeout_sec:
            return Status.FAILURE
        return Status.RUNNING


def create_return_steps(*, entrance_driver, dock_driver, rotate_driver,
                        entrance_xy, parking_xy, tolerance, resend_sec, timeout_sec,
                        yaw_tolerance_rad, retry_max, dock_confirm_sec=90.0,
                        now_fn=time.monotonic):
    """5단계 시퀀스를 만든다. 각 단계는 실패 흡수 데코레이터로 감싼다."""
    def _face_yaw(pose):
        if pose is None:
            return None
        return math.atan2(parking_xy[1] - pose["y"], parking_xy[0] - pose["x"])

    def _turn_around_yaw(pose):
        if pose is None or pose.get("yaw") is None:
            return None
        return wrap_angle(pose["yaw"] + math.pi)

    steps = [
        _GoalStep("GoToParkingEntrance", entrance_driver, entrance_xy,
                  tolerance, resend_sec, timeout_sec, now_fn),
        _YawStep("FaceParking", rotate_driver, _face_yaw,
                 yaw_tolerance_rad, timeout_sec, now_fn),
        _GoalStep("GoToParking", dock_driver, parking_xy,
                  tolerance, resend_sec, timeout_sec, now_fn),
        _YawStep("TurnAround", rotate_driver, _turn_around_yaw,
                 yaw_tolerance_rad, timeout_sec, now_fn),
        AlignDock(dock_confirm_sec, now_fn),
    ]
    return [AbsorbFailure(s, retry_max) for s in steps]
