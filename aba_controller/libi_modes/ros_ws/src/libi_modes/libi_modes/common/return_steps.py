"""복귀 진입부 6단계와, 그 실패를 흡수하는 데코레이터.

## 왜 흡수 데코레이터가 필요한가

이 시퀀스는 `Parallel(SuccessOnOne)` 안에 들어간다. py_trees 의 `Parallel` 은
**정책과 무관하게 자식 하나가 FAILURE 를 내면 즉시 실패한다.** 그러면 형제인
`FaultDetected` 가 SUCCESS 를 낼 tick 조차 없이 브랜치가 죽어 ERROR 전이가 영영
일어나지 않는다.

기존 `ReturnNavigation` 이 재시도 소진 시 "FAILURE 대신 fault + RUNNING" 을 유지하도록
일부러 짜여 있던 이유가 그것이다. 단계로 쪼개면 그 보호가 단계마다 사라지므로,
각 단계를 이 데코레이터로 감싼다.

## 왜 회전에 새 /cmd_vel 발행자를 만들지 않나

같은 x·y 에 목표 yaw 만 다른 `goal` 을 보내면 nav2 가 제자리 회전으로 처리한다.
회전 때문에 발행자를 늘릴 이유가 없다.

⚠️ 아래 ⑤단계(`DockNudge`)는 예외적으로 속도를 직접 낸다. 다만 `/cmd_vel` 로
   **직접 내지 않는다** — twist_mux 의 `dock` 입력(`cmd_vel_dock`, priority 120)으로
   보낸다. `/cmd_vel` 직접 발행은 twist_mux.yaml 이 금지한 것이고, 그 금지는
   "마지막에 도착한 명령이 이긴다"를 없애려고 만든 문이다.

## [2026-07-30] 6단계 재편 — 뒷캠 ArUco 정밀 주차

    ① GoToParkingEntrance   주차장 입구로 주행           (그대로)
    ② FaceApproachYaw       접근 자세로 회전 (절대각)     ← 좌표 계산 → 고정각
    ③ ReleaseNav            nav2 목표를 끊어 바퀴를 놓는다
    ④ DockApproach          정밀 도킹(6cm 까지)           ← **`dock_sensor` 가 정한다**
    ⑤ DockNudge             정속 개루프로 후진 (라이다면 0)
    ⑥ DockSettle            멈춰 서서 안정화 후 도킹으로 침

**없앤 것: `GoToParking` · `TurnAround`.** ②가 이제 "주차장 쪽"이 아니라
**접근 자세**(충전 단자가 뒤에 오는 각도)로 돌리므로, 180° 를 따로 돌 필요가 없고
nav2 로 주차장 정점까지 가는 구간도 ④ 정밀 도킹이 대신한다.

⚠️ **④의 실제 구현은 주입된 드라이버가 정한다** — `dock_sensor: aruco` 면
   `/fleet_cmd{action}` 왕복으로 다른 저장소(robot_agent)에 넘기고, `lidar` 면 이
   저장소의 `LidarDockDriver` 가 직접 몬다(`main.py` 가 고른다). leaf 이름을
   센서 중립(`DockApproach`)으로 두는 이유: 라이다가 도는데 화면에 "ArucoApproach"
   가 뜨면 조용한 거짓말이 된다.
   ArUco 경로는 답하는 쪽이 **하나여야 한다** — 둘이 답하면 먼저 온 결과로 다음
   단계가 시작되고, 아무도 안 답하면 timeout 까지 서 있는다. 팔(`LIBI_ARM_VIA_BT`)
   에서 같은 함정을 이미 밟았다. CLAUDE.md 참고.

## ⚠️ ③ ReleaseNav 가 왜 필요한가 (2026-07-30, codex 리뷰에서 나왔다)

`_GoalStep` 은 **x·y 거리만** 보고 SUCCESS 를 낸다(yaw 는 안 본다). 그리고 성공으로 끝난
단계는 `terminate` 에서 `driver.stop()` 을 부르지 않는다 — 그래야 다음 단계가 방금 낸
주행을 자기 stop 으로 죽이지 않는다. 즉 **①이 끝나도 그 nav2 목표는 살아 있다.**

예전에는 그게 문제가 안 됐다. 바로 뒤 `GoToParking` 이 새 nav2 goal 을 내서 **선점**했기
때문이다. 그 단계를 없애면서 선점해 줄 주체가 사라졌다. 특히 이 경로가 위험하다:

    로봇이 입구 좌표에 이미 서 있고 yaw 도 이미 접근 각도 근처다
      → ① x·y 만 보고 즉시 SUCCESS (entrance_yaw 로 가라는 nav2 목표는 살아 있음)
      → ② 이미 허용오차 안이라 **goal 을 한 번도 안 보내고** SUCCESS (선점 없음)
      → ArUco 접근이 시작되는데 nav2 는 아직 옛 목표로 회전을 시도한다

그래서 외부에 바퀴를 넘기기 **직전에 명시적으로 놓는다.** 오늘 배운 것과 모순이 아니다 —
`NavGoalTracker.begin()` 에서 취소를 없앤 건 "계속 가야 하는데 끊었다"라서였고, 여기는
**정말로 멈춰야 하는 자리**다(제어 주체가 다른 프로세스로 넘어간다).
"""
import math
import time

