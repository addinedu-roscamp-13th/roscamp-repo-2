"""회전과 직진을 실제 twist 로 내보낸다. 그리고 "온 만큼 되돌아가기" 를 보관한다.

## ⚠️ odom 기반이다 — 시간 기반이 아니다 (revision.md R1, codex P0)

처음 계획은 시간 기반(`거리/속도 = 지속시간`)이었다. codex 가 P0 로 반박했다: 배터리·
바닥 재질·부하로 실제 속도가 달라지면 그대로 거리 오차가 되고, 그건 설계가 요구한
"개루프 **odom**" 이 아니다. 그래서 `MoveExecutor` 는 시간을 전혀 재지 않는다.
`pose_fn()` 이 매 tick `(x, y, yaw)` 를 주고, 그 값의 **변화량**을 적분해 "얼마나
갔나/돌았나" 를 스스로 잰다. 속도가 중간에 바뀌어도(배터리가 처지든, 누가 손으로
막았다 놓든) 이동 거리는 그대로다 — `tests/shelf/test_move_executor.py` 의
`test_speed_change_does_not_change_the_distance` 가 바로 이걸 검증한다.

## ⚠️ 이 실행기는 `cmd_vel_dock` 으로 낸다

`/cmd_vel` 로 직접 내면 nav2 와 두 주체가 같은 토픽을 다툰다(중재자 없음).
`twist_mux` 우선순위 120 인 도킹 채널로 낸다 — `marker_dock` 선례.

## ⚠️ 취소는 실패다

`run()` 이 취소를 성공으로 답하면 상위 BT 가 **취소된 도킹을 성공으로 읽는다**
(codex P0). 완주했을 때만 `(True, "")` 다 — 취소되면 `(False, "canceled")`.

## 사람 차단 뒤 복귀 좌표는 FMS 가 준다 (R2)

나갈 때 만든 이동 목록(`record_outbound`)은 **우리가 만든 이동**(서가 도킹)에만
있다. nav2 가 몬 구간(사람 차단으로 멈춘 지점)은 우리가 만든 `Move` 기록이 없다.
그래서 `backup` 명령은 FMS 가 준 좌표(`args["x"]`/`args["y"]` — 로봇이 마지막으로
지나온 정점)로 그 자리에서 `approach_moves` 를 새로 계산한다. 좌표도 없고
`pop_outbound()` 도 비었으면 **실패로 답한다** — 조용히 안 움직이면 BT 가 성공으로
읽는다.
"""
from __future__ import annotations

import json
import math
import threading
import time

from app.shelf.geometry import (DRIVE, TURN, Move, approach_moves, retreat_moves,
                                return_moves, wrap_pi)

#: 나갈 때 만든 이동(서가 도킹)과 그때의 heading/final_yaw. 복귀 때 이걸 뒤집어 쓴다.
#
# ⚠️ `Move` 목록만으로는 복귀 회전량을 못 구한다(2026-08-03 리뷰, 실측 왕복오차
#    0.2448m) — `return_moves` 가 heading/final_yaw 를 요구하므로 같이 들고 있는다.
_outbound_state: dict = {}
_outbound_lock = threading.Lock()


def record_outbound(moves, heading: float, final_yaw: float) -> None:
    with _outbound_lock:
        _outbound_state["moves"] = list(moves)
        _outbound_state["heading"] = float(heading)
        _outbound_state["final_yaw"] = float(final_yaw)


def record_return_targets(targets) -> None:
    """FMS `backup` 명령 때 밟을 AMCL 체크포인트를 역순으로 보관한다."""
    clean = [(float(x), float(y)) for x, y in targets]
    with _outbound_lock:
        _outbound_state.clear()
        _outbound_state["return_targets"] = clean


def pending_return_targets() -> list[tuple[float, float]]:
    """실행 전 체크포인트를 복사한다. preflight 실패는 재시도할 수 있게 보존한다."""
    with _outbound_lock:
        return list(_outbound_state.get("return_targets") or [])


