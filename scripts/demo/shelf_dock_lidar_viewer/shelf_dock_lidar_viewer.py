#!/usr/bin/env python3
"""서가 도킹이 무엇을 보고 움직이는지 보여주는 실시간 ROS 2 뷰어.

왼쪽은 `/map` 점유격자 위의 AMCL 로봇 위치와 도킹 PGM 광선이다.
오른쪽은 실제 `/scan` 라이다 점군을 로봇 기준으로 그대로 그린다.

중요: 현재 `shelf_dock`의 최종 거리 판단은 `/scan`이 아니라
카메라 표식 방향 + `/map` PGM 광선이다. `/scan` 패널은 주변/전방의 실제 관측을
함께 확인하기 위한 진단 화면이다. 두 데이터를 같은 색으로 그리지 않는 이유다.

실행 예:
    source /opt/ros/jazzy/setup.bash
    ROS_DOMAIN_ID=119 python3 scripts/demo/shelf_dock_lidar_viewer.py

q 또는 ESC: 종료
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass

import cv2
import numpy as np

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String


WINDOW = "Shelf dock: camera + PGM + live lidar"
BG = (24, 27, 32)
PANEL = (35, 40, 47)
WHITE = (235, 235, 235)
MUTED = (155, 160, 170)
LIDAR_GREEN = (80, 230, 100)
RAY_CYAN = (255, 220, 50)
HIT_RED = (55, 65, 245)
STOP_YELLOW = (45, 225, 255)
ROBOT_BLUE = (245, 160, 65)


@dataclass
class MapData:
    cells: np.ndarray
    resolution: float
    origin_x: float
    origin_y: float
    received_at: float


@dataclass
class ScanData:
    ranges: np.ndarray
    angles: np.ndarray
    range_min: float
    range_max: float
    received_at: float


def age_label(received_at: float | None) -> str:
    if received_at is None:
        return "waiting"
    age = time.monotonic() - received_at
    return f"{age:.1f}s old" if age >= 0.15 else "live"


def text(img: np.ndarray, value: str, xy: tuple[int, int], color=WHITE,
         scale: float = 0.48, thickness: int = 1) -> None:
    cv2.putText(img, value, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def panel(canvas: np.ndarray, rect: tuple[int, int, int, int], title: str) -> np.ndarray:
    x, y, w, h = rect
    cv2.rectangle(canvas, (x, y), (x + w, y + h), PANEL, -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (78, 85, 95), 1)
    text(canvas, title, (x + 12, y + 24), WHITE, 0.60, 2)
    return canvas[y + 35:y + h - 10, x + 10:x + w - 10]


def draw_robot_map(img: np.ndarray, world_to_px, pose: tuple[float, float, float]) -> None:
    x, y, yaw = pose
    # 앞 8cm, 뒤쪽 폭 5cm의 삼각형. 좌표를 map으로 만들고 나서 화면으로 변환한다.
    points = []
    for forward, side in ((0.09, 0.0), (-0.055, 0.052), (-0.055, -0.052)):
        wx = x + forward * math.cos(yaw) - side * math.sin(yaw)
        wy = y + forward * math.sin(yaw) + side * math.cos(yaw)
        points.append(world_to_px(wx, wy))
    cv2.fillConvexPoly(img, np.asarray(points, np.int32), ROBOT_BLUE)
    cv2.polylines(img, [np.asarray(points, np.int32)], True, WHITE, 1, cv2.LINE_AA)


def draw_map(view: np.ndarray, map_data: MapData | None,
             pose: tuple[float, float, float] | None, status: dict) -> None:
    h, w = view.shape[:2]
    if map_data is None:
        text(view, "Waiting for /map (TRANSIENT_LOCAL)...", (18, 58), MUTED)
        return

    cells = map_data.cells
    # ROS OccupancyGrid row 0은 좌하단이다. OpenCV row 0은 좌상단이므로 여기서만 뒤집는다.
    shown = np.full(cells.shape, 118, np.uint8)       # unknown
    shown[cells == 0] = 242                           # free
    shown[cells > 0] = 45                             # occupied
    shown = cv2.cvtColor(np.flipud(shown), cv2.COLOR_GRAY2BGR)
    map_h, map_w = shown.shape[:2]
    scale = min((w - 12) / map_w, (h - 38) / map_h)
    out_w, out_h = max(1, int(map_w * scale)), max(1, int(map_h * scale))
    x0, y0 = (w - out_w) // 2, 30 + (h - 30 - out_h) // 2
    view[y0:y0 + out_h, x0:x0 + out_w] = cv2.resize(shown, (out_w, out_h), interpolation=cv2.INTER_NEAREST)
    text(view, f"/map {age_label(map_data.received_at)}  occupied=black", (8, 18), MUTED, 0.43)

    def world_to_px(wx: float, wy: float) -> tuple[int, int]:
        col = (wx - map_data.origin_x) / map_data.resolution
        row = (wy - map_data.origin_y) / map_data.resolution
        return int(x0 + col * scale), int(y0 + (map_h - 1 - row) * scale)

    if pose is None:
        text(view, "Waiting for /amcl_pose...", (18, h - 14), MUTED)
        return

    draw_robot_map(view, world_to_px, pose)
    ray_yaw = status.get("ray_yaw_rad")
    distance = status.get("pgm_distance_m")
    clearance = float(status.get("clearance_m", 0.02))
    if not isinstance(ray_yaw, (int, float)) or not isinstance(distance, (int, float)):
        text(view, "CAMERA MARKER -> PGM RAY: waiting for dock calculation", (8, h - 14), MUTED, 0.42)
        return

    rx, ry, _ = pose
    hit_x = rx + float(distance) * math.cos(float(ray_yaw))
    hit_y = ry + float(distance) * math.sin(float(ray_yaw))
    stop_dist = max(0.0, float(distance) - clearance)
    stop_x = rx + stop_dist * math.cos(float(ray_yaw))
    stop_y = ry + stop_dist * math.sin(float(ray_yaw))
    robot_px, hit_px, stop_px = world_to_px(rx, ry), world_to_px(hit_x, hit_y), world_to_px(stop_x, stop_y)
    cv2.line(view, robot_px, hit_px, RAY_CYAN, 2, cv2.LINE_AA)
    cv2.circle(view, hit_px, 5, HIT_RED, -1, cv2.LINE_AA)
    cv2.circle(view, stop_px, 5, STOP_YELLOW, -1, cv2.LINE_AA)
    text(view, "CAMERA MARKER -> PGM RAY", (8, h - 32), RAY_CYAN, 0.42)
    text(view, f"PGM wall {distance * 100:.1f}cm", (hit_px[0] + 7, hit_px[1] - 7), HIT_RED, 0.40)
    text(view, f"STOP +{clearance * 100:.0f}cm", (stop_px[0] + 7, stop_px[1] + 15), STOP_YELLOW, 0.40)


def draw_lidar(view: np.ndarray, scan: ScanData | None, status: dict,
               view_range_m: float, front_half_angle_deg: float) -> None:
    h, w = view.shape[:2]
    center = (w // 2, h // 2 + 15)
    scale = min(w, h) * 0.42 / view_range_m
    for meters in (0.10, 0.20, 0.50, 1.00):
        if meters > view_range_m:
            continue
        r = int(meters * scale)
        cv2.circle(view, center, r, (75, 80, 90), 1, cv2.LINE_AA)
        text(view, f"{int(meters * 100)}cm", (center[0] + 4, center[1] - r - 3), MUTED, 0.38)
    # 로봇 기준 forward는 위쪽. 빨간 원은 실제 stop clearance를 크기로 보여준다.
    clearance = float(status.get("clearance_m", 0.02))
    cv2.circle(view, center, max(2, int(clearance * scale)), STOP_YELLOW, 1, cv2.LINE_AA)
    cv2.arrowedLine(view, center, (center[0], center[1] - 38), ROBOT_BLUE, 3, cv2.LINE_AA, tipLength=0.25)
    text(view, "ROBOT FORWARD", (center[0] + 10, center[1] - 29), ROBOT_BLUE, 0.40)

    if scan is None:
        text(view, "Waiting for live /scan...", (18, 58), MUTED)
        return
    valid = np.isfinite(scan.ranges) & (scan.ranges >= scan.range_min) & (scan.ranges <= scan.range_max)
    valid &= scan.ranges <= view_range_m
    ranges, angles = scan.ranges[valid], scan.angles[valid]
    # LaserScan +x(forward)를 화면 위쪽으로 회전한다.
    xs = center[0] + ranges * np.sin(angles) * scale
    ys = center[1] - ranges * np.cos(angles) * scale
    for x, y in zip(xs[::2], ys[::2]):
        cv2.circle(view, (int(x), int(y)), 1, LIDAR_GREEN, -1)

    half = math.radians(front_half_angle_deg)
    front = ranges[np.abs(angles) <= half]
    front_m = float(np.min(front)) if len(front) else None
    cone_r = int(min(view_range_m, 0.45) * scale)
    p1 = (int(center[0] - math.sin(half) * cone_r), int(center[1] - math.cos(half) * cone_r))
    p2 = (int(center[0] + math.sin(half) * cone_r), int(center[1] - math.cos(half) * cone_r))
    cv2.line(view, center, p1, (110, 130, 145), 1, cv2.LINE_AA)
    cv2.line(view, center, p2, (110, 130, 145), 1, cv2.LINE_AA)
    state = age_label(scan.received_at)
    text(view, f"LIVE LIDAR /scan: {state}", (8, 18), LIDAR_GREEN if state == "live" else MUTED, 0.48, 2)
    if front_m is None:
        text(view, f"front +/-{front_half_angle_deg:.0f}deg: clear or no return", (8, h - 14), MUTED, 0.42)
    else:
        color = HIT_RED if front_m <= clearance else LIDAR_GREEN
        text(view, f"front +/-{front_half_angle_deg:.0f}deg minimum: {front_m * 100:.1f}cm", (8, h - 14), color, 0.45, 2)


class ShelfDockLidarViewer(Node):
    def __init__(self, args) -> None:
        super().__init__("shelf_dock_lidar_viewer")
        self.map_data: MapData | None = None
        self.scan_data: ScanData | None = None
        self.pose: tuple[float, float, float] | None = None
        self.pose_at: float | None = None
        self.status: dict = {}
        self.scan_forward = math.radians(args.scan_forward_deg)

        map_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(OccupancyGrid, args.map, self._on_map, map_qos)
        self.create_subscription(PoseWithCovarianceStamped, args.amcl, self._on_pose, 10)
        self.create_subscription(LaserScan, args.scan, self._on_scan, qos_profile_sensor_data)
        self.create_subscription(String, args.status, self._on_status, 20)

    def _on_map(self, msg: OccupancyGrid) -> None:
        info = msg.info
        if not info.width or not info.height or info.resolution <= 0:
            return
        cells = np.asarray(msg.data, dtype=np.int16).reshape(info.height, info.width)
        self.map_data = MapData(cells, float(info.resolution), float(info.origin.position.x),
                                float(info.origin.position.y), time.monotonic())

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose
        q = p.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self.pose = (float(p.position.x), float(p.position.y), yaw)
        self.pose_at = time.monotonic()

    def _on_scan(self, msg: LaserScan) -> None:
        ranges = np.asarray(msg.ranges, dtype=np.float32)
        angles = msg.angle_min + np.arange(len(ranges), dtype=np.float32) * msg.angle_increment
        # 설치 방향이 다르면 --scan-forward-deg로 보정한다. 화면 위쪽은 이 보정 뒤의 전방이다.
        self.scan_data = ScanData(ranges, angles - self.scan_forward, float(msg.range_min),
                                  float(msg.range_max), time.monotonic())

    def _on_status(self, msg: String) -> None:
        try:
            status = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        if status.get("event") == "shelf_dock":
            self.status = status


def render(node: ShelfDockLidarViewer, args) -> np.ndarray:
    canvas = np.full((720, 1280, 3), BG, np.uint8)
    left = panel(canvas, (12, 12, 750, 650), "WHAT DOCKING USES: CAMERA MARKER -> PGM MAP RAY")
    right = panel(canvas, (774, 12, 494, 650), "LIVE LIDAR /scan (diagnostic)")
    draw_map(left, node.map_data, node.pose, node.status)
    draw_lidar(right, node.scan_data, node.status, args.range_m, args.front_half_angle_deg)

    phase = str(node.status.get("phase", "waiting for shelf_dock_status"))
    remaining = node.status.get("remaining_to_clearance_m")
    detail = f"robot: {args.robot or 'not specified'} | phase: {phase}"
    if isinstance(remaining, (int, float)):
        detail += f" | PGM remaining to 2cm: {float(remaining) * 100:.1f}cm"
    text(canvas, detail, (20, 692), WHITE, 0.56, 2)
    text(canvas, "cyan=PGM ray  red=occupied wall  yellow=2cm stop point  green=real lidar points  q/ESC=quit",
         (20, 714), MUTED, 0.42)
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="실시간 서가 도킹 PGM/라이다 설명 뷰어")
    parser.add_argument("--scan", default="/scan", help="LaserScan 토픽 (기본 /scan)")
    parser.add_argument("--map", default="/map", help="OccupancyGrid 토픽 (기본 /map)")
    parser.add_argument("--amcl", default="/amcl_pose", help="AMCL pose 토픽")
    parser.add_argument("--status", default="/shelf_dock_status", help="도킹 상태 String 토픽")
    parser.add_argument("--range-m", type=float, default=1.20, help="라이다 패널 최대 거리(m)")
    parser.add_argument("--front-half-angle-deg", type=float, default=15.0, help="전방 최소거리 섹터 반각")
    parser.add_argument("--scan-forward-deg", type=float, default=0.0, help="센서 +x 대비 로봇 전방 보정각")
    parser.add_argument("--robot", required=True,
                        help="대상 로봇 ID. 생략하면 어떤 ROS 도메인을 볼지 불명확하므로 실행하지 않는다")
    args = parser.parse_args()
    if args.range_m <= 0:
        parser.error("--range-m은 0보다 커야 합니다")

    rclpy.init()
    node = ShelfDockLidarViewer(args)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)
    print(f"[dock-viewer] robot={args.robot} /map, /amcl_pose, /scan, "
          "/shelf_dock_status 구독 시작 (q/ESC 종료)")
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.02)
            cv2.imshow(WINDOW, render(node, args))
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
    finally:
        node.destroy_node()
        rclpy.shutdown()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