import py_trees
from py_trees.common import Access, Status

from libi_modes import blackboard as bb
from libi_modes.blackboard import Keys
from libi_modes.common.driver_action import DriverAction


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

    def __init__(self, name, driver, target, tolerance, resend_sec, timeout_sec, now_fn,
                 recovery_retry_max=3, recovery_stall_sec=0.0):
        super().__init__(name=name)
        self.driver = driver
        self.target = target                 # (x, y) — 콜러블도 허용
        self.tolerance = float(tolerance)
        self.resend_sec = float(resend_sec)
        self.timeout_sec = float(timeout_sec)
        self._now = now_fn
        self._sent_at = None
        self._started_at = None
        self._recovery_retry_max = max(0, int(recovery_retry_max))
        self._recovery_stall_sec = max(0.0, float(recovery_stall_sec))
        self._retries = 0
        self._best_distance = None
        self._progress_at = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.ROBOT_POSE, access=Access.READ)

    def initialise(self):
        self._sent_at = None
        self._started_at = None
        self._retries = 0
        self._best_distance = None
        self._progress_at = None

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
        distance = (math.hypot(pose["x"] - xy[0], pose["y"] - xy[1])
                    if pose else None)
        if distance is not None and distance <= self.tolerance:
            return Status.SUCCESS

        if self._best_distance is None and distance is not None:
            self._best_distance = distance
            self._progress_at = now
        elif (distance is not None and self._best_distance is not None
              and distance < self._best_distance - 0.01):
            self._best_distance = distance
            self._progress_at = now

        if self._sent_at is None or now - self._sent_at >= self.resend_sec:
            # nav2 주행은 도착 없이 끝날 수 있다(ABORTED, 선점). 다시 몰아 줄 주체가
            # 필요하고, 도착 여부를 아는 건 여기뿐이다.
            self.driver.start()
            self._sent_at = now
            self._best_distance = distance
            self._progress_at = now
        elif self.driver.poll() == "failure":
            if not self._retry(now):
                return Status.FAILURE            # 실행 층이 반복해서 거부했다

        stalled = (self._recovery_stall_sec > 0 and self._progress_at is not None
                   and now - self._progress_at >= self._recovery_stall_sec)
        if stalled or now - self._started_at >= self.timeout_sec:
            if not self._retry(now):
                return Status.FAILURE
        return Status.RUNNING

    def _retry(self, now):
        if self._retries >= self._recovery_retry_max:
            return False
        self._retries += 1
        self.driver.start()
        self._sent_at = now
        self._started_at = now
        self._best_distance = None
        self._progress_at = now
        return True

    def terminate(self, new_status):
        """상위가 이 단계를 끊었으면(예: fault → ERROR) 진행 중인 주행도 끊는다.

        안 끊으면 브랜치는 죽었는데 nav2 는 계속 달린다 — 화면상 ERROR 인 로봇이
        주차장으로 계속 가는 상태가 된다.
        """
        if new_status == Status.INVALID and self._sent_at is not None:
            self.driver.stop()
        self._sent_at = None


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

    def terminate(self, new_status):
        """회전도 마찬가지다 — 끊긴 뒤에도 goal 이 살아 있으면 계속 돈다."""
        if new_status == Status.INVALID and self._sent:
            self.driver.stop()
        self._sent = False


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


