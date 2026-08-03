"""서가 정밀 도킹 — 지도기반 LOS 교차점 추정.

카메라가 서가 표식의 **방향**을 주고, 지도가 그 방향의 **거리**를 준다. 둘을 합치면
서가 표면의 맵 좌표가 나오고, 거기서 이동량을 계산해 개루프로 간다.

    ① navgraph 가 정한 서가 방향으로 회전
    ② 오른쪽으로 조금 더 회전 (표식이 화면에 들어오게)
    ③ 앞캠 프레임에서 초록 표식 중점 픽셀 u
    ④ bearing θ = atan2(u - cx, fx)          ← 320 기준 K
    ⑤ /map 을 (yaw - θ) 방향으로 레이캐스트 → 첫 점유 셀 = 특징점
    ⑥ 삼각형 분해 → 회전·직진·회전
    ⑦ 서가 쪽으로 1cm 못 미쳐 멈춘다
    ⑧ 정점·간선 잠금 해제
    ⑨ 실행

## ⚠️ bearing 의 부호

`bearing_rad` 는 화면 **오른쪽**을 양수로 준다. 로봇 기준으로도 오른쪽이고, 오른쪽은
yaw 가 **줄어드는** 방향이므로 레이 방향은 `yaw - θ` 다. 부호를 뒤집으면 거리는
그럴듯한데 좌우만 반대로 흐른다 — `app/marker/calib.py` 가 경고하는 바로 그 증상이다.

## 왜 계산과 실행이 나뉘어 있나

계산은 로봇 없이 시험할 수 있고 실행은 아니다. 이 파일의 `plan_dock` 은 순수 함수라
합성 프레임·합성 격자로 전부 시험된다.
"""
from __future__ import annotations

import json
import math
import threading
import time

from app.shelf.bearing import bearing_rad, scale_k
from app.shelf.geometry import Move, axis_aligned_moves, wrap_pi
from app.shelf.geometry import TURN
from app.shelf.green_marker import centroid_u
from app.shelf.raycast import first_occupied

#: 서가 정점에서 서가를 마주 보는 방향(rad, map 프레임).
#: 화면(관제 UI, 90° CCW 회전) 기준으로 문학서가는 왼쪽, 과학-인문학서가는 오른쪽이다.
#: ⚠️ `arte2.navgraph.yaml` 의 같은 정점 `yaw` 와 **반드시 같은 값**이어야 한다.
SHELF_YAW = {
    "문학서가": 1.5708,
    "과학-인문학서가": -1.5708,
}

#: 서가를 마주 본 뒤 추가로 도는 각. 양수 = 왼쪽. 표식을 화각 안에 넣기 위한 값이다.
EXTRA_TURN_RAD = 0.3491

#: 도킹을 마쳤을 때의 자세. 두 서가가 같다 — 팔이 같은 조건에서 일하게.
FINAL_YAW_RAD = 3.1416

#: 서가에 닿지 않도록 못 미쳐 멈추는 거리(m).
CLEARANCE_M = 0.02

#: 레이캐스트 최대 사거리(m). 서가는 20cm 안쪽이라 넉넉하다.
MAX_RANGE_M = 1.0

#: 현장 비교용: 카메라 내부보정 K 없이 영상 정중앙을 기준으로 한다.
#: 중앙정렬 뒤 광선은 현재 AMCL yaw 그대로 쏜다.
USE_CAMERA_CALIBRATION = False

#: 초록 테이프 중점 오차의 PID 비주얼 서보 설정.
MARKER_CENTER_TOL_PX = 5.0
MARKER_CENTER_STABLE_FRAMES = 30
#: HSV 중점의 조명·마스크 노이즈를 줄이는 EMA 저역통과필터 계수.
MARKER_CENTER_LPF_ALPHA = 0.35
MARKER_SERVO_TIMEOUT_SEC = 10.0
MARKER_SERVO_HZ = 15.0
MARKER_SERVO_KP = 0.45
MARKER_SERVO_KI = 0.02
MARKER_SERVO_KD = 0.08
MARKER_SERVO_MAX_ANG = 0.12

#: 지도 좌표 폐루프(옆축) 설정. AMCL 투영 오차가 이 값 안에 연속으로 들어와야
#: 다음 단계로 간다. 명령은 cmd_vel_dock 으로만 나가므로 Nav2와 충돌하지 않는다.
MAP_AXIS_TOL_M = 0.01
MAP_AXIS_STABLE_TICKS = 5
MAP_AXIS_TIMEOUT_SEC = 15.0
MAP_AXIS_KP = 0.8
MAP_AXIS_MAX_LINEAR_MPS = 0.06
MAP_AXIS_HEADING_KP = 1.2
MAP_AXIS_MAX_ANG = 0.20