def clear_return_targets() -> None:
    """성공적으로 FMS backup을 끝낸 뒤에만 체크포인트를 소모한다."""
    with _outbound_lock:
        _outbound_state.pop("return_targets", None)


def pop_outbound() -> list:
    """복귀 이동. 나간 기록이 없으면 빈 목록 — 그때는 아무 데도 안 간다."""
    with _outbound_lock:
        state = dict(_outbound_state)
        _outbound_state.clear()
    if "moves" not in state:
        return []
    return return_moves(state["moves"], state["heading"], state["final_yaw"])


#: 한 번의 `_run_move` 가 tick 을 이만큼 넘게 쓰면 포기한다.
#
# ponytail: 실시간(초)이 아니라 고정 tick 수 — pose_fn 이 실제로 얼마나 자주
# 불리는지(제어 주기)와 무관하게 "확실히 하드웨어가 안 따라온다" 를 잡는 안전판이다.
# 실기에서 이 한도에 걸리는 사례가 나오면 그때 실시간 데드라인으로 승급한다.
MAX_TICKS_PER_MOVE = 4000


class MoveExecutor:
    """`Move` 목록을 twist 로 내보내고 **odom 으로 도달을 판정한다.**

    ROS 를 모른다 — 발행 함수(`publish_twist(lin, ang)`)와 pose 소스(`pose_fn() ->
    (x, y, yaw)`) 를 주입받는다. `pose_fn` 이 매 tick 최신 값을 주는 한, 그 값을
    어디서 구했는지(실제 `/odom`, 시험의 가짜 적분기)는 상관하지 않는다.
    """

    def __init__(self, publish_twist, pose_fn, turn_speed: float = 0.4,
                 drive_speed: float = 0.08, turn_tol: float = 0.02,
                 drive_tol: float = 0.005, map_yaw_fn=None):
        self.publish_twist = publish_twist
        self.pose_fn = pose_fn
        #: `map_yaw_fn() -> float|None` — map 프레임 현재 yaw. 주면 `Move.abs_yaw` 가
        #: 실린 회전을 **매 tick 절대 오차로 다시 재서** 돈다(odom 적분 누적을 안 쓴다).
        #: 안 주거나 그 tick 에 `None` 을 돌려주면 예전 상대 회전으로 자동 폴백한다.
        self.map_yaw_fn = map_yaw_fn
        self.turn_speed = abs(float(turn_speed))
        self.drive_speed = abs(float(drive_speed))
        self.turn_tol = abs(float(turn_tol))
        self.drive_tol = abs(float(drive_tol))

    @staticmethod
    def _reached(target: float, traveled: float, tol: float) -> bool:
        """부호 있는 목표량에 (허용오차 안으로) 닿았거나 지나쳤나."""
        if target >= 0.0:
            return traveled >= target - tol
        return traveled <= target + tol

    def _run_move(self, move: Move, cancel):
        """`Move` 하나를 odom 이 목표에 닿을 때까지 tick 마다 `yield` 하는 제너레이터.

        `"ok"` / `"canceled"` / `"timeout"` 을 반환값(`return`, `yield from` 이 받는다)
        으로 준다.
        """
        target = float(move.value)
        tol = self.turn_tol if move.kind == TURN else self.drive_tol
        speed = self.turn_speed if move.kind == TURN else self.drive_speed
        if speed <= 0.0:
            return "ok"  # 속도 0 이면 갈 방법이 없다 — 설정 문제이지 여기서 고칠 일이 아니다

        # ── map 절대 yaw 로 닫는 회전 ──────────────────────────────────────────
        # 목표는 언제나 map 절대 자세다. odom 적분 상대 회전으로 실행하면 그 사이
        # 오차가 남고 회전이 이어질수록 누적된다(복귀는 회전 6번). 매 tick map yaw 를
        # 다시 읽어 **남은 오차만큼만** 돌면 중간에 밀려도 그 tick 에 회수된다 —
        # 도킹의 `turn_to_map_yaw` 와 같은 판단이다(`Move.abs_yaw` 주석).
        if move.kind == TURN and move.abs_yaw is not None and self.map_yaw_fn is not None:
            ticks = 0
            while True:
                now_yaw = self.map_yaw_fn()
                if now_yaw is None:
                    break               # map pose 를 못 얻는다 — 아래 상대 회전으로 폴백
                err = wrap_pi(float(move.abs_yaw) - float(now_yaw))
                if abs(err) <= tol:
                    return "ok"
                if cancel is not None and cancel():
                    return "canceled"
                # P 제어로 줄이지 않는다 — 느려지면 정지마찰을 못 이겨 아예 안 돈다
                # (`shelf_dock.py` MAP_YAW_ANG 주석의 실측).
                self.publish_twist(0.0, math.copysign(self.turn_speed, err))
                yield
                ticks += 1
                if ticks > MAX_TICKS_PER_MOVE:
                    return "timeout"

        sign = 1.0 if target >= 0.0 else -1.0
        lin = self.drive_speed * sign if move.kind == DRIVE else 0.0
        ang = self.turn_speed * sign if move.kind == TURN else 0.0

        start = self.pose_fn()
        prev_yaw = start[2]
        traveled = 0.0
        ticks = 0
        while not self._reached(target, traveled, tol):
            if cancel is not None and cancel():
                return "canceled"
            self.publish_twist(lin, ang)
            yield
            ticks += 1
            if ticks > MAX_TICKS_PER_MOVE:
                return "timeout"
            pose = self.pose_fn()
            if move.kind == TURN:
                traveled += wrap_pi(pose[2] - prev_yaw)
                prev_yaw = pose[2]
            else:
                dx, dy = pose[0] - start[0], pose[1] - start[1]
                traveled = dx * math.cos(start[2]) + dy * math.sin(start[2])
        return "ok"

    def run_steps(self, moves, cancel=None):
        """`Move` 목록을 한 tick 씩 진행하는 제너레이터.

        매 tick 발행 뒤 아무 값도 없이 `yield` 한다(호출자가 `pose_fn` 의 부작용 —
        예: ROS `spin_once` — 을 tick 사이에 끼워 넣을 수 있게). **마지막에는 반드시
        정지 twist(0, 0) 를 낸 뒤** 상태 튜플을 한 번 더 `yield` 한다: 완주하면
        `(True, "")`, 취소/시간초과면 `(False, 사유)`.
        """
        for move in moves:
            if move.value == 0.0:
                continue
            outcome = yield from self._run_move(move, cancel)
            if outcome != "ok":
                self.publish_twist(0.0, 0.0)
                yield (False, outcome)
                return
        self.publish_twist(0.0, 0.0)
        yield (True, "")

    def run(self, moves, cancel=None) -> tuple[bool, str]:
        """끝까지 돌린다. 호출한 스레드를 막는다 — fleet_link 워커가 그 구조다.

        완주하면 `(True, "")`. **취소되면 `(False, "canceled")`** — 취소는 실패다
        (원안의 `return True, ""` 는 상위 BT 가 취소된 도킹을 성공으로 읽게 만든다).

        ⚠️ `pose_fn`/`publish_twist` 가 tick 도중 예외를 내면(ROS 통신 오류 등)
        마지막으로 낸 비영 twist 가 그대로 남는다 — 여기서 최선을 다해 정지를 한 번
        더 내고 나서 예외를 그대로 올린다(하드웨어 정지는 보장, 오류 처리는 호출자 몫).
        """
        result: tuple[bool, str] = (True, "")
        try:
            for item in self.run_steps(moves, cancel=cancel):
                if isinstance(item, tuple):
                    result = item
        except Exception:                                        # noqa: BLE001
            try:
                self.publish_twist(0.0, 0.0)
            except Exception:                                    # noqa: BLE001
                pass
            raise
        return result