class BackCamOn(py_trees.behaviour.Behaviour):
    """도킹 동안 **뒷캠을 선택 상태로 유지한다.** 절대 끝나지 않는다.

    ## 왜 필요한가 — 대기 캠은 1.9Hz 다

    `camera_sender` 는 선택되지 않은 캠을 **8틱에 한 번만** 잡는다
    (`STANDBY_EVERY=8`, 15fps → 0.53초). 복귀 중에는 추종 세션이 없어 `camera_select` 가
    front/none 이므로 뒷캠이 그 상태다. 그 프레임으로 12Hz 시각 서보를 돌리면
    HOMING(0.10m/s)에서 프레임 사이 **5.3cm** 를 간다 — 못 쓴다.

    `guide_watch{camera:back}` 를 내면 `follow_node` 가 `/libi/camera_select` 를 back 으로
    발행하고(그 토픽의 발행자는 follow_node 하나라는 규칙이 있다), 그러면 뒷캠이 매 프레임
    15fps 가 된다.

    ## 왜 절대 안 끝나는가

    이 leaf 는 `ReturnAndWatch`(Parallel, SuccessOnOne)의 자식이다. **SUCCESS 를 내면
    그 순간 Parallel 이 성공으로 끝나 복귀가 통째로 중단된다.** FAILURE 는 정책과
    무관하게 Parallel 을 죽인다. 그래서 어느 쪽도 내지 않는다 — 세션의 수명은
    Parallel 이 끝날 때 `terminate(INVALID)` 가 닫는다. 그게 정확히 원하는 수명이다:
    도킹이 성공하든 fault 로 빠지든, 나갈 때 뒷캠 선택이 풀린다.
    """

    def __init__(self, driver, name: str = "BackCamOn"):
        super().__init__(name=name)
        self.driver = driver
        self._sent = False

    def initialise(self):
        self._sent = False

    def update(self):
        if not self._sent:
            self.driver.start()
            self._sent = True
        return Status.RUNNING

    def terminate(self, new_status):
        if self._sent:
            self.driver.stop()          # follow_stop — 세션만 닫고 주행은 안 끊는다
        self._sent = False


class DockSettle(py_trees.behaviour.Behaviour):
    """마지막 미세 이동이 끝난 뒤 이만큼 서 있다가 **도킹으로 친다.**

    ## [2026-07-30] `is_docked` 를 기다리지 않는다 (사용자 결정)

    예전 `AlignDock` 은 `is_docked` 를 기다렸다. 그 신호를 내는 건 `dock_confirm.py` 이고
    판정 근거는 **주차장 정점 반경 0.12m** 다 — 즉 위치 판정이다. 앞 단계가 ArUco 로
    6cm 까지 붙고 다시 3cm 를 밀고 난 뒤에는 그 반경 안에 이미 들어와 있어서, 기다려 봐야
    아무것도 검증하지 못한다. 검증하는 척하는 게이트는 없느니만 못하다.

    그래서 **시간으로 넘긴다.** 이 대기가 하는 일은 하나다: 개루프 후진이 끝난 직후의
    관성·바퀴 되튐이 가라앉을 시간을 주고 나서 CHARGING 을 선언하는 것.

    ⚠️ 진짜 접촉 확인(전류·전압 상승 등)이 생기면 **여기가 그 자리다.** 그때는 시간이
       아니라 그 신호를 기다리게 바꾼다.
    """

    def __init__(self, settle_sec: float, now_fn, name: str = "DockSettle"):
        super().__init__(name=name)
        self.settle_sec = float(settle_sec)
        self._now = now_fn
        self._started_at = None

    def setup(self, **kwargs):
        self.blackboard = self.attach_blackboard_client(name=self.name)
        self.blackboard.register_key(key=Keys.UNDOCK_DONE, access=Access.WRITE)
        self.blackboard.register_key(key=Keys.DOCK_DECLARED, access=Access.WRITE)

    def initialise(self):
        self._started_at = None

    def update(self):
        now = self._now()
        if self._started_at is None:
            self._started_at = now
        if now - self._started_at >= self.settle_sec:
            # 도킹이 **여기서** 끝난다. 그래서 탈출 래치도 여기서 푼다 — 나갈 때 다시
            # 밀어야 한다(`common/undock.py`). 주행 브랜치의 게이트는 인스턴스가 셋이라
            # 그쪽에서 전이를 보고 풀면 서로 다른 인스턴스가 남의 래치를 지운다.
            self.blackboard.set(Keys.UNDOCK_DONE, False)
            # [2026-07-31] **도킹을 여기서 선언한다**(사용자 결정). 개루프 후진이 끝난
            # 이 자리가 "붙었다"를 아는 유일한 지점이다 — 위치로 추정하던
            # `dock_confirm.py` 는 `pi.sh` 에서 빠져 아무도 안 돌고 있다.
            # `state_io` 가 이 값을 `/is_docked` 로 내보낸다(`Keys.DOCK_DECLARED` 주석).
            self.blackboard.set(Keys.DOCK_DECLARED, True)
            return Status.SUCCESS
        return Status.RUNNING