#: 마지막 서가 법선축 접근은 카메라·PGM을 매 tick 재관측한다. PGM 격자 해상도보다
#: 지나치게 작은 종료 오차는 의미가 없으므로 clearance 뒤 5 mm 창만 허용한다.
FINAL_APPROACH_TOL_M = 0.005
FINAL_APPROACH_STABLE_TICKS = 3
FINAL_APPROACH_TIMEOUT_SEC = 20.0
FINAL_APPROACH_KP = 0.8
FINAL_APPROACH_MAX_LINEAR_MPS = 0.05
SENSOR_STATE_STALE_SEC = 0.75
#: GUI 로그에 최종 PGM 거리를 갱신하는 최대 주기. 제어 주기(15Hz)를 그대로
#: 기록하면 관리자 로그가 넘치므로, 관측 자체는 계속 쓰되 화면 보고만 제한한다.
DOCK_STATUS_UPDATE_SEC = 0.5

#: 현장 확인용: PID 중앙 정렬 뒤 전진하지 않고 도킹을 종료한다.
#: 테이프 정렬을 확인한 뒤 False로 돌려 전체 도킹을 재개한다.
VISUAL_SERVO_ONLY = False


def visual_servo_angular_z(error: float, integral: float, derivative: float) -> float:
    """정규화한 테이프 중점 오차(-1..1)의 PID 각속도 명령.

    화면 오른쪽 오차는 로봇을 오른쪽(음의 yaw)으로 돌려야 하므로 음수 부호를
    붙인다. 적분·미분항은 프레임 간 실제 시간으로 계산하는 호출자가 준다.
    """
    command = -(MARKER_SERVO_KP * float(error)
                + MARKER_SERVO_KI * float(integral)
                + MARKER_SERVO_KD * float(derivative))
    return max(-MARKER_SERVO_MAX_ANG, min(MARKER_SERVO_MAX_ANG, command))


