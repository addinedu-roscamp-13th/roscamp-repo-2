#!/usr/bin/env python3
import threading
import time
import math
import os

import yaml

from flask import Flask, jsonify, request, send_from_directory

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.time import Time

from nav_msgs.msg import OccupancyGrid, Path
from nav2_msgs.action import NavigateToPose
from nav2_msgs.msg import Costmap  # global/local costmap

from rclpy.qos import (
    QoSProfile,
    QoSDurabilityPolicy,
    QoSReliabilityPolicy,
    QoSHistoryPolicy,
)

# TF2
from tf2_ros import Buffer, TransformListener

# SLAM Toolbox services
from slam_toolbox.srv import SaveMap, Reset
from std_msgs.msg import String, Float32
from action_msgs.msg import GoalStatus


############################################################
# Flask 설정
############################################################

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=BASE_DIR,   # 같은 폴더의 index.html 서빙
    static_url_path=""
)

ros_node = None   # 전역 ROS 노드 포인터


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


def load_locations():
    """{'A': {'x':..,'y':..,'yaw':..}, ...} 형태 dict 반환."""
    try:
        with open(LOC_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[locations] load error: {e}")
        return {}


def save_locations(locs):
    os.makedirs(os.path.dirname(LOC_FILE), exist_ok=True)
    with open(LOC_FILE, "w") as f:
        yaml.safe_dump(locs, f, default_flow_style=False,
                       sort_keys=True, allow_unicode=True)


############################################################
# 유틸: Quaternion → yaw
############################################################
def quat_to_yaw(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


############################################################
# ROS2 노드 (Flask 브리지)
############################################################
class Nav2WebBridge(Node):
    def __init__(self):
        super().__init__("nav2_web_bridge_tf")
        
        self.declare_parameter("ip", "0.0.0.0")   # 모든 인터페이스에 바인딩 → 192.168.0.28 등 어디서든 접속
        self.declare_parameter("port", 8080)

        # ROS 데이터
        self.map_msg = None
        self.path_msg = None
        self.local_costmap_msg = None
        self.global_costmap_msg = None

        # TF 기반 pose (x,y,yaw)
        self.tf_pose = None  # (x, y, yaw)

        self.lock = threading.Lock()

        # ---- map: TRANSIENT_LOCAL QoS (latched) ----
        map_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(
            OccupancyGrid,
            "map",              # 필요시 실제 토픽 이름으로 수정
            self.map_callback,
            map_qos,
        )

        # ---- path: 기본 QoS ----
        self.create_subscription(
            Path,
            "plan",
            self.path_callback,
            10,
        )

        # ---- local costmap: costmap / costmap_raw 둘 다 시도 ----
        self.local_costmap_seen = False
        self.create_subscription(
            Costmap,
            "local_costmap/costmap",
            self.local_costmap_callback,
            10,
        )
        self.create_subscription(
            Costmap,
            "local_costmap/costmap_raw",
            self.local_costmap_callback,
            10,
        )

        # ---- global costmap: costmap / costmap_raw 둘 다 시도 ----
        self.global_costmap_seen = False
        self.create_subscription(
            Costmap,
            "global_costmap/costmap",
            self.global_costmap_callback,
            10,
        )
        self.create_subscription(
            Costmap,
            "global_costmap/costmap_raw",
            self.global_costmap_callback,
            10,
        )

        # ---- TF2: map -> base_link ----
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self, spin_thread=False)

        # 주기적으로 TF에서 pose 업데이트
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
        self.mission_status = "idle"     # idle/running/done/failed/stopped
        self.mission_current = None      # 현재 향하는 구역 이름
        self.mission_names = []          # 미션 구역 순서
        self.mission_loop = False
        self._goal_done = threading.Event()
        self._goal_result = False
        self._active_goal_handle = None

        # ---- 스케줄 순찰 ----
        self.schedule_thread = None
        self.schedule_stop = threading.Event()
        self.schedule_minutes = 0

        # ---- SLAM Toolbox 서비스 클라이언트 ----
        # /slam_toolbox/save_map : slam_toolbox/srv/SaveMap
        # /slam_toolbox/reset    : slam_toolbox/srv/Reset
        self.save_map_client = self.create_client(SaveMap, "/slam_toolbox/save_map")
        self.reset_client = self.create_client(Reset, "/slam_toolbox/reset")

        self.get_logger().info("Nav2WebBridge (TF-based + SLAM) started.")

    # ---------------- 콜백 ----------------
    def map_callback(self, msg):
        with self.lock:
            self.map_msg = msg

    def path_callback(self, msg):
        with self.lock:
            self.path_msg = msg

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
    # 로봇 base 프레임 후보 (위에서부터 차례로 시도)
    BASE_FRAME_CANDIDATES = ["base_link", "base_footprint", "base"]

    def update_pose_from_tf(self):
        """
        map -> (base_link | base_footprint | base) TF를 읽어 (x,y,yaw)로 저장.
        라이브 TF 트리에 어떤 base 프레임이 있든 자동으로 잡는다.
        """
        last_err = None
        for base in self.BASE_FRAME_CANDIDATES:
            try:
                trans = self.tf_buffer.lookup_transform("map", base, Time())
                t = trans.transform
                pose = (t.translation.x, t.translation.y, quat_to_yaw(t.rotation))

                with self.lock:
                    self.tf_pose = pose

                # 처음 성공했을 때 어떤 프레임을 쓰는지 한 번 로그
                if getattr(self, "_pose_frame_used", None) != base:
                    self._pose_frame_used = base
                    self.get_logger().info(
                        f"[POSE] map -> {base} TF 확인됨. 로봇 위치 publish 시작."
                    )
                return
            except Exception as e:
                last_err = e
                continue

        # 모든 후보 실패 → 5초에 한 번만 경고 (이유 포함)
        now = time.time()
        if now - getattr(self, "_last_tf_warn", 0.0) > 5.0:
            self._last_tf_warn = now
            self.get_logger().warn(
                f"[POSE] map -> {self.BASE_FRAME_CANDIDATES} TF를 못 찾음. "
                f"(로봇 bringup/amcl/odom 확인 필요) 마지막 에러: {last_err}"
            )

    # ---------------- costmap origin 프레임 변환 ----------------
    def transform_origin_to_map(self, x, y, yaw, src_frame):
        """
        costmap origin(x,y,yaw)을 src_frame(예: odom)에서 map 프레임으로 변환.
        local costmap은 odom 프레임이라 그대로 그리면 map↔odom 오프셋만큼 어긋남.
        실패하거나 이미 map이면 원본 그대로 반환.
        """
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

        # map
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
                    "yaw": quat_to_yaw(info.origin.orientation)
                },
                "data": list(map_msg.data),
            }

        # pose (TF 기반)
        pose_json = None
        if tf_pose is not None:
            x, y, yaw = tf_pose
            pose_json = {
                "x": x,
                "y": y,
                "yaw": yaw,
            }

        # path
        path_json = []
        if path_msg is not None:
            for ps in path_msg.poses:
                path_json.append({
                    "x": ps.pose.position.x,
                    "y": ps.pose.position.y,
                })

        # local costmap (odom 프레임 → map 프레임으로 변환해서 전송)
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

        # global costmap
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
        """(x, y, yaw) 또는 아직 TF 미준비면 None."""
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

        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw / 2.0)

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
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw / 2.0)

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
        """
        /slam_toolbox/reset 호출: 새 맵 시작(현재 pose-graph 리셋).
        """
        if not self.reset_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().error("/slam_toolbox/reset service not available.")
            return False

        req = Reset.Request()
        future = self.reset_client.call_async(req)
        # 결과는 굳이 기다리지 않고 True 반환 (비동기)
        self.get_logger().info("[WEB] Requested SLAM reset.")
        return True

    def slam_save_map(self, name: str) -> bool:
        """
        /slam_toolbox/save_map 호출: 현재 SLAM 맵을 pgm+yaml로 저장.
        name: 파일 이름 (확장자 없이). slam_toolbox가 실행중인 디렉토리에 저장됨.
        """
        if not self.save_map_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().error("/slam_toolbox/save_map service not available.")
            return False

        req = SaveMap.Request()
        req.name = String(data=name)

        future = self.save_map_client.call_async(req)
        self.get_logger().info(f"[WEB] Requested SLAM save_map: name='{name}'")
        # 마찬가지로 결과는 비동기로 처리하고 여기선 성공 요청만 리턴
        return True