def run_backup(args: dict, *, _read_pose_fn=None, _drive_fn=None) -> tuple[bool, int, dict, str]:
    """`backup` 명령의 실행 진입점. `(ok, http_status, data, msg)` — fleet_link 계약.

    `args` 에 `x`/`y` 가 있으면 그 좌표로(R2, 사람 차단 뒤 복귀) 새로 계산한다.
    없으면 `pop_outbound()`(서가 도킹 복귀)를 쓴다. 둘 다 없으면 실패로 답한다 —
    조용히 안 움직이면 BT 가 성공으로 읽는다.

    ⚠️ `_running`/세대 번호를 **preflight(좌표형이면 `/amcl_pose` 읽기) 전에** 잡는다
    — `marker_dock` 과 같은 원칙이다. 늦게 잡으면(예전엔 `_read_current_pose()` 를
    먼저 부르고 그 다음에야 세대를 잡았다) preflight 도중 들어온 취소가 잡을 세대가
    없어 사라지고, 상위가 이미 포기한 뒤에 실제 주행이 시작된다
    (2026-08-04 리뷰 P0). `_read_pose_fn`/`_drive_fn` 은 시험 주입용(기본은 실제
    ROS 구현 `_read_current_pose`/`_run`).
    """
    args = args or {}
    read_pose = _read_pose_fn or _read_current_pose
    drive = _drive_fn or _run

    global _running, _gen
    with _lock:
        if _running:
            return False, 409, {"backed": False}, "후퇴가 이미 진행 중이다"
        _gen += 1
        my_gen = _gen
        _running = True
    try:
        return _plan_and_drive(args, my_gen, read_pose, drive)
    finally:
        with _lock:
            _running = False


