"""
Nav2/SLAM 브리지 — 옛 Flask ``nav2_web_server.py`` 의 ``Nav2WebBridge`` 를 백엔드로 포팅.

ros_bridge 의 MultiThreadedExecutor 에 함께 올라가 단일 rclpy 컨텍스트에서 스핀된다.
FastAPI 라우터(app/routers/nav.py)가 ``get_node()`` 로 접근한다.

이 모듈의 최상위는 rclpy 의존성이 없어야 한다(ROS 미설치 환경에서도 import 가능).
실제 ROS 노드 클래스는 ``build_node()`` 안에서 lazy 하게 정의한다.
"""
from __future__ import annotations

import math
import os
import threading
import time


############################################################
# 명명 위치(A~E) 저장소
#   - rebuild(install 덮어쓰기)에도 유지되도록 홈 디렉토리에 저장
#   - PINKY_LOCATIONS 환경변수로 경로 변경 가능
############################################################
LOC_FILE = os.environ.get(
    "PINKY_LOCATIONS",
    os.path.expanduser("~/.pinky/locations.yaml"),
)
_loc_lock = threading.Lock()

# /api/state 의 scan 다운샘플 상한. sllidar DenseBoost 는 한 바퀴 수천 점이라
# 그대로 실으면 페이로드가 커진다(맵이 2.14×1.32m 라 360점이면 이미 과촘촘).
SCAN_MAX_POINTS = 360


