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


def get_cmd_vel_info() -> dict[str, Any]:
    """현재 타겟/발행 타입/수신측(구독자) 정보."""
    info: dict[str, Any] = {
        "target": _desired_target,
        "cmd_vel_type": None,
        "subscriber_count": 0,
        "subscriber_types": [],
    }
    node = _node
    if node is None:
        return info
    try:
        info["cmd_vel_type"] = getattr(node, "_cmd_vel_type", None)
        subs = [
            e for e in node.get_subscriptions_info_by_topic("/cmd_vel")
            if e.node_name != node.get_name()
        ]
        info["subscriber_count"] = len(subs)
        info["subscriber_types"] = sorted({e.topic_type for e in subs})
    except Exception:
        pass
    return info


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
    """rclpy로 /cmd_vel 발행. 실패 시 False 반환."""
    global _node
    if _node is None:
        return False
    try:
        from geometry_msgs.msg import Twist, TwistStamped
        twist = Twist()
        twist.linear.x = float(linear)
        twist.angular.z = float(angular)

        stamped = TwistStamped()
        stamped.header.stamp = _node.get_clock().now().to_msg()
        stamped.header.frame_id = "base_link"
        stamped.twist = twist

        if _node._cmd_vel_type == "twist":
            _node._cmd_pub.publish(twist)
        else:
            _node._cmd_pub.publish(stamped)
        return True
    except Exception:
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
                self._apply_cmd_vel_type(self._wanted_cmd_vel_type())
                self.create_timer(0.5, self._sync_cmd_vel_pub)
                self._nav_goal_pub = self.create_publisher(PoseStamped, "/goal_pose", 10)
                self._initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
                self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
                self.create_subscription(Odometry, "/odom", self._on_odom, 10)
                self.create_subscription(OccupancyGrid, "/map", self._on_map, QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL, history=QoSHistoryPolicy.KEEP_LAST))
                self.create_subscription(Path, "/plan", self._on_plan, 10)
                self.create_subscription(Twist, "/cmd_vel_raw", self._on_cmd_vel_twist, 10)

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

                # TF: map -> base_link (0.1초마다 pose 갱신)
                self._tf_buffer = Buffer()
                self._tf_listener = TransformListener(self._tf_buffer, self, spin_thread=False)
                self._pose_frame_used = None
                self._last_tf_warn = 0.0
                self.create_timer(0.1, self._update_pose_from_tf)

                # NavigateToPose 액션 클라이언트 + 완료대기 상태
                self._nav_action = ActionClient(self, NavigateToPose, "navigate_to_pose")
                self._goal_done = threading.Event()
                self._goal_result = False
                self._active_goal_handle = None

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

                self.get_logger().info(f"fastapi_ros_bridge 노드 시작됨 (/cmd_vel={self._cmd_vel_type})")

            # ── Nav 대시보드 콜백 ────────────────────────────────
            BASE_FRAME_CANDIDATES = ("base_link", "base_footprint", "base")

            def _on_battery(self, key: str, value: float) -> None:
                with _lock:
                    _state["battery"] = {**_state["battery"], key: value}

            def _update_pose_from_tf(self) -> None:
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
                if not self._ensure_nav_action(2.0):
                    self.get_logger().error("navigate_to_pose 액션서버 없음")
                    return False
                self._nav_action.send_goal_async(self._make_goal(x, y, yaw))
                self.get_logger().info(f"[WEB] send goal: x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}")
                return True

            def _on_goal_response(self, future):
                gh = future.result()
                if not gh.accepted:
                    self._goal_result = False
                    self._goal_done.set()
                    return
                self._active_goal_handle = gh
                gh.get_result_async().add_done_callback(self._on_goal_result)

            def _on_goal_result(self, future):
                try:
                    self._goal_result = (
                        future.result().status == self._GoalStatus.STATUS_SUCCEEDED)
                except Exception:
                    self._goal_result = False
                self._active_goal_handle = None
                self._goal_done.set()

            def cancel_active_goal(self) -> None:
                gh = self._active_goal_handle
                if gh is not None:
                    try:
                        gh.cancel_goal_async()
                    except Exception:
                        pass

            def nav_to(self, x, y, yaw, stop_event=None) -> bool:
                """완료까지 블로킹(미션 워커 스레드용)."""
                if not self._ensure_nav_action(3.0):
                    self.get_logger().error("navigate_to_pose 액션서버 없음")
                    return False
                self._goal_done.clear()
                self._goal_result = False
                self._nav_action.send_goal_async(
                    self._make_goal(x, y, yaw)
                ).add_done_callback(self._on_goal_response)
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

            def _wanted_cmd_vel_type(self) -> str:
                try:
                    sub_types = {
                        e.topic_type for e in self.get_subscriptions_info_by_topic("/cmd_vel")
                        if e.node_name != self.get_name()
                    }
                except Exception:
                    sub_types = set()
                has_stamped = any("TwistStamped" in t for t in sub_types)
                has_twist = any(t.rsplit("/", 1)[-1] == "Twist" for t in sub_types)

                if has_twist:
                    return "twist"
                if has_stamped:
                    return "twist_stamped"
                return "twist"

            def _apply_cmd_vel_type(self, wanted: str) -> None:
                if wanted == self._cmd_vel_type and self._cmd_pub is not None:
                    return
                if self._cmd_pub is not None:
                    self.destroy_publisher(self._cmd_pub)
                if wanted == "twist":
                    self._cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
                else:
                    self._cmd_pub = self.create_publisher(TwistStamped, "/cmd_vel", 10)
                self._cmd_vel_type = wanted
                self.get_logger().info(
                    f"/cmd_vel 퍼블리셔 타입 = {wanted} (target={_desired_target})"
                )

            def _sync_cmd_vel_pub(self) -> None:
                self._apply_cmd_vel_type(self._wanted_cmd_vel_type())

            def _on_cmd_vel_twist(self, msg: Twist) -> None:
                out = TwistStamped()
                out.header.stamp = self.get_clock().now().to_msg()
                out.header.frame_id = "base_link"
                out.twist = msg
                if self._cmd_vel_type == "twist":
                    self._cmd_pub.publish(msg)
                else:
                    self._cmd_pub.publish(out)

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


def start() -> None:
    """FastAPI 시작 시 한 번 호출한다."""
    t = threading.Thread(target=_bridge_thread, daemon=True, name="ros-bridge")
    t.start()