def _plan_and_drive(args: dict, my_gen: int, read_pose, drive) -> tuple[bool, int, dict, str]:
    """세대를 이미 잡은 뒤(위 `run_backup`) 호출된다 — preflight 중 취소가 들어와도
    `_cancelled(my_gen)` 이 이걸 본다(취소 자체는 사라지지 않는다)."""
    x, y = args.get("x"), args.get("y")
    targets = pending_return_targets() if x is None and y is None else []

    if targets:
        pose = read_pose()
        if pose is None:
            return False, 503, {"backed": False}, "현재 pose(/amcl_pose) 를 못 얻었다"
        if _cancelled(my_gen):
            return False, 499, {"backed": False}, "취소됨(preflight)"
        moves = []
        rx, ry, ryaw = pose
        for tx, ty in targets:
            # 돌지 않고 **후진**으로 뺀다 — 서가 앞 3cm 에서 제자리로 돌면 꽁무늬가
            # 서가를 쓴다(`geometry.retreat_moves` 머리말, 2026-08-07 실기).
            moves.extend(retreat_moves(rx, ry, ryaw, tx, ty))
            back_yaw = wrap_pi(math.atan2(ty - ry, tx - rx) + math.pi)
            rx, ry, ryaw = tx, ty, back_yaw
    elif x is not None and y is not None:
        pose = read_pose()
        if pose is None:
            return False, 503, {"backed": False}, "현재 pose(/amcl_pose) 를 못 얻었다"
        if _cancelled(my_gen):
            return False, 499, {"backed": False}, "취소됨(preflight)"
        rx, ry, ryaw = pose
        heading = math.atan2(float(y) - ry, float(x) - rx)
        moves = approach_moves(rx, ry, ryaw, float(x), float(y),
                               clearance=0.0, final_yaw=heading)
    else:
        moves = pop_outbound()
        if not moves:
            return False, 400, {"backed": False}, \
                "복귀할 좌표(args.x/y)도 기록된 왕복 이동(record_outbound)도 없다"
        if _cancelled(my_gen):
            return False, 499, {"backed": False}, "취소됨(preflight)"

    result = drive(moves, my_gen)
    if targets and result[0]:
        clear_return_targets()
    return result


# ── ROS 실행 (marker_dock.py 구조를 따른다: 전용 노드 + spin_once + 취소 세대) ──

_lock = threading.Lock()
_running = False
_gen = 0
_cancel_gen = -1


