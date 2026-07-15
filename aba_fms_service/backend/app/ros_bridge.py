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


# ── ROS2 브리지 스레드 ──────────────────────────────────────
def _bridge_thread() -> None:
    global _node
    try:
        import rclpy
        from rclpy.node import Node
        from geometry_msgs.msg import Twist, TwistStamped
        from nav_msgs.msg import OccupancyGrid, Odometry, Path
        from sensor_msgs.msg import LaserScan

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
                self.create_subscription(OccupancyGrid, "/map", self._on_map, 1)
                self.create_subscription(Path, "/plan", self._on_plan, 10)
                self.create_subscription(Twist, "/cmd_vel_raw", self._on_cmd_vel_twist, 10)
                self.get_logger().info(f"fastapi_ros_bridge 노드 시작됨 (/cmd_vel={self._cmd_vel_type})")

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

        node = BridgeNode()
        _node = node
        with _lock:
            _state["ros_active"] = True

        # Nav2/SLAM 관제 노드(NavBridge)도 같은 rclpy 컨텍스트/executor 에 올린다.
        # (별도 rclpy.init() 중복 호출 방지)
        from rclpy.executors import MultiThreadedExecutor
        executor = MultiThreadedExecutor()
        executor.add_node(node)
        try:
            from app import nav_bridge
            nav_node = nav_bridge.build_node()
            executor.add_node(nav_node)
        except Exception as e:
            print(f"[ros_bridge] nav_bridge 비활성화: {e}")

        executor.spin()

    except Exception as e:
        print(f"[ros_bridge] ROS2 브리지 비활성화: {e}")
        with _lock:
            _state["ros_active"] = False
    finally:
        _node = None
        try:
            from app import nav_bridge
            nav_bridge.set_node(None)
        except Exception:
            pass
        with _lock:
            _state["ros_active"] = False


def start() -> None:
    """FastAPI 시작 시 한 번 호출한다."""
    t = threading.Thread(target=_bridge_thread, daemon=True, name="ros-bridge")
    t.start()