############################################################
# Flask 라우트
############################################################

@app.route("/")
def serve_index():
    """브라우저에서 / 접근하면 index.html 반환"""
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/state")
def api_state():
    """현재 맵 + TF 기반 pose + 경로 + local/global costmap 반환"""
    global ros_node
    if ros_node is None:
        return jsonify({"error": "ROS node not started"}), 500

    return jsonify(ros_node.get_state_snapshot())


@app.route("/api/goal", methods=["POST"])
def api_goal():
    """웹에서 goal 입력"""
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500

    data = request.get_json()
    x = float(data["x"])
    y = float(data["y"])
    yaw = float(data.get("yaw", 0.0))  # 기본 yaw=0

    ok = ros_node.send_goal(x, y, yaw)
    return jsonify({"success": ok})


@app.route("/api/slam/reset", methods=["POST"])
def api_slam_reset():
    """
    SLAM Toolbox /reset 호출: 웹에서 '새 맵 시작' 버튼 눌렀을 때.
    """
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500

    ok = ros_node.slam_reset()
    return jsonify({"success": ok})


@app.route("/api/slam/save_map", methods=["POST"])
def api_slam_save_map():
    """
    SLAM Toolbox /save_map 호출: 웹에서 '맵 저장' 버튼 눌렀을 때.
    body: { "name": "pinky_lab_office" }
    """
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500

    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        # 비어있으면 날짜 기반 기본 이름
        name = time.strftime("pinky_map_%Y%m%d_%H%M%S")

    ok = ros_node.slam_save_map(name)
    return jsonify({"success": ok, "name": name})