def request_cancel() -> bool:
    """진행 중인 후퇴를 끊는다. `fleet_link` 의 **구독 콜백**에서 부른다."""
    global _cancel_gen
    with _lock:
        if not _running:
            return False
        _cancel_gen = _gen
        return True


def _cancelled(my_gen: int) -> bool:
    with _lock:
        return _cancel_gen == my_gen


def is_running() -> bool:
    with _lock:
        return _running


def _read_current_pose() -> tuple[float, float, float] | None:
    """현재 map 자세 `(x, y, yaw)`. 못 얻으면 `None`.

    ⚠️ **상시 구독을 먼저 본다.** 예전엔 곧바로 `_probe_amcl_pose()` 로 갔는데,
    그러면 **반드시 실패한다** — 이 함수를 부르는 시점(서가 도킹 뒤 복귀)은 로봇이
    **정지해 있는** 순간이고, AMCL 은 `update_min_d`(2cm) 이상 움직여야만 발행한다
    (`ros_bridge.py:328` 머리말). 새 구독이 기다려도 올 게 없다.

    실측(2026-08-05 관제 UI): 배달 5다리 중 3번(`backup`)이 **매번** "현재
    pose(/amcl_pose) 를 못 얻었다" 로 죽어서 목적지 주행·놓기·순회까지 전부 막혔다.
    flaky 가 아니라 도킹 뒤엔 정의상 정지해 있으니 결정적이었다.

    `ros_bridge.get_current_pose()` 는 상시 떠 있는 구독이 채우는 **마지막 자세**라
    정지 중에도 그대로 남아 있다. 같은 성격의 preflight 인 `park_dock.py:129` 도
    이걸 쓴다 — 여기만 예외였다.

    대가: AMCL 갱신 사이 최대 2cm 낡을 수 있다(`ros_bridge.py:338`). 복귀 체크포인트
    (`record_return_targets`)도 같은 출처로 기록된 값이라 기준이 같고, 무엇보다
    **못 얻어서 안 움직이는 것보다 낫다.**
    """
    from app.core import ros_bridge

    pose = ros_bridge.get_current_pose()
    if pose is not None:
        return pose
    return _probe_amcl_pose()


def _probe_amcl_pose() -> tuple[float, float, float] | None:
    """`/amcl_pose` 를 잠깐 구독해 최신 `(x, y, yaw)` 하나만 받아온다.

    상시 구독이 아직 아무것도 못 받았을 때(부팅 직후 등)의 예비 경로다.
    """
    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    from app.core import fleet_link
    from app.core.ros_bridge import quat_to_yaw

    ctx = fleet_link.get_context()
    if ctx is None:
        return None
    node = rclpy.create_node("backup_pose_probe", context=ctx)
    executor = SingleThreadedExecutor(context=ctx)
    executor.add_node(node)
    got: dict = {}

    def _on_pose(msg) -> None:
        p = msg.pose.pose
        got["pose"] = (p.position.x, p.position.y, quat_to_yaw(p.orientation))

    # ⚠️ **TRANSIENT_LOCAL 이어야 한다.** 기본 VOLATILE 로 두면 구독한 뒤에 새로
    # 발행되는 것만 받는데, 여기 오는 시점의 로봇은 정지해 있어 AMCL 이 아무것도
    # 안 낸다 — 5초를 기다려도 영영 못 받는다(위 `_read_current_pose` 머리말의
    # 2026-08-05 실측). AMCL 은 마지막 자세를 래치해 두므로 TRANSIENT_LOCAL 로
    # 붙으면 **구독 즉시** 그 값이 온다. `shelf_dock.py:588` 이 같은 이유로 같은
    # 선택을 해뒀는데 여기만 안 따라왔다.
    latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", _on_pose, latched)
    try:
        deadline = time.monotonic() + 5.0
        while "pose" not in got and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
        return got.get("pose")
    finally:
        executor.remove_node(node)
        node.destroy_node()


