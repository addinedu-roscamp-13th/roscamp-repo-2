"""
ROS2 백그라운드 브리지.

별도 스레드에서 rclpy를 실행하여 /scan, /odom, /map 을 구독하고
최신 데이터를 메모리에 캐싱한다. WebSocket 핸들러는 이 캐시를 읽는다.

FastAPI 서버를 ROS2 환경과 함께 실행할 때 (source /opt/ros/jazzy/setup.bash)
Automatically activated when rclpy is importable.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any

from app.core.nav_goal_tracker import NavGoalTracker

# ── 공유 상태 (스레드 세이프 읽기/쓰기) ──────────────────────
_lock = threading.Lock()
_state: dict[str, Any] = {
    "ros_active": False,
    "scan": None,      # LaserScan 최신값
    "odom": None,      # Odometry 최신값
    "map": None,       # OccupancyGrid 최신값 (SLAM 중일 때만)
    "nav_goal": None,  # 최근 자율주행 목표
    "initial_pose": None,  # 최근 초기 위치(AMCL)
    "plan": None,      # Nav2 계획 경로 (/plan)
    "tf_pose": None,   # TF(map->base_link) 기반 pose {x,y,yaw}
    "local_costmap": None,   # local costmap (map 프레임으로 변환됨)
    "global_costmap": None,  # global costmap
    "battery": {"percent": None, "voltage": None},
    "us_range": None,   # /us_sensor/range → {sensor,distance_m,distance_cm}
    "ir_range": None,   # /ir_sensor/range → {sensor,left,center,right,obstacle,raw}
}

# rclpy node (publish용)
_node = None

# 조이스틱/제어 타겟: 실제 로봇
_desired_target = "real"


def set_target(target: str) -> bool:
    """제어 타겟을 변경한다. 실제 퍼블리셔 타입 전환은 브리지 타이머가 반영한다."""
    global _desired_target
    if target != "real":
        return False
    _desired_target = "real"
    return True


#: 이 노드가 속도를 내보내는 토픽.
#
# ⚠️ `/cmd_vel` 이 **아니다.** 바퀴로 가는 문은 twist_mux 하나뿐이다
#    (pinky_bringup/config/twist_mux.yaml). 여기는 `manual` 입력(priority 200)이라
#    다른 어떤 자동 제어보다 우선한다 — 사람이 잡으면 사람이 이긴다.
#
# [2026-07-29] 수동 주행 발행 제거 — twist_mux 의 manual 입력을 없앴다.
#   남겨 두면 토픽은 나가는데 아무도 안 듣는 상태라 "붙었는데 안 움직인다"로 나타난다.
#   정지는 패널 비상정지(`/cmd_vel_stop`, priority 255)가 담당한다.


def get_cmd_vel_info() -> dict[str, Any]:
    """[2026-07-29] 수동 발행 경로가 사라져 항상 빈 정보다.

    엔드포인트를 지우지 않고 남긴 이유: 대시보드가 이 키들을 읽고 있고, 없으면 화면이
    깨진다. 값이 0/None 이라는 것 자체가 "이 경로는 없다"는 뜻이다.
    """
    return {
        "target": _desired_target,
        "cmd_vel_type": None,
        "subscriber_count": 0,
        "subscriber_types": [],
        "removed": "manual drive removed 2026-07-29 (twist_mux manual input deleted)",
    }


def is_active() -> bool:
    return _state["ros_active"]


def get_state() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def get_topic(key: str) -> Any:
    with _lock:
        return _state.get(key)


def clear_map() -> None:
    """캐시된 맵/계획 경로/목표를 비운다 (맵 초기화 시 stale 데이터 제거)."""
    with _lock:
        _state["map"] = None
        _state["plan"] = None
        _state["nav_goal"] = None
        _state["local_costmap"] = None
        _state["global_costmap"] = None


def publish_cmd_vel(linear: float, angular: float) -> bool:
    """[2026-07-29 제거] 수동 주행 발행.

    twist_mux 의 `cmd_vel_manual`(priority 200) 입력을 없앴다 — 시나리오에 수동 조작이
    없는데 **자율 제어 전부를 이기는 문**만 열려 있었다(pinky_bringup/config/twist_mux.yaml).
    퍼블리셔를 남겨 두면 토픽은 나가는데 아무도 안 듣는 상태가 되어, "붙었는데 안 움직인다"
    로 나타난다. 그래서 발행 자체를 없애고 여기서 False 를 돌려준다.

    정지가 필요하면 **비상정지 경로**를 쓴다: libi_gui 가 `/cmd_vel_stop`(priority 255).
    """
    return False

def publish_nav_goal(x: float, y: float, yaw: float) -> bool:
    """Nav2 /goal_pose 발행 (map 프레임 기준)."""
    global _node
    if _node is None:
        return False
    try:
        import math
        from geometry_msgs.msg import PoseStamped
        msg = PoseStamped()
        msg.header.frame_id = "map"
        msg.header.stamp.sec = 0
        msg.header.stamp.nanosec = 0
        msg.pose.position.x = float(x)
        msg.pose.position.y = float(y)
        msg.pose.orientation.z = math.sin(float(yaw) / 2.0)
        msg.pose.orientation.w = math.cos(float(yaw) / 2.0)
        _node._nav_goal_pub.publish(msg)
        with _lock:
            _state["nav_goal"] = {"x": x, "y": y, "yaw": yaw}
        return True
    except Exception:
        return False


def publish_initial_pose(x: float, y: float, yaw: float) -> bool:
    """AMCL /initialpose 발행 — 지도 위 로봇 초기 위치를 알려준다."""
    global _node
    if _node is None:
        return False
    try:
        from geometry_msgs.msg import PoseWithCovarianceStamped
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = _node.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(x)
        msg.pose.pose.position.y = float(y)
        msg.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
        msg.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)
        # 대각 공분산 (위치 0.25m², 방향 0.07rad²)
        cov = [0.0] * 36
        cov[0] = 0.25
        cov[7] = 0.25
        cov[35] = 0.07
        msg.pose.covariance = cov
        _node._initial_pose_pub.publish(msg)
        with _lock:
            _state["initial_pose"] = {"x": x, "y": y, "yaw": yaw}
        return True
    except Exception:
        return False


def quat_to_yaw(q) -> float:
    """Quaternion → yaw(rad)."""
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def get_current_pose() -> tuple[float, float, float] | None:
    """TF(map->base) 기반 (x, y, yaw). 아직 TF 미준비면 None."""
    pose = get_topic("tf_pose")
    if pose is None:
        return None
    return pose["x"], pose["y"], pose["yaw"]


def get_nav_snapshot() -> dict[str, Any]:
    """대시보드용 통합 스냅샷: map+pose+path+costmap+battery."""
    with _lock:
        return {
            "map": _state["map"],
            "pose": _state["tf_pose"],
            "path": _state["plan"] or [],
            "local_costmap": _state["local_costmap"],
            "global_costmap": _state["global_costmap"],
            "battery": dict(_state["battery"]),
        }


def send_nav_goal(x: float, y: float, yaw: float) -> bool:
    """NavigateToPose 액션으로 goal 전송(완료 대기 없이). 성공 요청 시 True."""
    node = _node
    if node is None:
        return False
    try:
        ok = node.send_nav_goal(float(x), float(y), float(yaw))
        if ok:
            with _lock:
                _state["nav_goal"] = {"x": x, "y": y, "yaw": yaw}
        return ok
    except Exception:
        return False


def nav_to(x: float, y: float, yaw: float, stop_event=None) -> bool:
    """NavigateToPose 로 주행하고 완료까지 블로킹 대기. 성공 True / 실패·중단 False.

    미션 워커 스레드에서 호출한다(액션 콜백은 spin 스레드에서 발생).
    """
    node = _node
    if node is None:
        return False
    return node.nav_to(float(x), float(y), float(yaw), stop_event)


def cancel_nav() -> None:
    node = _node
    if node is not None:
        try:
            node.cancel_active_goal()
        except Exception:
            pass


def slam_reset() -> bool:
    """/slam_toolbox/reset 호출 — 새 맵 시작."""
    node = _node
    if node is None:
        return False
    try:
        return node.slam_reset()
    except Exception:
        return False


def slam_save_map(name: str) -> bool:
    """/slam_toolbox/save_map 호출 — 현재 맵을 pgm+yaml 로 저장."""
    node = _node
    if node is None:
        return False
    try:
        return node.slam_save_map(name)
    except Exception:
        return False


# ── ROS2 브리지 스레드 ──────────────────────────────────────
def _bridge_thread() -> None:
    global _node
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.action import ActionClient
        from rclpy.time import Time
        from rclpy.qos import (
            QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy, QoSHistoryPolicy,
        )
        from geometry_msgs.msg import Twist, TwistStamped
        from nav_msgs.msg import OccupancyGrid, Odometry, Path
        from sensor_msgs.msg import LaserScan, Range
        from std_msgs.msg import Float32, String, UInt16MultiArray
        from pinky_interfaces.srv import SetLed, SetBrightness
        from pinky_interfaces.srv import Emotion as EmotionSrv
        from pinky_interfaces.srv import PlayBuzzer, PlayMelody, StopBuzzer, LcdText
        from std_srvs.srv import Trigger as LcdStop
        from nav2_msgs.action import NavigateToPose
        from nav2_msgs.msg import Costmap
        from action_msgs.msg import GoalStatus
        from tf2_ros import Buffer, TransformListener
        from slam_toolbox.srv import SaveMap, Reset

        if not rclpy.ok():
            rclpy.init()

        class BridgeNode(Node):
            def __init__(self) -> None:
                super().__init__("fastapi_ros_bridge")
                from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
                self._cmd_vel_type = None
                self._cmd_pub = None
                # [2026-07-29] /cmd_vel_manual 퍼블리셔·타입 동기화 타이머 제거
                #   (twist_mux 의 manual 입력을 없앴다 — publish_cmd_vel 주석 참고)
                self._nav_goal_pub = self.create_publisher(PoseStamped, "/goal_pose", 10)
                self._initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
                # 대시보드용 구독(scan/odom/map/plan/cmd_vel_raw/costmap/battery)은
                # "명령 실행"에는 전혀 필요 없다. 명령 경로가 쓰는 pose 는
                # get_current_pose()(TF 기반)로만 얻으므로 이 구독들과 무관하다.
                # FLEET_LINK_LITE=1(명령 실행 전용 경량 모드)이면 전부 건너뛴다 —
                # 이들이 Pi에서 초당 수십 건 역직렬화로 CPU를 크게 잡아먹는다.
                # 남는 것: goal_pose/initialpose/cmd_vel 발행, TF pose, nav 액션, slam 서비스.
                if not os.getenv("FLEET_LINK_LITE"):
                    self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
                    self.create_subscription(Odometry, "/odom", self._on_odom, 10)
                    self.create_subscription(OccupancyGrid, "/map", self._on_map, QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL, history=QoSHistoryPolicy.KEEP_LAST))
                    self.create_subscription(Path, "/plan", self._on_plan, 10)

                    # ── Nav2 대시보드(nav2_web_server) 흡수 ──────────────────
                    # local/global costmap: costmap / costmap_raw 둘 다 시도
                    for topic in ("local_costmap/costmap", "local_costmap/costmap_raw"):
                        self.create_subscription(Costmap, topic, self._on_local_costmap, 10)
                    for topic in ("global_costmap/costmap", "global_costmap/costmap_raw"):
                        self.create_subscription(Costmap, topic, self._on_global_costmap, 10)

                    # 배터리 (대시보드용)
                    self.create_subscription(
                        Float32, "/battery/percent",
                        lambda m: self._on_battery("percent", m.data), 10)
                    self.create_subscription(
                        Float32, "/battery/voltage",
                        lambda m: self._on_battery("voltage", m.data), 10)

                # 지도 위 내 위치를 어디서 얻나 — 모드에 따라 다르다.
                #
                # [2026-07-28] 경량 모드에서는 **TF 를 아예 안 쓴다.**
                #
                #   왜: `TransformListener` 는 `/tf` 를 통째로 구독하고 **모든 메시지를
                #   파이썬에서 역직렬화**한다. 이 로봇의 `/tf` 는 bringup 오도메트리만으로도
                #   30Hz 다(bringup.py:95). FLEET_LINK_LITE 로 대시보드 구독을 다 껐는데도
                #   이 프로세스가 코어의 33% 를 쓰고 있었고(2026-07-28 실측, 4코어 91% 포화),
                #   남은 상시 비용 중 가장 큰 후보가 이것이었다.
                #
                #   대신 `/amcl_pose` 를 직접 구독한다. AMCL 은 로봇이 update_min_d(0.02m)
                #   이상 움직였을 때만 내므로, 이 로봇 속도(0.07m/s)에서 최대 3.5msg/s 이고
                #   **정지 중에는 0** 이다. 30Hz 역직렬화가 사라진다.
                #
                #   바꿔도 되는 이유: **주행 판정은 이 pose 를 안 쓴다.** BT 의 도착 판정과
                #   fleet_node 의 배차는 노트북 robot_state_adapter 가 같은 `/amcl_pose` 를
                #   받아 `/robot_state` 로 낸다(providers.py 도 같은 타입으로 구독 중이다).
                #   여기 pose 는 robot_agent HTTP 조회와 로그용이다.
                #
                #   대가: AMCL 갱신 사이에는 최대 2cm 만큼 값이 낡는다. 화면 표시용으로는
                #   무의미한 오차다.
                #
                # ⚠️ 비-lite(대시보드) 모드는 그대로 둔다 — `_transform_origin_to_map` 이
                #    costmap origin(odom 프레임)을 map 으로 옮기는 데 TF 버퍼가 필요하다.
                #    그 경로는 costmap 구독과 함께만 살아 있다.
                self._pose_frame_used = None
                self._last_tf_warn = 0.0
                if os.getenv("FLEET_LINK_LITE"):
                    self._tf_buffer = None
                    self._tf_listener = None
                    self.create_subscription(
                        PoseWithCovarianceStamped, "/amcl_pose", self._on_amcl_pose, 10)
                    self.get_logger().info(
                        "[POSE] 경량 모드 — /amcl_pose 직접 구독 (TF 미사용)")
                else:
                    self._tf_buffer = Buffer()
                    self._tf_listener = TransformListener(
                        self._tf_buffer, self, spin_thread=False)
                    self.create_timer(0.2, self._update_pose_from_tf)

                # NavigateToPose 액션 클라이언트 + 완료대기 상태
                self._nav_action = ActionClient(self, NavigateToPose, "navigate_to_pose")
                self._goal_done = threading.Event()
                self._goal_result = False
                # Nav2 goal 은 한 번에 하나만 선점한다. fleet_node 가 같은 홉을
                # 재발행하거나 다음 홉을 너무 일찍 보내도 액션 서버에 바로 넣지 않고
                # 현재 goal 이 끝난 뒤 마지막 요청을 이어 보낸다. 그렇지 않으면
                # preemption 때 controller 가 cmd_vel 을 끊고 모터 watchdog 이 선다.
                self._nav_goal_lock = threading.Lock()
                self._nav_inflight_target = None
                self._nav_pending_target = None
                #: 목표 핸들의 수명(보관·세대·선취소)을 다루는 순수 상태기.
                #: 여기 두면 ROS 없이 단위테스트가 된다 — tests/test_nav_goal_tracker.py
                self._goals = NavGoalTracker()

                # slam_toolbox 서비스 클라이언트
                self._save_map_client = self.create_client(SaveMap, "/slam_toolbox/save_map")
                self._reset_client = self.create_client(Reset, "/slam_toolbox/reset")

                # 콜백에서 참조할 메시지 타입 보관
                self._NavGoal = NavigateToPose.Goal
                self._NavAction = NavigateToPose  # 액션 클라이언트 재생성용
                self._SaveMapReq = SaveMap.Request
                self._ResetReq = Reset.Request
                self._String = String
                self._Time = Time
                self._GoalStatus = GoalStatus

                # 센서 구독 (pinky_sensor_adc)
                self.create_subscription(Range, "/us_sensor/range", self._on_us_range, 10)
                self.create_subscription(UInt16MultiArray, "/ir_sensor/range", self._on_ir_range, 10)

                # 하드웨어 서비스 클라이언트 (LED/밝기/표정)
                self._set_led_client = self.create_client(SetLed, "/set_led")
                self._set_brightness_client = self.create_client(SetBrightness, "/set_brightness")
                self._set_emotion_client = self.create_client(EmotionSrv, "/set_emotion")
                self._SetLedReq = SetLed.Request
                self._SetBrightnessReq = SetBrightness.Request
                self._EmotionReq = EmotionSrv.Request

                # 부저 서비스 클라이언트
                self._play_buzzer_client = self.create_client(PlayBuzzer, "/play_buzzer")
                self._play_melody_client = self.create_client(PlayMelody, "/play_melody")
                self._stop_buzzer_client = self.create_client(StopBuzzer, "/stop_buzzer")
                self._PlayBuzzerReq = PlayBuzzer.Request
                self._PlayMelodyReq = PlayMelody.Request
                self._StopBuzzerReq = StopBuzzer.Request

                # LCD 텍스트 서비스 클라이언트
                self._lcd_text_client = self.create_client(LcdText, "/lcd_text")
                self._lcd_stop_client = self.create_client(LcdStop, "/lcd_stop")
                self._LcdTextReq = LcdText.Request
                self._LcdStopReq = LcdStop.Request

                self.get_logger().info(f"fastapi_ros_bridge 노드 시작됨 (/cmd_vel={self._cmd_vel_type})")

            # ── Nav 대시보드 콜백 ────────────────────────────────
            BASE_FRAME_CANDIDATES = ("base_link", "base_footprint", "base")

            def _on_battery(self, key: str, value: float) -> None:
                with _lock:
                    _state["battery"] = {**_state["battery"], key: value}

            def _on_amcl_pose(self, msg) -> None:
                """경량 모드의 pose 출처. `/amcl_pose` 는 **이미 map 프레임**이라 변환이 없다."""
                p = msg.pose.pose
                with _lock:
                    _state["tf_pose"] = {
                        "x": p.position.x,
                        "y": p.position.y,
                        "yaw": quat_to_yaw(p.orientation),
                    }
                if self._pose_frame_used != "amcl":
                    self._pose_frame_used = "amcl"
                    self.get_logger().info("[POSE] /amcl_pose 수신 시작.")

            def _update_pose_from_tf(self) -> None:
                if self._tf_buffer is None:
                    return          # 경량 모드 — `_on_amcl_pose` 가 대신한다
                last_err = None
                for base in self.BASE_FRAME_CANDIDATES:
                    try:
                        tr = self._tf_buffer.lookup_transform("map", base, self._Time())
                        t = tr.transform
                        with _lock:
                            _state["tf_pose"] = {
                                "x": t.translation.x,
                                "y": t.translation.y,
                                "yaw": quat_to_yaw(t.rotation),
                            }
                        if self._pose_frame_used != base:
                            self._pose_frame_used = base
                            self.get_logger().info(f"[POSE] map -> {base} TF 확인됨.")
                        return
                    except Exception as e:
                        last_err = e
                now = time.time()
                if now - self._last_tf_warn > 5.0:
                    self._last_tf_warn = now
                    self.get_logger().warn(
                        f"[POSE] map -> {self.BASE_FRAME_CANDIDATES} TF 못 찾음. "
                        f"(bringup/amcl/odom 확인) 마지막 에러: {last_err}")

            def _transform_origin_to_map(self, x, y, yaw, src_frame):
                """costmap origin(src_frame, 예: odom)을 map 프레임으로 변환."""
                if not src_frame or src_frame == "map":
                    return x, y, yaw
                try:
                    tr = self._tf_buffer.lookup_transform("map", src_frame, self._Time())
                    t = tr.transform
                    tyaw = quat_to_yaw(t.rotation)
                    c, s = math.cos(tyaw), math.sin(tyaw)
                    nx = t.translation.x + c * x - s * y
                    ny = t.translation.y + s * x + c * y
                    return nx, ny, yaw + tyaw
                except Exception:
                    return x, y, yaw

            def _costmap_to_dict(self, msg, to_map: bool):
                if not msg.data:
                    return None
                meta = msg.metadata
                ox = meta.origin.position.x
                oy = meta.origin.position.y
                oyaw = quat_to_yaw(meta.origin.orientation)
                if to_map:
                    src = msg.header.frame_id or "odom"
                    ox, oy, oyaw = self._transform_origin_to_map(ox, oy, oyaw, src)
                return {
                    "width": meta.size_x,
                    "height": meta.size_y,
                    "resolution": meta.resolution,
                    "origin": {"x": ox, "y": oy, "yaw": oyaw},
                    "data": list(msg.data),
                }

            def _on_local_costmap(self, msg) -> None:
                d = self._costmap_to_dict(msg, to_map=True)
                with _lock:
                    _state["local_costmap"] = d

            def _on_global_costmap(self, msg) -> None:
                d = self._costmap_to_dict(msg, to_map=False)
                with _lock:
                    _state["global_costmap"] = d

            # ── NavigateToPose 액션 ──────────────────────────────
            def _make_goal(self, x, y, yaw):
                goal = self._NavGoal()
                goal.pose.header.frame_id = "map"
                goal.pose.header.stamp = self.get_clock().now().to_msg()
                goal.pose.pose.position.x = x
                goal.pose.pose.position.y = y
                goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
                goal.pose.pose.orientation.w = math.cos(yaw / 2.0)
                return goal

            def _ensure_nav_action(self, timeout_sec: float) -> bool:
                """navigate_to_pose 액션서버 준비를 보장. nav2 가 재시작(주차 등)되면 액션
                클라이언트가 stale 해져 서버를 못 찾을 수 있다(주차 후 지도/구역 클릭 주행
                먹통의 원인). 1차 대기 실패 시 액션 클라이언트를 재생성해 강제 재탐색한다."""
                if self._nav_action.wait_for_server(timeout_sec=timeout_sec):
                    return True
                self.get_logger().warn("navigate_to_pose 재탐색 — 액션 클라이언트 재생성")
                try:
                    try:
                        self._nav_action.destroy()
                    except Exception:
                        pass
                    self._nav_action = ActionClient(self, self._NavAction, "navigate_to_pose")
                    return self._nav_action.wait_for_server(timeout_sec=max(timeout_sec, 6.0))
                except Exception as e:
                    self.get_logger().error(f"액션 클라이언트 재생성 실패: {e}")
                    return False

            def send_nav_goal(self, x, y, yaw) -> bool:
                """완료를 기다리지 않고 목표만 던진다(BT 의 `goal` 명령이 쓰는 경로).

                ⚠️ **응답 콜백을 반드시 달아야 한다.** 예전엔 `send_goal_async()` 의 future 를
                그냥 버렸다. 그러면 `_on_goal_response` 가 영영 안 불려 `_active_goal_handle`
                이 채워지지 않고, 결국 `cancel_active_goal()` 이 **어떤 주행도 취소하지
                못한다** — 배달·순회·복귀·길잡이 전부. 길잡이의 "사람을 놓치면 멈춘다" 가
                화면에만 뜨고 로봇은 계속 달리던 원인이 이것이다.
                (반면 블로킹 경로인 `nav_to()` 는 처음부터 콜백을 달고 있었다.)
                """
                target = (float(x), float(y), float(yaw))
                with self._nav_goal_lock:
                    inflight = self._nav_inflight_target
                    if inflight is not None:
                        # 같은 목표는 heartbeat 로 취급한다. 새 액션을 만들지 않는다.
                        if all(abs(a - b) < 1e-4 for a, b in zip(inflight, target)):
                            return True
                        # 다음 waypoint 는 현재 waypoint 완료 뒤에만 선점 없이 전달한다.
                        self._nav_pending_target = target
                        self.get_logger().info(
                            "목표 대기열: 현재 goal 완료 후 다음 goal 전송 "
                            f"({target[0]:.2f}, {target[1]:.2f}, {target[2]:.2f})")
                        return True

                if not self._ensure_nav_action(2.0):
                    self.get_logger().error("navigate_to_pose 액션서버 없음")
                    return False
                with self._nav_goal_lock:
                    self._nav_inflight_target = target
                gen = self._goals.begin()
                self._nav_action.send_goal_async(self._make_goal(*target)) \
                    .add_done_callback(lambda f, g=gen: self._on_goal_response(f, g))
                self.get_logger().info(
                    f"[WEB] send goal: x={target[0]:.2f}, y={target[1]:.2f}, yaw={target[2]:.2f}")
                return True

            def _dispatch_pending_nav_goal(self) -> None:
                """현재 goal 종료 뒤 대기 중인 최신 waypoint를 한 번만 전송한다."""
                with self._nav_goal_lock:
                    target = self._nav_pending_target
                    self._nav_pending_target = None
                    if target is None or self._nav_inflight_target is not None:
                        return
                    self._nav_inflight_target = target
                if not self._ensure_nav_action(2.0):
                    with self._nav_goal_lock:
                        self._nav_inflight_target = None
                    self.get_logger().error("대기 goal 전송 실패: navigate_to_pose 액션서버 없음")
                    return
                gen = self._goals.begin()
                self._nav_action.send_goal_async(self._make_goal(*target)) \
                    .add_done_callback(lambda f, g=gen: self._on_goal_response(f, g))
                self.get_logger().info(
                    f"[WEB] queued goal send: x={target[0]:.2f}, "
                    f"y={target[1]:.2f}, yaw={target[2]:.2f}")

            def _on_goal_response(self, future, generation=None):
                try:
                    gh = future.result()
                except Exception as e:      # noqa: BLE001 — 콜백 예외가 executor 를 죽인다
                    self.get_logger().error(f"goal 응답 처리 실패: {e}")
                    self._goal_result = False
                    self._goal_done.set()
                    return
                self._goals.on_response(gh, generation)
                if self._goals.active_handle is not gh:
                    # 거절됐거나, 낡은 세대이거나, 응답보다 취소가 먼저 왔다.
                    # 어느 쪽이든 이 목표는 진행하지 않는다.
                    if gh is None or not getattr(gh, "accepted", True):
                        with self._nav_goal_lock:
                            self._nav_inflight_target = None
                        self._goal_result = False
                        self._goal_done.set()
                        self._dispatch_pending_nav_goal()
                    return
                gh.get_result_async().add_done_callback(
                    lambda f, g=generation: self._on_goal_result(f, g))

            def _on_goal_result(self, future, generation=None):
                # ⚠️ 세대를 안 가리면 **이전 목표의 결과가 새 목표의 완료로 보고된다.**
                #    fire-and-forget 로 A 를 보낸 뒤 nav_to(B) 가 대기에 들어갔을 때
                #    A 의 결과가 도착하면, B 는 아직 출발도 안 했는데 A 의 성공/실패를
                #    자기 결과로 받는다 — 도착하지 않았는데 다음 단계로 넘어간다.
                if not self._goals.is_current(generation):
                    return
                try:
                    self._goal_result = (
                        future.result().status == self._GoalStatus.STATUS_SUCCEEDED)
                except Exception:      # noqa: BLE001 — 콜백 예외가 executor 를 죽인다
                    self._goal_result = False
                self._goals.clear()
                with self._nav_goal_lock:
                    self._nav_inflight_target = None
                self._goal_done.set()
                self._dispatch_pending_nav_goal()

            def cancel_active_goal(self) -> None:
                # 핸들이 아직 없으면 취소 의사만 남는다(응답 시점에 갚는다).
                # 그냥 돌아가면 잠시 뒤 핸들이 도착해 로봇이 계속 달린다.
                with self._nav_goal_lock:
                    self._nav_inflight_target = None
                    self._nav_pending_target = None
                self._goals.cancel()

            def nav_to(self, x, y, yaw, stop_event=None, wait_action_sec: float = None) -> bool:
                """완료까지 블로킹(미션 워커 스레드용).

                `wait_action_sec` 는 액션서버를 기다리는 시간이다. 기본 3초는 **콜드부팅에
                너무 짧다** — pi.sh 로 띄우면 nav2 창이 `/scan` 을 기다렸다가 라이프사이클을
                올리느라 30초 넘게 걸린다. 그 사이 미션이 시작되면 첫 지점에서 바로 실패했다
                (실측: 부팅 +30초에 "액션서버 없음" → 미션 스레드 종료 → 순찰이 A 에서 멈춤).
                미션 첫 지점은 넉넉히 기다리도록 호출측이 값을 준다.
                """
                if not self._ensure_nav_action(3.0 if wait_action_sec is None else wait_action_sec):
                    self.get_logger().error("navigate_to_pose 액션서버 없음")
                    return False
                self._goal_done.clear()
                self._goal_result = False
                # 블로킹 경로도 같은 추적기를 지난다. begin() 을 빼면 세대가 안 올라가서
                # 직전 목표의 응답이 이 목표의 것으로 오인된다.
                gen = self._goals.begin()
                self._nav_action.send_goal_async(
                    self._make_goal(x, y, yaw)
                ).add_done_callback(lambda f, g=gen: self._on_goal_response(f, g))
                while not self._goal_done.is_set():
                    if stop_event is not None and stop_event.is_set():
                        self.cancel_active_goal()
                        return False
                    self._goal_done.wait(timeout=0.2)
                return self._goal_result

            # ── slam_toolbox 서비스 ──────────────────────────────
            def slam_reset(self) -> bool:
                if not self._reset_client.wait_for_service(timeout_sec=1.0):
                    self.get_logger().error("/slam_toolbox/reset 서비스 없음")
                    return False
                self._reset_client.call_async(self._ResetReq())
                self.get_logger().info("[WEB] SLAM reset 요청")
                return True

            def slam_save_map(self, name: str) -> bool:
                if not self._save_map_client.wait_for_service(timeout_sec=1.0):
                    self.get_logger().error("/slam_toolbox/save_map 서비스 없음")
                    return False
                req = self._SaveMapReq()
                req.name = self._String(data=name)
                self._save_map_client.call_async(req)
                self.get_logger().info(f"[WEB] SLAM save_map 요청: name='{name}'")
                return True

            # [2026-07-29] _wanted_cmd_vel_type / _apply_cmd_vel_type / _sync_cmd_vel_pub /
            #   _on_cmd_vel_twist 제거 — 수동 주행 발행 경로 자체가 사라졌다
            #   (publish_cmd_vel 주석 참고: twist_mux 의 manual 입력 삭제).

            def _on_scan(self, msg: LaserScan) -> None:
                with _lock:
                    _state["scan"] = {
                        "angle_min": msg.angle_min,
                        "angle_max": msg.angle_max,
                        "angle_increment": msg.angle_increment,
                        "range_min": msg.range_min,
                        "range_max": msg.range_max,
                        "ranges": [
                            r if math.isfinite(r) and r > 0 else None
                            for r in msg.ranges
                        ],
                    }

            def _on_odom(self, msg: Odometry) -> None:
                q = msg.pose.pose.orientation
                yaw = math.atan2(
                    2.0 * (q.w * q.z + q.x * q.y),
                    1.0 - 2.0 * (q.y ** 2 + q.z ** 2),
                )
                with _lock:
                    _state["odom"] = {
                        "x": msg.pose.pose.position.x,
                        "y": msg.pose.pose.position.y,
                        "yaw": yaw,
                        "vx": msg.twist.twist.linear.x,
                        "wz": msg.twist.twist.angular.z,
                    }

            def _on_map(self, msg: OccupancyGrid) -> None:
                with _lock:
                    _state["map"] = {
                        "width": msg.info.width,
                        "height": msg.info.height,
                        "resolution": msg.info.resolution,
                        "origin_x": msg.info.origin.position.x,
                        "origin_y": msg.info.origin.position.y,
                        "data": list(msg.data),  # -1=unknown, 0=free, 100=occupied
                    }

            def _on_plan(self, msg: Path) -> None:
                poses = msg.poses
                step = max(1, len(poses) // 400)
                pts = [
                    {"x": p.pose.position.x, "y": p.pose.position.y}
                    for p in poses[::step]
                ]
                with _lock:
                    _state["plan"] = pts if pts else None

            # ── pinky_sensor_adc 콜백 ────────────────────────────
            _IR_THRESHOLD = 2048  # 12비트 ADC 중간값 — 실물 센서 기준으로 조정

            def _on_us_range(self, msg: "Range") -> None:
                with _lock:
                    _state["us_range"] = {
                        "sensor": "ultrasonic",
                        "distance_m": round(float(msg.range), 4),
                        "distance_cm": round(float(msg.range) * 100, 1),
                    }

            def _on_ir_range(self, msg) -> None:
                if len(msg.data) < 3:
                    return
                left = int(msg.data[0] > self._IR_THRESHOLD)
                center = int(msg.data[1] > self._IR_THRESHOLD)
                right = int(msg.data[2] > self._IR_THRESHOLD)
                with _lock:
                    _state["ir_range"] = {
                        "sensor": "ir",
                        "left": left,
                        "center": center,
                        "right": right,
                        "obstacle": bool(left or center or right),
                        "raw": list(msg.data[:3]),
                    }

            # ── LED / 밝기 / 표정 서비스 ─────────────────────────
            def call_set_led(self, command: str, r: int = 0, g: int = 0, b: int = 0, pixels: list | None = None) -> bool:
                if not self._set_led_client.service_is_ready():
                    self.get_logger().warn("/set_led 서비스 미준비")
                    return False
                req = self._SetLedReq()
                req.command = command
                req.r = r
                req.g = g
                req.b = b
                req.pixels = list(pixels or [])
                self._set_led_client.call_async(req)
                return True

            def call_set_brightness(self, brightness: int) -> bool:
                if not self._set_brightness_client.service_is_ready():
                    return False
                req = self._SetBrightnessReq()
                req.brightness = brightness
                self._set_brightness_client.call_async(req)
                return True

            def call_set_emotion(self, emotion: str) -> bool:
                if not self._set_emotion_client.service_is_ready():
                    self.get_logger().warn("/set_emotion 서비스 미준비")
                    return False
                req = self._EmotionReq()
                req.emotion = emotion
                self._set_emotion_client.call_async(req)
                return True

            # ── 부저 서비스 ──────────────────────────────────
            def call_play_buzzer(self, preset: str, count: int = 0, freq: int = 0, duration: float = 0.0) -> bool:
                if not self._play_buzzer_client.service_is_ready():
                    self.get_logger().warn("/play_buzzer 서비스 미준비")
                    return False
                req = self._PlayBuzzerReq()
                req.preset = preset
                req.count = count
                req.freq = freq
                req.duration = float(duration)
                self._play_buzzer_client.call_async(req)
                return True

            def call_play_melody(self, melody: str) -> bool:
                if not self._play_melody_client.service_is_ready():
                    self.get_logger().warn("/play_melody 서비스 미준비")
                    return False
                req = self._PlayMelodyReq()
                req.melody = melody
                self._play_melody_client.call_async(req)
                return True

            def call_stop_buzzer(self) -> bool:
                if not self._stop_buzzer_client.service_is_ready():
                    return False
                self._stop_buzzer_client.call_async(self._StopBuzzerReq())
                return True

            # ── LCD 텍스트 서비스 ─────────────────────────────
            def call_lcd_text(self, text: str, font_path: str = "", font_size: int = 24,
                              color: str = "#ffffff", bg_color: str = "#000000",
                              align: str = "center", scroll: bool = False, scroll_speed: int = 3) -> bool:
                if not self._lcd_text_client.service_is_ready():
                    self.get_logger().warn("/lcd_text 서비스 미준비")
                    return False
                req = self._LcdTextReq()
                req.text = text
                req.font_path = font_path
                req.font_size = font_size
                req.color = color
                req.bg_color = bg_color
                req.align = align
                req.scroll = scroll
                req.scroll_speed = scroll_speed
                self._lcd_text_client.call_async(req)
                return True

            def call_lcd_stop(self) -> bool:
                if not self._lcd_stop_client.service_is_ready():
                    return False
                self._lcd_stop_client.call_async(self._LcdStopReq())
                return True

        node = BridgeNode()
        _node = node
        with _lock:
            _state["ros_active"] = True

        rclpy.spin(node)

    except Exception as e:
        print(f"[ros_bridge] ROS2 브리지 비활성화: {e}")
        with _lock:
            _state["ros_active"] = False
    finally:
        _node = None
        with _lock:
            _state["ros_active"] = False


def call_set_led(command: str, r: int = 0, g: int = 0, b: int = 0, pixels: list | None = None) -> bool:
    """LED 색상/픽셀 설정 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_set_led(command, r, g, b, pixels)
    except Exception:
        return False