def load_locations() -> dict:
    """{'A': {'x':..,'y':..,'yaw':..}, ...} 형태 dict 반환."""
    import yaml

    try:
        with open(LOC_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[nav_bridge] locations load error: {e}")
        return {}


def save_locations(locs: dict) -> None:
    import yaml

    os.makedirs(os.path.dirname(LOC_FILE), exist_ok=True)
    with open(LOC_FILE, "w") as f:
        yaml.safe_dump(locs, f, default_flow_style=False,
                       sort_keys=True, allow_unicode=True)


def locations_lock() -> threading.Lock:
    return _loc_lock


def quat_to_yaw(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


############################################################
# 노드 싱글톤 (ros_bridge 스레드가 생성/등록)
############################################################
_node = None  # NavBridge 인스턴스 (없으면 None)


def set_node(node) -> None:
    global _node
    _node = node


def get_node():
    return _node


def build_node():
    """ros_bridge 스레드에서 rclpy.init() 이후 호출 — NavBridge 생성/등록.

    rclpy 의존 import 와 노드 클래스 정의를 여기서 수행해
    모듈 최상위는 ROS 미설치 환경에서도 import 가능하게 한다.
    """
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from rclpy.time import Time
    from rclpy.qos import (
        QoSProfile,
        QoSDurabilityPolicy,
        QoSReliabilityPolicy,
        QoSHistoryPolicy,
    )

    from nav_msgs.msg import OccupancyGrid, Path
    from nav2_msgs.action import NavigateToPose
    from nav2_msgs.msg import Costmap
    from sensor_msgs.msg import LaserScan
    from tf2_ros import Buffer, TransformListener
    from slam_toolbox.srv import SaveMap, Reset
    from std_msgs.msg import String, Float32
    from action_msgs.msg import GoalStatus

    class NavBridge(Node):
        BASE_FRAME_CANDIDATES = ["base_link", "base_footprint", "base"]

        def __init__(self):
            super().__init__("nav2_web_bridge_tf")

            # ROS 데이터
            self.map_msg = None
            self.path_msg = None
            self.local_costmap_msg = None
            self.global_costmap_msg = None

            # TF 기반 pose (x,y,yaw)
            self.tf_pose = None

            self.lock = threading.Lock()

            # ---- map: TRANSIENT_LOCAL QoS (latched) ----
            map_qos = QoSProfile(
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=1,
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )
            self.create_subscription(OccupancyGrid, "map", self.map_callback, map_qos)

            # ---- path: 기본 QoS ----
            self.create_subscription(Path, "plan", self.path_callback, 10)

            # ---- laser scan: 원시 라이다. costmap(=inflation 적용본)과 구분해서 보기 위함.
            # 센서 데이터라 BEST_EFFORT QoS 여야 sllidar 퍼블리셔와 매칭된다.
            self.scan_msg = None
            scan_qos = QoSProfile(
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=1,
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
            )
            self.create_subscription(LaserScan, "scan", self.scan_callback, scan_qos)

            # ---- local costmap: costmap / costmap_raw 둘 다 시도 ----
            self.local_costmap_seen = False
            self.create_subscription(Costmap, "local_costmap/costmap", self.local_costmap_callback, 10)
            self.create_subscription(Costmap, "local_costmap/costmap_raw", self.local_costmap_callback, 10)

            # ---- global costmap: costmap / costmap_raw 둘 다 시도 ----
            self.global_costmap_seen = False
            self.create_subscription(Costmap, "global_costmap/costmap", self.global_costmap_callback, 10)
            self.create_subscription(Costmap, "global_costmap/costmap_raw", self.global_costmap_callback, 10)

            # ---- TF2: map -> base_link ----
            self.tf_buffer = Buffer()
            self.tf_listener = TransformListener(self.tf_buffer, self, spin_thread=False)
            self.create_timer(0.1, self.update_pose_from_tf)

            # Nav2 액션 클라이언트
            self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")

            # ---- 배터리 구독 (대시보드용) ----
            self.battery_percent = None
            self.battery_voltage = None
            self.create_subscription(Float32, "/battery/percent",
                                     lambda m: setattr(self, "battery_percent", m.data), 10)
            self.create_subscription(Float32, "/battery/voltage",
                                     lambda m: setattr(self, "battery_voltage", m.data), 10)

            # ---- 미션(순찰/경유) 상태 ----
            self.mission_lock = threading.Lock()
            self.mission_thread = None
            self.mission_stop = threading.Event()
            self.mission_status = "idle"
            self.mission_current = None
            self.mission_names = []
            self.mission_loop = False
            self._goal_done = threading.Event()
            self._goal_result = False
            self._active_goal_handle = None

            # ---- 스케줄 순찰 ----
            self.schedule_thread = None
            self.schedule_stop = threading.Event()
            self.schedule_minutes = 0

            # ---- SLAM Toolbox 서비스 클라이언트 ----
            self.save_map_client = self.create_client(SaveMap, "/slam_toolbox/save_map")
            self.reset_client = self.create_client(Reset, "/slam_toolbox/reset")

            self.get_logger().info("NavBridge (TF-based + SLAM) started.")

        # ---------------- 콜백 ----------------
        def map_callback(self, msg):
            with self.lock:
                self.map_msg = msg

        def path_callback(self, msg):
            with self.lock:
                self.path_msg = msg

        def scan_callback(self, msg):
            with self.lock:
                self.scan_msg = msg

        def local_costmap_callback(self, msg):
            with self.lock:
                self.local_costmap_msg = msg
            if not self.local_costmap_seen:
                self.local_costmap_seen = True
                self.get_logger().info(
                    f"Received first LOCAL costmap: "
                    f"size=({msg.metadata.size_x}, {msg.metadata.size_y}), "
                    f"res={msg.metadata.resolution}"
                )

        def global_costmap_callback(self, msg):
            with self.lock:
                self.global_costmap_msg = msg
            if not self.global_costmap_seen:
                self.global_costmap_seen = True
                self.get_logger().info(
                    f"Received first GLOBAL costmap: "
                    f"size=({msg.metadata.size_x}, {msg.metadata.size_y}), "
                    f"res={msg.metadata.resolution}"
                )

        # ---------------- TF에서 pose 업데이트 ----------------
        def update_pose_from_tf(self):
            last_err = None
            for base in self.BASE_FRAME_CANDIDATES:
                try:
                    trans = self.tf_buffer.lookup_transform("map", base, Time())
                    t = trans.transform
                    pose = (t.translation.x, t.translation.y, quat_to_yaw(t.rotation))
                    with self.lock:
                        self.tf_pose = pose
                    if getattr(self, "_pose_frame_used", None) != base:
                        self._pose_frame_used = base
                        self.get_logger().info(
                            f"[POSE] map -> {base} TF 확인됨. 로봇 위치 publish 시작."
                        )
                    return
                except Exception as e:
                    last_err = e
                    continue

            now = time.time()
            if now - getattr(self, "_last_tf_warn", 0.0) > 5.0:
                self._last_tf_warn = now
                self.get_logger().warn(
                    f"[POSE] map -> {self.BASE_FRAME_CANDIDATES} TF를 못 찾음. "
                    f"(로봇 bringup/amcl/odom 확인 필요) 마지막 에러: {last_err}"
                )

        # ---------------- costmap origin 프레임 변환 ----------------
        def transform_origin_to_map(self, x, y, yaw, src_frame):
            if not src_frame or src_frame == "map":
                return x, y, yaw
            try:
                tr = self.tf_buffer.lookup_transform("map", src_frame, Time())
                t = tr.transform
                tx, ty = t.translation.x, t.translation.y
                tyaw = quat_to_yaw(t.rotation)
                c, s = math.cos(tyaw), math.sin(tyaw)
                nx = tx + c * x - s * y
                ny = ty + s * x + c * y
                return nx, ny, yaw + tyaw
            except Exception:
                return x, y, yaw

        # ---------------- JSON 스냅샷 ----------------
        def get_state_snapshot(self):
            with self.lock:
                map_msg = self.map_msg
                path_msg = self.path_msg
                local_costmap_msg = self.local_costmap_msg
                global_costmap_msg = self.global_costmap_msg
                tf_pose = self.tf_pose
                scan_msg = self.scan_msg

            map_json = None
            if map_msg is not None:
                info = map_msg.info
                map_json = {
                    "width": info.width,
                    "height": info.height,
                    "resolution": info.resolution,
                    "origin": {
                        "x": info.origin.position.x,
                        "y": info.origin.position.y,
                        "yaw": quat_to_yaw(info.origin.orientation),
                    },
                    "data": list(map_msg.data),
                }

            pose_json = None
            if tf_pose is not None:
                x, y, yaw = tf_pose
                pose_json = {"x": x, "y": y, "yaw": yaw}

            path_json = []
            if path_msg is not None:
                for ps in path_msg.poses:
                    path_json.append({
                        "x": ps.pose.position.x,
                        "y": ps.pose.position.y,
                    })

            local_costmap_json = None
            if local_costmap_msg is not None and len(local_costmap_msg.data) > 0:
                meta = local_costmap_msg.metadata
                src_frame = local_costmap_msg.header.frame_id or "odom"
                ox, oy, oyaw = self.transform_origin_to_map(
                    meta.origin.position.x,
                    meta.origin.position.y,
                    quat_to_yaw(meta.origin.orientation),
                    src_frame,
                )
                local_costmap_json = {
                    "width": meta.size_x,
                    "height": meta.size_y,
                    "resolution": meta.resolution,
                    "origin": {"x": ox, "y": oy, "yaw": oyaw},
                    "data": list(local_costmap_msg.data),
                }

            global_costmap_json = None
            if global_costmap_msg is not None and len(global_costmap_msg.data) > 0:
                meta = global_costmap_msg.metadata
                global_costmap_json = {
                    "width": meta.size_x,
                    "height": meta.size_y,
                    "resolution": meta.resolution,
                    "origin": {
                        "x": meta.origin.position.x,
                        "y": meta.origin.position.y,
                        "yaw": quat_to_yaw(meta.origin.orientation),
                    },
                    "data": list(global_costmap_msg.data),
                }

            # 원시 라이다 → 맵 좌표 점 목록. costmap 은 inflation 이 씌워진 결과라
            # 실제 센서가 뭘 보는지 구분되지 않으므로 이 레이어를 따로 제공한다.
            # 스캔이 맵 벽선에 얹히지 않으면 AMCL 위치추정이 틀어진 것.
            scan_json = None
            if scan_msg is not None and len(scan_msg.ranges) > 0:
                src_frame = scan_msg.header.frame_id or "base_scan"
                ox, oy, oyaw = self.transform_origin_to_map(0.0, 0.0, 0.0, src_frame)
                c, s = math.cos(oyaw), math.sin(oyaw)
                rmin = scan_msg.range_min
                rmax = scan_msg.range_max
                n = len(scan_msg.ranges)
                # 페이로드 억제: 최대 SCAN_MAX_POINTS 개만 (맵이 2m 대라 360점이면 충분히 촘촘).
                step = max(1, math.ceil(n / SCAN_MAX_POINTS))
                pts = []
                for i in range(0, n, step):
                    r = scan_msg.ranges[i]
                    if not math.isfinite(r) or r < rmin or r > rmax:
                        continue  # inf/nan = 미측정
                    a = scan_msg.angle_min + i * scan_msg.angle_increment
                    lx, ly = r * math.cos(a), r * math.sin(a)
                    pts.append([
                        round(ox + c * lx - s * ly, 3),
                        round(oy + s * lx + c * ly, 3),
                    ])
                scan_json = {"frame": src_frame, "points": pts}

            with self.mission_lock:
                mission_json = {
                    "status": self.mission_status,
                    "current": self.mission_current,
                    "names": list(self.mission_names),
                    "loop": self.mission_loop,
                    "schedule_minutes": self.schedule_minutes,
                }

            return {
                "map": map_json,
                "pose": pose_json,
                "path": path_json,
                "scan": scan_json,
                "local_costmap": local_costmap_json,
                "global_costmap": global_costmap_json,
                "battery": {
                    "percent": self.battery_percent,
                    "voltage": self.battery_voltage,
                },
                "mission": mission_json,
            }

        # ---------------- 현재 위치 (TF 기반) ----------------
        def get_current_pose(self):
            with self.lock:
                return self.tf_pose

        # ---------------- Goal 전송 ----------------
        def send_goal(self, x, y, yaw):
            if not self.nav_client.wait_for_server(timeout_sec=1.0):
                self.get_logger().error("navigate_to_pose Action Server not available.")
                return False

            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x = float(x)
            goal.pose.pose.position.y = float(y)
            goal.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
            goal.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)

            self.get_logger().info(f"[WEB] send goal: x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}")
            self.nav_client.send_goal_async(goal)
            return True

        # ---------------- 미션(순찰/경유) 엔진 ----------------
        def _set_mission(self, status, current):
            with self.mission_lock:
                self.mission_status = status
                self.mission_current = current

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
                self._goal_result = (future.result().status == GoalStatus.STATUS_SUCCEEDED)
            except Exception:
                self._goal_result = False
            self._active_goal_handle = None
            self._goal_done.set()

        def _cancel_active(self):
            gh = self._active_goal_handle
            if gh is not None:
                try:
                    gh.cancel_goal_async()
                except Exception:
                    pass

        def _nav_to(self, x, y, yaw):
            """한 목표로 주행하고 완료까지 대기. 성공 True / 실패·중단 False."""
            if not self.nav_client.wait_for_server(timeout_sec=3.0):
                self.get_logger().error("navigate_to_pose 액션서버 없음")
                return False
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x = float(x)
            goal.pose.pose.position.y = float(y)
            goal.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
            goal.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)

            self._goal_done.clear()
            self._goal_result = False
            self.nav_client.send_goal_async(goal).add_done_callback(self._on_goal_response)

            while not self._goal_done.is_set():
                if self.mission_stop.is_set():
                    self._cancel_active()
                    return False
                self._goal_done.wait(timeout=0.2)
            return self._goal_result

        def _mission_worker(self, names, loop):
            self._set_mission("running", None)
            while not self.mission_stop.is_set():
                for nm in names:
                    if self.mission_stop.is_set():
                        break
                    locs = load_locations()
                    if nm not in locs:
                        continue
                    p = locs[nm]
                    self._set_mission("running", nm)
                    self.get_logger().info(f"[MISSION] -> {nm}")
                    ok = self._nav_to(float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
                    if self.mission_stop.is_set():
                        break
                    if not ok:
                        self.get_logger().warn(f"[MISSION] '{nm}' 주행 실패 → 미션 중단")
                        self._set_mission("failed", nm)
                        return
                if not loop:
                    break
            self._set_mission("stopped" if self.mission_stop.is_set() else "done", None)

        def start_mission(self, names, loop):
            self.stop_mission()
            names = [str(n).strip() for n in names if str(n).strip()]
            if not names:
                return False
            with self.mission_lock:
                self.mission_names = names
                self.mission_loop = bool(loop)
            self.mission_stop.clear()
            self.mission_thread = threading.Thread(
                target=self._mission_worker, args=(names, bool(loop)), daemon=True)
            self.mission_thread.start()
            return True

        def stop_mission(self):
            self.mission_stop.set()
            self._cancel_active()
            t = self.mission_thread
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
            self.mission_thread = None

        def go_home(self):
            """'HOME' 구역이 등록돼 있으면 그곳으로, 없으면 맵 원점(0,0)으로."""
            self.stop_mission()
            locs = load_locations()
            if "HOME" in locs:
                p = locs["HOME"]
                return self.send_goal(float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
            return self.send_goal(0.0, 0.0, 0.0)

        # ---------------- 스케줄 순찰 ----------------
        def _schedule_worker(self, minutes, names, loop):
            interval = max(1, int(minutes)) * 60
            while not self.schedule_stop.is_set():
                if self.schedule_stop.wait(timeout=interval):
                    break
                self.get_logger().info(f"[SCHEDULE] {minutes}분 주기 순찰 시작")
                self.start_mission(names, loop)

        def start_schedule(self, minutes, names, loop):
            self.stop_schedule()
            names = [str(n).strip() for n in names if str(n).strip()]
            if not names or int(minutes) < 1:
                return False
            self.schedule_minutes = int(minutes)
            self.schedule_stop.clear()
            self.schedule_thread = threading.Thread(
                target=self._schedule_worker, args=(int(minutes), names, bool(loop)), daemon=True)
            self.schedule_thread.start()
            return True

        def stop_schedule(self):
            self.schedule_stop.set()
            t = self.schedule_thread
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
            self.schedule_thread = None
            self.schedule_minutes = 0

        # ---------------- SLAM Toolbox 제어 ----------------
        def slam_reset(self) -> bool:
            if not self.reset_client.wait_for_service(timeout_sec=1.0):
                self.get_logger().error("/slam_toolbox/reset service not available.")
                return False
            req = Reset.Request()
            self.reset_client.call_async(req)
            self.get_logger().info("[WEB] Requested SLAM reset.")
            return True

        def slam_save_map(self, name: str) -> bool:
            if not self.save_map_client.wait_for_service(timeout_sec=1.0):
                self.get_logger().error("/slam_toolbox/save_map service not available.")
                return False
            req = SaveMap.Request()
            req.name = String(data=name)
            self.save_map_client.call_async(req)
            self.get_logger().info(f"[WEB] Requested SLAM save_map: name='{name}'")
            return True

    node = NavBridge()
    set_node(node)
    return node