def create_return_steps(*, entrance_driver, rotate_driver, nav_release_driver,
                        dock_driver, nudge_driver,
                        entrance_xy, approach_yaw=None, dock_xy=None,
                        tolerance, resend_sec, timeout_sec,
                        yaw_tolerance_rad, retry_max, settle_sec=1.0,
                        now_fn=time.monotonic, recovery_retry_max=3,
                        recovery_stall_sec=0.0):
    """6단계 시퀀스를 만든다. 각 단계는 실패 흡수 데코레이터로 감싼다."""
    def _approach_yaw(pose):
        """**지금 보고 있는 방향에서 180° 돌린 각도.** 사용자 지정(2026-07-30).

        ①이 끝나면 로봇은 충전소를 바라보고 서 있다(정점 goal yaw 가 그쪽이다).
        거기서 반 바퀴 돌면 **등이 충전소를 향하고**, 그래야 뒷캠이 마커를 본다.

        ## 왜 절대각도, 충전소 좌표도 아닌 상대 180° 인가

        둘 다 시도했고 둘 다 틀렸다:

          · **고정 절대각**(정점 yaw + 180°) — ①이 x·y 만 보고 성공하므로 실제 헤딩이
            그 정점 yaw 라는 보장이 없다. 상황마다 다른 곳을 봤다.
          · **충전소 좌표에서 atan2** — 로봇이 충전소와 거의 일직선상에 서면 방위각이
            작은 위치 오차에도 크게 튄다(38cm 앞에서 y 가 2cm 어긋나면 3° 다. 더 가까워지면
            더 심하다). 그리고 사용자가 원한 동작이 아니다.

        상대 180° 는 ①이 만들어 준 자세를 그대로 이어받는다 — 그 자세가 곧 충전소를
        바라보는 자세이므로, 반대편이 정확히 등을 지는 자세다.

        ⚠️ 목표는 `_YawStep` 이 **한 번만** 정한다(그 leaf 주석). 매 tick 다시 계산하면
           돌면서 목표도 따라 움직여 영원히 안 닿는다.
        """
        # 현장 보정 절대각이 있으면 현재 heading 으로 다시 계산하지 않는다.
        # `approach_yaw` 를 받으면서도 아래 상대 180°만 쓰면 설정 파일을 바꿔도
        # 실제 로봇은 전혀 바뀌지 않는 조용한 실패가 된다.
        if approach_yaw is not None:
            return wrap_angle(float(approach_yaw))
        # 위치·자세를 모르면 회전을 시작하지 않는다 — 실행 층이 좌표 없이 goal 을 못 만든다.
        if pose is None or pose.get("yaw") is None:
            return None
        return wrap_angle(pose["yaw"] + math.pi)

    steps = [
        _GoalStep("GoToParkingEntrance", entrance_driver, entrance_xy,
                  tolerance, resend_sec, timeout_sec, now_fn,
                  recovery_retry_max=recovery_retry_max,
                  recovery_stall_sec=recovery_stall_sec),
        _YawStep("FaceApproachYaw", rotate_driver, _approach_yaw,
                 yaw_tolerance_rad, timeout_sec, now_fn),
        # ③ 바퀴를 외부에 넘기기 전에 nav2 목표를 명시적으로 끊는다 (위 주석 참고).
        #    취소할 목표가 없으면 아무 일도 안 일어난다 — 멈추는 것은 중복돼도 안전하다.
        DriverAction(nav_release_driver, "ReleaseNav"),
        # ④ 정밀 도킹. **어떤 센서로 하는지는 주입된 드라이버가 정한다** —
        #    `dock_sensor: aruco` 면 다른 저장소(robot_agent)의 뒷캠 ArUco 가,
        #    `lidar` 면 이 저장소의 `LidarDockDriver` 가 수행한다. leaf 이름을
        #    센서 중립으로 두는 이유: 라이다가 도는데 화면에 "ArucoApproach" 가
        #    뜨면 조용한 거짓말이 된다.
        DriverAction(dock_driver, "DockApproach"),
        # ⑤ 개루프. 드라이버가 자기 타이머로 정속을 밀고 시간이 되면 스스로 멈춘다.
        DriverAction(nudge_driver, "DockNudge"),
        DockSettle(settle_sec, now_fn),
    ]
    return [AbsorbFailure(s, retry_max) for s in steps]