def shelf_axes(shelf: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """`(법선축, 옆축)` 단위벡터. 옆축은 서가 표면과 평행한 왼쪽 방향이다."""
    yaw = SHELF_YAW[shelf]
    normal = (math.cos(yaw), math.sin(yaw))
    lateral = (-math.sin(yaw), math.cos(yaw))
    return normal, lateral


def axis_projection(x: float, y: float, axis: tuple[float, float]) -> float:
    """맵 좌표를 `axis` 위의 스칼라 좌표로 투영한다."""
    return float(x) * axis[0] + float(y) * axis[1]


def bounded_pid_linear(error_m: float, kp: float, max_speed: float) -> float:
    """거리 오차 P항을 안전 속도 범위로 제한한다.

    목표를 지나친 경우에도 부호를 유지해 천천히 되돌린다. 호출자는 항상 최신
    AMCL/PGM 관측을 다시 읽으므로, 이 함수는 상태를 저장하지 않는 것이 안전하다.
    """
    raw = float(kp) * float(error_m)
    limit = abs(float(max_speed))
    return max(-limit, min(limit, raw))


def dock_status_payload(shelf: str, phase: str, **fields) -> str:
    """GUI가 구독하는 도킹 상태 JSON. ROS·Qt 없이 형식을 단위 시험할 수 있다."""
    body = {"event": "shelf_dock", "shelf": str(shelf), "phase": str(phase), **fields}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def ray_yaw(robot_yaw: float, bearing: float) -> float:
    """레이를 쏠 방향. 화면 오른쪽(양수 bearing)은 yaw 를 줄인다."""
    return wrap_pi(float(robot_yaw) - float(bearing))


def camera_center_and_bearing(u: float, k_calib, calib_width: int, frame_width: int) -> tuple[float, float]:
    """영상 중앙 기준과 지도 광선 보정각.

    현장 비교 모드에서는 K를 전혀 쓰지 않는다. 테이프를 영상 정중앙에 PID로
    맞췄으므로 bearing은 0이고 광선은 AMCL yaw 방향으로 간다.
    """
    if not USE_CAMERA_CALIBRATION:
        return float(frame_width) / 2.0, 0.0
    fx, _fy, cx, _cy = scale_k(k_calib, calib_width, frame_width)
    return cx, bearing_rad(u, fx, cx)


def plan_dock(shelf: str, robot_pose, frame, grid, k_calib, frame_width: int,
              calib_width: int = 640):
    """도킹 이동 계획. `(moves, info)` 또는 `(None, info)`.

    `robot_pose` 는 `(x, y, yaw)`. 이 함수를 부르기 전에 로봇은 이미
    `SHELF_YAW[shelf] + EXTRA_TURN_RAD` 자세로 서 있어야 한다.
    """
    info: dict = {"shelf": shelf}
    if shelf not in SHELF_YAW:
        info["error"] = f"unknown shelf: {shelf}"
        return None, info

    u = centroid_u(frame)
    if u is None:
        info["error"] = "marker not found"
        return None, info
    info["u_px"] = u

    _cx, bearing = camera_center_and_bearing(u, k_calib, calib_width, frame_width)
    info["bearing_rad"] = bearing

    rx, ry, ryaw = robot_pose
    yaw = ray_yaw(ryaw, bearing)
    info["ray_yaw_rad"] = yaw

    hit = first_occupied(grid, rx, ry, yaw, max_m=MAX_RANGE_M)
    if hit is None:
        info["error"] = "raycast found no wall"
        return None, info
    (hx, hy), dist = hit
    info["hit_xy"] = (hx, hy)
    info["hit_dist_m"] = dist

    # 충돌점은 서가 표면이다. 서가를 보는 방향의 반대로 1cm 물러난 지점을
    # 목표로 잡고, x축 뒤 y축 순서로 접근한다.
    approach_x = hx - CLEARANCE_M * math.cos(SHELF_YAW[shelf])
    approach_y = hy - CLEARANCE_M * math.sin(SHELF_YAW[shelf])
    info["approach_xy"] = (approach_x, approach_y)
    moves = axis_aligned_moves(rx, ry, ryaw, approach_x, approach_y,
                               final_yaw=FINAL_YAW_RAD)
    info["moves"] = [(m.kind, m.value) for m in moves]
    return moves, info


# ══════════════════════════════════════════════════════════════════════════
# 실행부 — 위 `plan_dock` 은 순수 함수로 그대로 둔다. 여기서부터는 ROS 로 실제로
# 로봇을 돌린다. 구조는 `app/core/marker_dock.py` 를 그대로 따른다: 전용 노드 +
# `spin_once`, `/dev/shm` 프레임 탭, `cmd_vel_dock` 발행, 취소는 구독 콜백이 세대
# 번호로 세우고 이 루프가 tick 마다 읽는다.
# ══════════════════════════════════════════════════════════════════════════

#: 앞캠 640x480 보정값을 이 프레임 폭(320)에 맞춰 `scale_k` 가 스케일한다.
FRONT_CAM_K_640 = (609.15651744, 607.39537016, 278.17496904, 250.36175645)

#: 이보다 오래된 앞캠 프레임은 못 믿는다 (marker_dock.FRAME_STALE_SEC 와 같은 값·이유).
#
# ⚠️ 이 값과 비교하는 "지금"은 **`time.monotonic()`** 이어야 한다 — 프레임 탭의
#    stamp 가 그 시계다(`aba_ai_service/follower_perception/scripts/frame_tap.py`
#    `write()`). `time.time()`(epoch) 과 섞으면 두 시계의 기준점이 달라 차이가
#    억 단위로 벌어져서 **항상 stale** 로 판정된다 — 표식을 영영 못 보고 도킹이
#    매번 frame_stale 로 실패한다(2026-08-04 리뷰 P0, 실측 재현. `marker_dock.py`
#    는 원래부터 `time.monotonic()` 을 쓴다).
FRAME_STALE_SEC = 0.4

#: 센서/프레임을 기다리는 상한(초).
SENSOR_WAIT_SEC = 5.0
FRAME_WAIT_SEC = 2.0

#: `/libi/camera_select` 재발행 주기(초) — 송출기 만료(3.0초, `libi_perception.config.
#: CAMERA_SELECT_EXPIRY_SEC`)의 절반 이하. `libi_modes/common/person_block.py` 의
#: `_CAMERA_RENEW_SEC` 와 같은 값·같은 이유.
#
# ⚠️ 왜 여기서 또 보내나 — `PersonBlockGuard`(주행 중 앞캠 요청)는
#    `active_command == "navigate"` 일 때만 동작한다. 도킹 중엔 `active_command`
#    가 `"shelf_dock"` 이라 그 가드가 멈추고, **아무도 앞캠을 안 잡는다.**
#    `camera_sender` 는 선택 안 된 캠을 8틱에 한 번(≈1.9Hz)만 보므로 `FRAME_STALE_SEC`
#    (0.4초) 판정에 늘 걸린다(2026-08-04 리뷰 P1).
CAMERA_RENEW_SEC = 1.0


def _should_renew_camera(last_sent_at: float | None, now: float,
                         renew_sec: float = CAMERA_RENEW_SEC) -> bool:
    """`/libi/camera_select` 를 다시 보낼 때가 됐나 — 순수 함수(`person_block.py` 의
    `_request_camera` 와 같은 판단을 ROS 없이 시험하려고 분리했다)."""
    return last_sent_at is None or (now - last_sent_at) >= renew_sec


def _wait_for_fresh_frame(read_tap_fn, now_fn, sleep_fn, deadline_sec: float,
                          stale_sec: float, on_tick=lambda: None):
    """`read_tap_fn()` 이 주는 `(frame, seq, stamp)` 중 신선한 것을 기다린다. 없으면
    `None`.

    순수 함수 — `read_tap_fn`/`now_fn`/`sleep_fn` 을 주입받아 ROS 없이 시험한다.

    ⚠️ `now_fn` 과 `stamp` 는 **같은 시계**여야 한다(위 `FRAME_STALE_SEC` 주석).
    """
    deadline = now_fn() + deadline_sec
    while now_fn() < deadline:
        on_tick()
        got = read_tap_fn()
        if got is not None:
            frame, _seq, stamp = got
            if now_fn() - stamp <= stale_sec:
                return frame
        sleep_fn(0.05)
    return None

_lock = threading.Lock()
_running = False
_gen = 0                #: 도킹을 시작할 때마다 오른다
_cancel_gen = -1         #: 취소를 요청받은 세대


def request_cancel() -> bool:
    """진행 중인 서가 도킹을 끊는다. `fleet_link` 의 **구독 콜백**에서 부른다."""
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


def unlock_payload(args: dict) -> dict | None:
    """`args["node"]` 로 **즉시 해제**(`ttl_sec=0`) `/libi/node_block` payload 를 만든다.

    순수 함수 — 발행은 호출자(ROS 층)의 몫이다. `node` 가 없으면 `None` 을 돌려주고,
    호출자는 발행을 건너뛴 채 경고를 남겨야 한다 — 조용히 넘어가면 그 서가 정점이
    사람이 걸어 둔 TTL 만료까지 잠긴 채로 남는다.

    수신측 계약: `NodeBlockRegistry.set`(`aba_fms_service/backend/app/node_block.py`)
    — `ttl_sec <= 0` 은 이 owner(`reason`)의 차단만 푼다.
    """
    node = args.get("node")
    if node is None:
        return None
    return {"node": int(node), "ttl_sec": 0.0, "reason": "shelf_dock"}


def _release_lock_before_moving(args: dict, publish_fn, warn_fn) -> tuple[int | None, float | None]:
    """`plan_dock` 의 성공/실패를 **아예 모른 채** 잠금 해제를 시도한다.

    `_run` 이 이 함수를 `plan_dock` 결과를 보기 **전에** 한 번만 호출한다 — 그래서
    "성공 경로"/"실패 경로"가 따로 없다. 이 함수 자체가 성공/실패 분기를 안 가지므로
    실패 경로에서 빠뜨릴 방법이 구조적으로 없다(2026-08-04 리뷰 P0-Important 대응).

    `publish_fn(payload)` / `warn_fn(msg)` 을 주입받아 ROS 없이 시험한다. `(unlocked_
    node, unlocked_at)` — 둘 다 `None` 이면 못 보낸 것(그때는 `warn_fn` 이 이미
    불렸다).
    """
    unlock = unlock_payload(args)
    if unlock is None:
        warn_fn(f"[서가도킹] args 에 node 가 없어 잠금 해제를 못 보낸다: {args.get('shelf')}")
        return None, None
    publish_fn(unlock)
    return unlock["node"], time.time()


def run_shelf_dock(args: dict) -> tuple[bool, int, dict, str]:
    """`shelf_dock` 명령의 실행 진입점. `(ok, status, data, msg)` — fleet_link 계약.

    순서: 서가 yaw 로 회전 → `EXTRA_TURN_RAD` 추가 회전 → 앞캠 프레임·`/map`·`/amcl_pose`
    로 `plan_dock` → **`plan_dock` 성공/실패와 무관하게 잠금 해제를 먼저 알리고**
    (결과가 아니라 별도 토픽 `/libi/node_block` — 정밀 이동이 실제로 시작하는 순간
    알려야 한다. 결과 payload 에 얹으면 `MoveExecutor.run()` 이 끝날 때까지 몇
    초~수십 초를 FMS 가 모른 채 통행을 계속 막고, `plan_dock` 실패 시엔 아예 안
    풀려 TTL 만료까지 잠긴다) → 접근 이동 실행 →
    `record_outbound(moves, heading, final_yaw)`.

    `args["node"]`(서가 정점 번호)가 있어야 잠금 해제를 보낼 수 있다 — 없으면
    경고만 남기고 도킹 자체는 계속 진행한다(`unlock_payload` 참고).
    """
    args = args or {}
    shelf = str(args.get("shelf") or "").strip()
    if shelf not in SHELF_YAW:
        return False, 400, {"docked": False}, f"unknown shelf: {shelf}"

    global _running, _gen
    with _lock:
        if _running:
            return False, 409, {"docked": False}, "서가 도킹이 이미 진행 중이다"
        _gen += 1
        my_gen = _gen
        _running = True
    try:
        return _run(shelf, my_gen, args)
    finally:
        with _lock:
            _running = False


def _run(shelf: str, my_gen: int, args: dict) -> tuple[bool, int, dict, str]:
    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
    from nav_msgs.msg import OccupancyGrid, Odometry
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    from app.core import fleet_link
    from app.core.backup_runner import MoveExecutor, record_return_targets
    from app.core.marker_dock import read_tap
    from app.core.ros_bridge import quat_to_yaw
    from app.shelf.raycast import Grid

    ctx = fleet_link.get_context()
    if ctx is None:
        return False, 503, {"docked": False}, "fleet_link ROS context 가 아직 없다"

    node = rclpy.create_node("shelf_dock_exec", context=ctx)
    executor = SingleThreadedExecutor(context=ctx)
    executor.add_node(node)
    pub = node.create_publisher(Twist, "cmd_vel_dock", 10)
    # 이동 시작 "전" 잠금 해제 알림 — 결과 payload 와 분리된 별도 채널(위 docstring
    # 참고). FMS 가 이미 구독 중인 로봇발 채널을 그대로 쓴다(새 토픽을 안 만든다).
    node_block_pub = node.create_publisher(String, "/libi/node_block", 10)
    # 관리자 패널은 이 토픽만 읽는다. 제어 루프의 Python 로그를 SSH로 긁지 않는다.
    dock_status_pub = node.create_publisher(String, "shelf_dock_status", 20)
    # 도킹 내내 앞캠을 선택 상태로 유지 — `PersonBlockGuard` 는 navigate 중에만
    # 이 토픽을 갱신하므로 도킹 중(active_command=shelf_dock)엔 우리가 직접 잡아야
    # 한다(위 `CAMERA_RENEW_SEC` 주석, 2026-08-04 리뷰 P1). 구독자가 요구하는 QoS
    # (depth=1·RELIABLE·TRANSIENT_LOCAL, `libi_modes/main.py` 의 `_CAMERA_SELECT_QOS`
    # 와 같은 값)를 맞춘다 — 안 맞으면 /map 과 같은 이유로 메시지가 영영 안 간다.
    camera_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                            durability=DurabilityPolicy.TRANSIENT_LOCAL)
    camera_select_pub = node.create_publisher(String, "/libi/camera_select", camera_qos)
    camera_state: dict = {"sent_at": None}
    log = node.get_logger()

    map_state: dict = {}
    amcl_state: dict = {}
    odom_state: dict = {}

    def _on_map(msg) -> None:
        map_state["grid"] = Grid(
            data=list(msg.data), width=msg.info.width, height=msg.info.height,
            resolution=msg.info.resolution,
            origin_x=msg.info.origin.position.x, origin_y=msg.info.origin.position.y)
        map_state["at"] = time.monotonic()

    def _on_amcl(msg) -> None:
        p = msg.pose.pose
        amcl_state["pose"] = (p.position.x, p.position.y, quat_to_yaw(p.orientation))
        amcl_state["at"] = time.monotonic()

    def _on_odom(msg) -> None:
        p = msg.pose.pose.position
        odom_state["pose"] = (p.x, p.y, quat_to_yaw(msg.pose.pose.orientation))
        odom_state["at"] = time.monotonic()

    # ⚠️ `/map` 은 절대경로("/map") + TRANSIENT_LOCAL + RELIABLE + depth 1 이어야 한다.
    #    기본 VOLATILE 로 두면 발행자(TRANSIENT_LOCAL)와 QoS 가 안 맞아 메시지를
    #    영영 못 받는다(mismatch — 구독은 되지만 콜백이 한 번도 안 불린다).
    map_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(OccupancyGrid, "/map", _on_map, map_qos)
    # AMCL도 /map처럼 마지막 자세를 TRANSIENT_LOCAL로 보존한다. 도킹은
    # Nav2가 멈춘 직후 시작하므로 기본 VOLATILE 구독이면 새 자세가 올 때까지
    # 기다리다 센서 preflight가 실패할 수 있다.
    node.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", _on_amcl, map_qos)
    node.create_subscription(Odometry, "/odom", _on_odom, 10)

    def spin() -> None:
        for _ in range(4):
            executor.spin_once(timeout_sec=0.0)
        # 도킹 시작부터 끝까지(센서 대기 포함) 앞캠을 계속 선택 상태로 유지한다.
        # `spin()` 은 이 함수 전체에서 tick 마다 불리므로 여기 얹는 게 가장 확실하다.
        now = time.monotonic()
        if _should_renew_camera(camera_state["sent_at"], now):
            camera_select_pub.publish(String(data="front"))
            camera_state["sent_at"] = now

    def report(phase: str, **fields) -> None:
        dock_status_pub.publish(String(data=dock_status_payload(shelf, phase, **fields)))

    def publish(lin: float, ang: float) -> None:
        t = Twist()
        t.linear.x = float(lin)
        t.angular.z = float(ang)
        pub.publish(t)

    def pose_fn():
        # MoveExecutor 가 tick 마다 이 함수를 부른다 — 그 부작용으로 실행 주기(페이싱)와
        # ROS spin 을 겸한다(marker_dock 의 spin()+sleep(period) 과 같은 역할).
        time.sleep(0.02)
        spin()
        return odom_state.get("pose", (0.0, 0.0, 0.0))

    def cancel_fn() -> bool:
        return _cancelled(my_gen)

    def finish(ok: bool, status: int, data: dict, msg: str):
        report("completed" if ok else "failed", status=status, message=msg)
        for _ in range(5):
            publish(0.0, 0.0)
            time.sleep(0.02)
        executor.remove_node(node)
        node.destroy_node()
        return ok, status, data, msg

    mover = MoveExecutor(publish_twist=publish, pose_fn=pose_fn)
    report("started", clearance_m=CLEARANCE_M)

    # ── 센서 대기 ────────────────────────────────────────────────────────────
    deadline = time.monotonic() + SENSOR_WAIT_SEC
    while not ("grid" in map_state and "pose" in amcl_state and "pose" in odom_state) \
            and time.monotonic() < deadline:
        spin()
        time.sleep(0.05)
    missing = [n for n, ok in (("/map", "grid" in map_state),
                              ("/amcl_pose", "pose" in amcl_state),
                              ("/odom", "pose" in odom_state)) if not ok]
    if missing:
        return finish(False, 503, {"docked": False},
                      f"센서가 안 들어온다: {', '.join(missing)}")

    pose_before = amcl_state["pose"]

    # ① 서가 방향으로 회전 (절대 yaw, map 프레임)
    rx, ry, ryaw = pose_before
    ok, why = mover.run([Move(TURN, wrap_pi(SHELF_YAW[shelf] - ryaw))], cancel=cancel_fn)
    if not ok:
        return finish(False, 499 if why == "canceled" else 502, {"docked": False},
                      f"서가 방향 회전 실패: {why}")

    # ② 표식을 화각에 넣기 위한 추가 회전
    ok, why = mover.run([Move(TURN, EXTRA_TURN_RAD)], cancel=cancel_fn)
    if not ok:
        return finish(False, 499 if why == "canceled" else 502, {"docked": False},
                      f"추가 회전 실패: {why}")

    # 회전 중 spin 이 amcl_state 를 계속 갱신했다 — 최신값을 그대로 쓴다(D14: AMCL
    # 오차를 1:1 로 받아들이기로 한 결정. 실측 오차는 아래 로그로 남긴다).
    robot_pose = amcl_state["pose"]

    # ③/⑤ 테이프 중앙정렬은 옆축 이동 전과 후에 각각 수행한다. 두 관측 사이에
    # 이동했으므로 두 번째 결과만 최종 PGM 광선의 기준으로 쓴다.
    def center_marker_pid(phase: str):
        frame = None
        trace = []
        last_report_at = 0.0
        last_seq = None
        last_new_at = time.monotonic()
        prev_error = None
        filtered_u = None
        integral = 0.0
        prev_t = None
        stable = 0
        deadline = time.monotonic() + MARKER_SERVO_TIMEOUT_SEC
        while time.monotonic() < deadline:
            spin()
            if cancel_fn():
                return None, trace, "canceled"
            got = read_tap("front")
            now = time.monotonic()
            if got is None:
                if now - last_new_at > FRAME_STALE_SEC:
                    return None, trace, "frame_empty"
                publish(0.0, 0.0)
                time.sleep(1.0 / MARKER_SERVO_HZ)
                continue
            frame, seq, stamp = got
            if now - stamp > FRAME_STALE_SEC:
                return None, trace, "frame_stale"
            if seq == last_seq:
                publish(0.0, 0.0)
                if now - last_new_at > FRAME_STALE_SEC:
                    return None, trace, "frame_not_updating"
                time.sleep(1.0 / MARKER_SERVO_HZ)
                continue
            last_seq, last_new_at = seq, now
            u = centroid_u(frame)
            if u is None:
                return None, trace, "marker_not_found"
            cx, _bearing = camera_center_and_bearing(u, FRONT_CAM_K_640, 640, frame.shape[1])
            raw_u = float(u)
            filtered_u = raw_u if filtered_u is None else (
                MARKER_CENTER_LPF_ALPHA * raw_u
                + (1.0 - MARKER_CENTER_LPF_ALPHA) * filtered_u)
            error = (filtered_u - float(cx)) / (frame.shape[1] / 2.0)
            dt = (now - prev_t) if prev_t is not None else (1.0 / MARKER_SERVO_HZ)
            dt = min(max(dt, 1e-3), 0.5)
            integral = max(-1.0, min(1.0, integral + error * dt))
            derivative = 0.0 if prev_error is None else (error - prev_error) / dt
            angular_z = visual_servo_angular_z(error, integral, derivative)
            aligned = abs(filtered_u - float(cx)) <= MARKER_CENTER_TOL_PX
            stable = stable + 1 if aligned else 0
            trace.append((raw_u, filtered_u, error, angular_z, stable))
            publish(0.0, 0.0 if aligned else angular_z)
            prev_error, prev_t = error, now
            if stable >= MARKER_CENTER_STABLE_FRAMES:
                log.info(f"[서가도킹][{phase}] 테이프 중앙정렬 완료 yaw_map={amcl_state["pose"][2]:.4f}")
                return frame, trace, ""
            time.sleep(1.0 / MARKER_SERVO_HZ)
        return None, trace, "marker_timeout"

    def current_amcl():
        spin()
        pose = amcl_state.get("pose")
        at = amcl_state.get("at")
        if pose is None or at is None or time.monotonic() - at > SENSOR_STATE_STALE_SEC:
            return None
        return pose

    def move_lateral_axis_pid(target: float, axis: tuple[float, float]):
        pose = current_amcl()
        if pose is None:
            return False, "amcl_stale", []
        error = target - axis_projection(pose[0], pose[1], axis)
        heading = math.atan2(axis[1], axis[0]) + (math.pi if error < 0.0 else 0.0)
        odom = odom_state.get("pose")
        if odom is None:
            return False, "odom_missing", []
        ok, why = mover.run([Move(TURN, wrap_pi(heading - odom[2]))], cancel=cancel_fn)
        if not ok:
            return False, why, []
        trace = []
        stable = 0
        deadline = time.monotonic() + MAP_AXIS_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if cancel_fn():
                publish(0.0, 0.0)
                return False, "canceled", trace
            pose = current_amcl()
            odom = odom_state.get("pose")
            odom_at = odom_state.get("at")
            if pose is None or odom is None or odom_at is None or time.monotonic() - odom_at > SENSOR_STATE_STALE_SEC:
                publish(0.0, 0.0)
                return False, "pose_stale", trace
            error = target - axis_projection(pose[0], pose[1], axis)
            stable = stable + 1 if abs(error) <= MAP_AXIS_TOL_M else 0
            linear = bounded_pid_linear(error, MAP_AXIS_KP, MAP_AXIS_MAX_LINEAR_MPS)
            angular = max(-MAP_AXIS_MAX_ANG, min(MAP_AXIS_MAX_ANG,
                MAP_AXIS_HEADING_KP * wrap_pi(heading - odom[2])))
            trace.append((pose[0], pose[1], error, linear, angular, stable))
            if stable >= MAP_AXIS_STABLE_TICKS:
                publish(0.0, 0.0)
                return True, "", trace
            publish(linear, angular)
            time.sleep(1.0 / MARKER_SERVO_HZ)
        publish(0.0, 0.0)
        return False, "lateral_timeout", trace

    def final_forward_pid(grid):
        trace = []
        last_seq = None
        last_new_at = time.monotonic()
        prev_error = None
        filtered_u = None
        integral = 0.0
        prev_t = None
        stable = 0
        deadline = time.monotonic() + FINAL_APPROACH_TIMEOUT_SEC
        while time.monotonic() < deadline:
            spin()
            if cancel_fn():
                publish(0.0, 0.0)
                return False, "canceled", trace
            got = read_tap("front")
            now = time.monotonic()
            if got is None:
                if now - last_new_at > FRAME_STALE_SEC:
                    publish(0.0, 0.0)
                    return False, "frame_empty", trace
                publish(0.0, 0.0)
                time.sleep(1.0 / MARKER_SERVO_HZ)
                continue
            frame_now, seq, stamp = got
            if now - stamp > FRAME_STALE_SEC:
                publish(0.0, 0.0)
                return False, "frame_stale", trace
            if seq == last_seq:
                publish(0.0, 0.0)
                if now - last_new_at > FRAME_STALE_SEC:
                    return False, "frame_not_updating", trace
                time.sleep(1.0 / MARKER_SERVO_HZ)
                continue
            last_seq, last_new_at = seq, now
            pose = current_amcl()
            if pose is None:
                publish(0.0, 0.0)
                return False, "amcl_stale", trace
            u = centroid_u(frame_now)
            if u is None:
                publish(0.0, 0.0)
                return False, "marker_not_found", trace
            cx, bearing = camera_center_and_bearing(u, FRONT_CAM_K_640, 640, frame_now.shape[1])
            hit = first_occupied(grid, pose[0], pose[1], ray_yaw(pose[2], bearing), max_m=MAX_RANGE_M)
            if hit is None:
                publish(0.0, 0.0)
                return False, "raycast_no_wall", trace
            _hit_xy, distance = hit
            raw_u = float(u)
            filtered_u = raw_u if filtered_u is None else (
                MARKER_CENTER_LPF_ALPHA * raw_u
                + (1.0 - MARKER_CENTER_LPF_ALPHA) * filtered_u)
            image_error = (filtered_u - float(cx)) / (frame_now.shape[1] / 2.0)
            dt = (now - prev_t) if prev_t is not None else (1.0 / MARKER_SERVO_HZ)
            dt = min(max(dt, 1e-3), 0.5)
            integral = max(-1.0, min(1.0, integral + image_error * dt))
            derivative = 0.0 if prev_error is None else (image_error - prev_error) / dt
            angular = visual_servo_angular_z(image_error, integral, derivative)
            remaining = distance - CLEARANCE_M
            stable = stable + 1 if remaining <= FINAL_APPROACH_TOL_M else 0
            linear = 0.0 if stable else max(0.0, bounded_pid_linear(
                remaining, FINAL_APPROACH_KP, FINAL_APPROACH_MAX_LINEAR_MPS))
            trace.append((distance, remaining, raw_u, filtered_u, image_error, linear, angular, stable))
            if now - last_report_at >= DOCK_STATUS_UPDATE_SEC:
                report("final_progress", pgm_distance_m=round(distance, 3),
                       remaining_to_clearance_m=round(max(0.0, remaining), 3),
                       marker_error_px=round(filtered_u - float(cx), 1),
                       linear_mps=round(linear, 3))
                last_report_at = now
            if stable >= FINAL_APPROACH_STABLE_TICKS:
                publish(0.0, 0.0)
                return True, "", trace
            publish(linear, angular)
            prev_error, prev_t = image_error, now
            time.sleep(1.0 / MARKER_SERVO_HZ)
        publish(0.0, 0.0)
        return False, "final_timeout", trace

    first_frame, first_center_trace, why = center_marker_pid("초기 중앙정렬")
    if first_frame is None:
        return finish(False, 499 if why == "canceled" else 502,
                      {"docked": False, "center_trace": first_center_trace},
                      f"초기 비주얼 서보 실패: {why}")

    report("initial_marker_centered", frames=len(first_center_trace))

    if VISUAL_SERVO_ONLY:
        return finish(False, 409, {"docked": False, "servo_centered": True,
                                   "center_trace": first_center_trace},
                      "PID 비주얼 서보 중앙 정렬 확인 완료(테스트 모드)")

    # 첫 PGM 관측은 옆축 목표만 만든다. 이 목록을 실행하지 않는다.
    robot_pose = current_amcl()
    grid = map_state.get("grid")
    if robot_pose is None or grid is None:
        return finish(False, 503, {"docked": False}, "옆축 PID 전 AMCL 또는 map 이 오래됐다")
    moves, first_info = plan_dock(shelf, robot_pose, first_frame, grid, FRONT_CAM_K_640, first_frame.shape[1])
    if moves is None:
        return finish(False, 502, {"docked": False, **first_info},
                      f"첫 PGM 관측 실패: {first_info.get("error")}")

    unlocked_node, unlocked_at = _release_lock_before_moving(
        args, publish_fn=lambda payload: node_block_pub.publish(String(data=json.dumps(payload))),
        warn_fn=log.warning)
    normal_axis, lateral_axis = shelf_axes(shelf)
    lateral_target = axis_projection(*first_info["approach_xy"], lateral_axis)
    report("lateral_start", target_m=round(lateral_target, 3),
           initial_error_m=round(lateral_target - axis_projection(robot_pose[0], robot_pose[1], lateral_axis), 3))
    side_start_pose = current_amcl()
    if side_start_pose is None:
        return finish(False, 503, {"docked": False}, "옆축 PID 시작 전 AMCL 이 오래됐다")
    ok, why, lateral_trace = move_lateral_axis_pid(lateral_target, lateral_axis)
    if not ok:
        return finish(False, 499 if why == "canceled" else 502,
                      {"docked": False, "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
                       "first_observation": first_info, "lateral_trace": lateral_trace},
                      f"옆축 AMCL PID 실패: {why}")

    report("lateral_complete", final_error_m=round(lateral_trace[-1][2], 3) if lateral_trace else 0.0)

    # 옆축 이동 후에는 첫 프레임/첫 광선을 절대 재사용하지 않는다.
    lateral_pose = current_amcl()
    if lateral_pose is None:
        return finish(False, 503, {"docked": False}, "옆축 PID 뒤 AMCL 이 오래됐다")
    second_frame, second_center_trace, why = center_marker_pid("재관측 중앙정렬")
    if second_frame is None:
        return finish(False, 499 if why == "canceled" else 502,
                      {"docked": False, "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
                       "first_observation": first_info, "lateral_trace": lateral_trace,
                       "recenter_trace": second_center_trace},
                      f"재관측 비주얼 서보 실패: {why}")
    report("reobserve_marker_centered", frames=len(second_center_trace))
    robot_pose = current_amcl()
    if robot_pose is None:
        return finish(False, 503, {"docked": False}, "재관측 뒤 AMCL 이 오래됐다")
    _unused_moves, final_info = plan_dock(shelf, robot_pose, second_frame, grid, FRONT_CAM_K_640, second_frame.shape[1])
    if _unused_moves is None:
        return finish(False, 502, {"docked": False, **final_info},
                      f"재관측 PGM 실패: {final_info.get("error")}")

    # 최종축은 고정 거리 명령이 아니다. 새 프레임의 테이프 중점 PID와 새 AMCL+PGM
    # 거리로 매 tick 제어하고, 서가 표면 2 cm 앞에서만 종료한다.
    report("final_start", clearance_m=CLEARANCE_M)
    ok, why, final_trace = final_forward_pid(grid)
    pose_after = current_amcl() or pose_fn()
    if ok:
        # FMS가 backup 을 명시적으로 보낼 때만, 최종축 → 옆축의 역순 체크포인트를
        # AMCL로 재계산해 돌아간다. 단순 거리 합산 복귀를 쓰지 않는다.
        record_return_targets([(lateral_pose[0], lateral_pose[1]),
                               (side_start_pose[0], side_start_pose[1])])
    payload = {
        "docked": ok, "shelf": shelf, "clearance_m": CLEARANCE_M,
        "first_observation": first_info, "lateral_target_m": lateral_target,
        "lateral_trace": lateral_trace, "recenter_trace": second_center_trace,
        "final_observation": final_info, "final_trace": final_trace,
        "pose_before": pose_before, "pose_after": pose_after,
        "unlocked_node": unlocked_node, "unlocked_at": unlocked_at,
    }
    if not ok:
        return finish(False, 499 if why == "canceled" else 502, payload,
                      f"최종 PGM+비주얼 PID 접근 실패: {why}")
    return finish(True, 200, payload, "")