@app.route("/api/locations", methods=["GET"])
def api_locations():
    """등록된 명명 위치(A~E 등) 목록 반환."""
    with _loc_lock:
        return jsonify(load_locations())


@app.route("/api/locations/set", methods=["POST"])
def api_locations_set():
    """name 위치 저장.
    - x,y(,yaw)가 함께 오면 그 좌표로 저장 (맵 클릭 지정)
    - 좌표가 없으면 로봇의 현재 위치(TF)로 저장 (현장 티칭)
    """
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500

    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"success": False, "msg": "name이 필요합니다"}), 400

    if data.get("x") is not None and data.get("y") is not None:
        # 맵 클릭으로 받은 좌표
        x = float(data["x"])
        y = float(data["y"])
        yaw = float(data.get("yaw", 0.0))
    else:
        # 로봇 현재 위치(TF)
        pose = ros_node.get_current_pose()
        if pose is None:
            return jsonify({"success": False,
                            "msg": "현재 위치(TF)를 아직 알 수 없습니다. 위치추정/주행을 먼저 확인하세요."}), 409
        x, y, yaw = pose

    with _loc_lock:
        locs = load_locations()
        locs[name] = {"x": round(x, 4), "y": round(y, 4), "yaw": round(yaw, 4)}
        save_locations(locs)

    return jsonify({"success": True, "name": name,
                    "x": x, "y": y, "yaw": yaw})


@app.route("/api/locations/delete", methods=["POST"])
def api_locations_delete():
    """name 위치 삭제."""
    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    with _loc_lock:
        locs = load_locations()
        if name in locs:
            del locs[name]
            save_locations(locs)
            return jsonify({"success": True, "name": name})
    return jsonify({"success": False, "msg": f"'{name}' 위치가 없습니다"}), 404


@app.route("/api/goto", methods=["POST"])
def api_goto():
    """name으로 등록된 위치로 주행 시작."""
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500

    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    with _loc_lock:
        locs = load_locations()
    if name not in locs:
        return jsonify({"success": False,
                        "msg": f"'{name}' 위치가 등록되지 않았습니다"}), 404

    p = locs[name]
    ok = ros_node.send_goal(float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
    return jsonify({"success": ok, "name": name})


@app.route("/api/mission/start", methods=["POST"])
def api_mission_start():
    """순찰/경유 시작. body: {names:[...], loop:bool}
    names 없으면 등록된 모든 구역을 이름순으로."""
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500
    data = request.get_json() or {}
    names = data.get("names")
    loop = bool(data.get("loop", False))
    if not names:
        with _loc_lock:
            names = sorted(load_locations().keys())
    ok = ros_node.start_mission(names, loop)
    return jsonify({"success": ok, "names": names, "loop": loop})


@app.route("/api/mission/stop", methods=["POST"])
def api_mission_stop():
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500
    ros_node.stop_mission()
    ros_node._set_mission("stopped", None)
    return jsonify({"success": True})


@app.route("/api/home", methods=["POST"])
def api_home():
    """홈 복귀 ('HOME' 구역 또는 맵 원점)."""
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500
    ok = ros_node.go_home()
    return jsonify({"success": ok})


@app.route("/api/schedule/start", methods=["POST"])
def api_schedule_start():
    """스케줄 순찰. body: {minutes:int, names:[...], loop:bool}"""
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500
    data = request.get_json() or {}
    minutes = int(data.get("minutes", 0))
    names = data.get("names")
    loop = bool(data.get("loop", False))
    if not names:
        with _loc_lock:
            names = sorted(load_locations().keys())
    ok = ros_node.start_schedule(minutes, names, loop)
    return jsonify({"success": ok, "minutes": minutes, "names": names})


@app.route("/api/schedule/stop", methods=["POST"])
def api_schedule_stop():
    global ros_node
    if ros_node is None:
        return jsonify({"success": False, "msg": "ROS not ready"}), 500
    ros_node.stop_schedule()
    return jsonify({"success": True})


############################################################
# ROS 스레드
############################################################
def ros_spin_thread():
    try:
        rclpy.spin(ros_node)
    finally:
        ros_node.destroy_node()
        rclpy.shutdown()


############################################################
# 메인 실행부
############################################################
if __name__ == "__main__":
    rclpy.init()    
    
    ros_node = Nav2WebBridge()

    ip_param = ros_node.get_parameter("ip").value
    port_param = ros_node.get_parameter("port").value

    # ROS2 스레드 시작
    t = threading.Thread(target=ros_spin_thread, daemon=True)
    t.start()

    time.sleep(1.0)

    print(f"Flask Web Server Running on http://{ip_param}:{port_param}")
    app.run(host=ip_param, port=int(port_param), debug=False)