def _run(moves: list, my_gen: int) -> tuple[bool, int, dict, str]:
    """`moves` 를 `cmd_vel_dock` 으로 실제로 낸다. `(ok, status, data, msg)`.

    ⚠️ `_running`/세대는 이미 `run_backup` 이 잡아 뒀다 — 여기서 다시 잡지 않는다
    (예전엔 이 함수를 감싸는 `_run_moves_blocking` 이 preflight *뒤에* 잡아서
    P0 취소유실 결함이 있었다. `_plan_and_drive` 참고)."""
    import rclpy
    from geometry_msgs.msg import Twist
    from std_msgs.msg import String
    from nav_msgs.msg import Odometry
    from rclpy.executors import SingleThreadedExecutor

    from app.core import fleet_link
    from app.core.ros_bridge import quat_to_yaw

    ctx = fleet_link.get_context()
    if ctx is None:
        return False, 503, {"backed": False}, "fleet_link ROS context 가 아직 없다"

    node = rclpy.create_node("backup_runner", context=ctx)
    executor = SingleThreadedExecutor(context=ctx)
    executor.add_node(node)
    pub = node.create_publisher(Twist, "cmd_vel_dock", 10)
    status_pub = node.create_publisher(String, "shelf_dock_status", 20)
    odom_state: dict = {}

    def _on_odom(msg) -> None:
        p = msg.pose.pose.position
        odom_state["pose"] = (p.x, p.y, quat_to_yaw(msg.pose.pose.orientation))

    node.create_subscription(Odometry, "/odom", _on_odom, 10)
    log = node.get_logger()

    def report(phase: str, **fields) -> None:
        body = {"event": "shelf_dock", "shelf": "FMS", "phase": phase, **fields}
        status_pub.publish(String(data=json.dumps(body, ensure_ascii=False, separators=(",", ":"))))

    def publish(lin: float, ang: float) -> None:
        t = Twist()
        t.linear.x = float(lin)
        t.angular.z = float(ang)
        pub.publish(t)

    def pose_fn():
        # 매 tick 여기서 spin 한다 — MoveExecutor 가 tick 마다 이 함수를 부르므로,
        # 그 부작용으로 실행 주기(spin + pacing)를 겸한다(marker_dock 의 spin()/sleep 과
        # 같은 자리, 순서만 tick 안으로 접었다).
        time.sleep(0.02)
        for _ in range(4):
            executor.spin_once(timeout_sec=0.0)
        return odom_state.get("pose", (0.0, 0.0, 0.0))

    def cancel_fn() -> bool:
        return _cancelled(my_gen)

    try:
        deadline = time.monotonic() + 5.0
        while "pose" not in odom_state and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
        if "pose" not in odom_state:
            return False, 503, {"backed": False}, "/odom 이 안 들어온다"

        def map_yaw_fn():
            """복귀 회전을 **map 절대 yaw** 로 닫기 위한 현재 방위. 못 얻으면 `None`.

            `_read_current_pose()` 는 상시 구독(`ros_bridge`)이 들고 있는 마지막
            자세라 정지 중에도 값이 있다(그 함수 머리말 참고). 여기서 `None` 이
            나오면 실행부가 예전 odom 상대 회전으로 조용히 폴백한다.
            """
            p = _read_current_pose()
            return None if p is None else p[2]

        executor_ = MoveExecutor(publish_twist=publish, pose_fn=pose_fn,
                                 map_yaw_fn=map_yaw_fn)
        report("backup_started", planned_drive_m=round(sum(abs(m.value) for m in moves if m.kind == DRIVE), 3))
        ok, why = executor_.run(moves, cancel=cancel_fn)
        log.info(f"[후퇴] moves={[(m.kind, m.value) for m in moves]} ok={ok} why={why}")
        if not ok:
            report("backup_failed", message=why)
            return False, 502 if why != "canceled" else 499, {"backed": False}, why
        report("backup_completed")
        return True, 200, {"backed": True}, ""
    finally:
        for _ in range(5):
            publish(0.0, 0.0)
            time.sleep(0.02)
        executor.remove_node(node)
        node.destroy_node()