def call_set_brightness(brightness: int) -> bool:
    """LED 밝기 설정 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_set_brightness(brightness)
    except Exception:
        return False


def call_set_emotion(emotion: str) -> bool:
    """LCD 표정 설정 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_set_emotion(emotion)
    except Exception:
        return False


def call_play_buzzer(preset: str, count: int = 0, freq: int = 0, duration: float = 0.0) -> bool:
    """단음 beep — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_play_buzzer(preset, count, freq, duration)
    except Exception:
        return False


def call_play_melody(melody: str) -> bool:
    """멜로디 재생 시작 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_play_melody(melody)
    except Exception:
        return False


def call_stop_buzzer() -> bool:
    """부저/멜로디 중지 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_stop_buzzer()
    except Exception:
        return False


def call_lcd_text(text: str, font_path: str = "", font_size: int = 24,
                  color: str = "#ffffff", bg_color: str = "#000000",
                  align: str = "center", scroll: bool = False, scroll_speed: int = 3) -> bool:
    """LCD 텍스트 표시 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_lcd_text(text, font_path, font_size, color, bg_color, align, scroll, scroll_speed)
    except Exception:
        return False


def call_lcd_stop() -> bool:
    """LCD 중지 — fire-and-forget."""
    node = _node
    if node is None:
        return False
    try:
        return node.call_lcd_stop()
    except Exception:
        return False


def start() -> None:
    """FastAPI 시작 시 한 번 호출한다."""
    t = threading.Thread(target=_bridge_thread, daemon=True, name="ros-bridge")
    t.start()
