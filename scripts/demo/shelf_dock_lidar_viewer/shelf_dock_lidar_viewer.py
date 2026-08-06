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
import re
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# 흰테두리 후보 박스를 그리려고 robot_agent 의 검출 로직을 그대로 가져온다(직접
# import — 로직 재작성 안 함) — cv2/numpy 만 쓰는 순수 알고리즘 모듈이라 ROS/서비스
# 의존 없이 안전하다. 여기서 다시 짜면 언젠가 원본과 갈라져 뷰어가 거짓 박스를
# 그리는 사고가 난다(BT 스냅샷과 같은 급의 함정).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                       / "aba_controller/libi_drive_controller/robot_agent"))
from app.shelf.green_marker import GreenConfig, detect_candidates  # noqa: E402

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String


# ── 카메라: HTTP/ROS 새 토픽 없이, 이미 있는 UDP 영상 스트림을 직접 받는다 ──────
# `aba_ai_service/follower_perception/scripts/udp_video.py`(camera_sender 가 로봇에서
# 이 노트북으로 이미 유니캐스트하는 그 스트림, 포트는 로봇 번호로 계산)의
# FrameReassembler/UdpVideoReceiver 를 그대로 옮겼다 — cross-service import 는 피하고
# (서비스 독립성), 이 데모 스크립트 하나로 계속 서서 돌게 한다. 프로토콜이 바뀌면
# 그쪽 원본도 같이 봐야 한다.
_CAM_HDR = struct.Struct(">IHH")   # frame_id, chunk_idx, total_chunks


def cam_port_for(robot: str) -> int:
    """로봇 이름의 마지막 숫자로 영상 UDP 포트를 계산한다 — `scripts/_common.sh`의
    `robot_ports()`와 정확히 같은 규칙(pinky-1=6001, pinky-2=6011, pinky-3=6021)이라
    로봇·노트북 양쪽이 서로 포트를 안 넘겨도 맞는다. 이건 **프로덕션** 포트다 —
    perception_server 가 이미 그 포트/뷰어 슬롯을 쓴다(listen(1), 뷰어 1개뿐).
    이 뷰어는 DEBUG_PORT_OFFSET 을 더한 별도 포트를 기본으로 쓴다(자리다툼 안 하게)."""
    m = re.findall(r"\d+", robot or "")
    num = int(m[-1]) if m else 1
    return 6001 + (num - 1) * 10


# camera_sender.py --debug-port(2026-08-05)가 앞캠을 여기로 "추가" 송출한다 —
# 프로덕션 UDP/뷰어 슬롯과 완전히 분리된 포트라 perception_server/libi_gui/
# security_viewer 와 안 부딪힌다. libi_pi.sh --dock-debug 가 로봇 쪽에서 같은 규칙으로
# 이 값을 계산한다 — 여기 값을 바꾸면 거기도 같이 바꿔야 한다.
DEBUG_PORT_OFFSET = 1000


class _CamFrameReassembler:
    """청크를 모아 최신 프레임만 남긴다(오래된/뒤섞인 조각은 버림). udp_video.py 의
    FrameReassembler 와 동일 프로토콜: [frame_id][chunk_idx][total_chunks][jpeg bytes]."""

    def __init__(self) -> None:
        self._buffers: dict[int, dict[int, bytes]] = {}
        self._latest_done = -1

    def feed(self, packet: bytes) -> bytes | None:
        if len(packet) < _CAM_HDR.size:
            return None
        fid, idx, total = _CAM_HDR.unpack(packet[:_CAM_HDR.size])
        if fid <= self._latest_done:
            if fid < self._latest_done - 30:
                self._buffers.clear()
                self._latest_done = -1
            else:
                return None
        buf = self._buffers.setdefault(fid, {})
        buf[idx] = packet[_CAM_HDR.size:]
        if len(buf) >= total:
            jpeg = b"".join(buf[i] for i in range(total))
            self._latest_done = fid
            for k in [k for k in self._buffers if k <= fid]:
                del self._buffers[k]
            return jpeg
        return None


class CamReceiver:
    """카메라 UDP 스트림을 백그라운드 스레드로 받아 최신 프레임만 들고 있는다.
    바인드 실패(포트 이미 씀 — 보통 aba_ai_service 가 떠 있음)해도 뷰어 전체는
    안 죽는다, 카메라 패널만 못 뜬다."""

    def __init__(self, port: int, host: str = "0.0.0.0") -> None:
        self.error: str | None = None
        self._frame: np.ndarray | None = None
        self._at: float | None = None
        self._lock = threading.Lock()
        self._run = True
        try:
            self._sock: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.settimeout(0.5)
            self._sock.bind((host, port))
        except OSError as e:
            self.error = f"UDP {port} bind 실패: {e}"
            self._sock = None
            return
        self._reasm = _CamFrameReassembler()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._run and self._sock is not None:
            try:
                pkt, _addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            jpeg = self._reasm.feed(pkt)
            if jpeg is None:
                continue
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                with self._lock:
                    self._frame, self._at = img, time.monotonic()

    def latest(self) -> tuple[np.ndarray | None, float | None]:
        with self._lock:
            return self._frame, self._at

    def close(self) -> None:
        self._run = False
        if self._sock is not None:
            self._sock.close()


def draw_camera(view: np.ndarray, frame: np.ndarray | None, at: float | None,
                 marker_error_px: object = None, marker_row_px: object = None) -> None:
    h, w = view.shape[:2]
    if frame is None or at is None or time.monotonic() - at > 2.0:
        text(view, "Waiting for camera UDP stream...", (8, 58), MUTED, 0.42)
        return
    fh, fw = frame.shape[:2]
    scale = min(w / fw, h / fh)
    out_w, out_h = max(1, int(fw * scale)), max(1, int(fh * scale))
    x0, y0 = (w - out_w) // 2, (h - out_h) // 2
    view[y0:y0 + out_h, x0:x0 + out_w] = cv2.resize(frame, (out_w, out_h))
    text(view, f"FRONT CAM {age_label(at)}", (8, 18), LIDAR_GREEN, 0.44, 2)

    # shelf_dock.py 제어는 가로(u)만 본다(로봇은 yaw 로만 도니까, USE_CAMERA_CALIBRATION
    # =False라 목표 cx는 정확히 frame_width/2, shelf_dock.py:181-182) — 하양 점은 그래서
    # 항상 화면 정중앙 세로선 위다. 근데 마커가 "실제로 어디를 보고 있는지"(상하 포함
    # 진짜 2D 위치)는 marker_row_px(centroid_uv 의 v, 2026-08-05 추가)로 노랑 점을
    # 실제 세로 위치에 찍는다 — 더는 세로 중앙에 강제로 고정하지 않는다.
    center_x, mid_y = x0 + out_w // 2, y0 + out_h // 2
    cv2.line(view, (center_x, y0), (center_x, y0 + out_h), (90, 90, 95), 1, cv2.LINE_AA)
    cv2.circle(view, (center_x, mid_y), 5, WHITE, 2, cv2.LINE_AA)

    # 흰테두리 후보 박스 — robot_agent 의 detect_candidates() 를 그대로 돌려서 실제
    # 검출/판정과 100% 같은 것만 그린다(뷰어가 따로 짠 판정이 아니다). 초록=흰테두리
    # 통과(centroid_uv 가 우선 고르는 풀), 회색=탈락(안전망에서만 쓰임).
    for c in detect_candidates(frame):
        bx0, by0 = int(round(x0 + c["x"] * scale)), int(round(y0 + c["y"] * scale))
        bx1, by1 = int(round(x0 + (c["x"] + c["w"]) * scale)), int(round(y0 + (c["y"] + c["h"]) * scale))
        color = LIDAR_GREEN if c["bordered"] else MUTED
        cv2.rectangle(view, (bx0, by0), (bx1, by1), color, 1, cv2.LINE_AA)

    if isinstance(marker_error_px, (int, float)):
        marker_x = int(round(center_x + float(marker_error_px) * scale))
        marker_y = (int(round(y0 + float(marker_row_px) * scale))
                    if isinstance(marker_row_px, (int, float)) else mid_y)
        cv2.circle(view, (marker_x, marker_y), 6, STOP_YELLOW, -1, cv2.LINE_AA)
        text(view, f"marker err {marker_error_px:+.1f}px", (8, h - 10), STOP_YELLOW, 0.38)


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
STEP_DONE = (90, 150, 95)
STEP_CURRENT = (45, 225, 255)
STEP_PENDING = (55, 58, 64)
STEP_COMPLETED = (90, 220, 120)

# 한 단계(특히 marker_centering)가 반복 보고할 때 step_log 에 쌓아두는 최대 개수.
# DOCK_STATUS_UPDATE_SEC(0.5초)마다 오니 200개면 100초 분량 — 실패 타임아웃(10~20초)
# 몇 번을 넘게 담고도 메모리에 문제없다.
HISTORY_CAP = 200

# Groot2 식 진행 단계 — shelf_dock.py 의 report() 호출 순서 그대로다
# (app/core/shelf_dock.py:492,531,730,753,756,769,531,782,794,797,708).
# marker_centering 은 stage 로 초기/재관측 두 번 나온다 — (phase, stage, 짧은 라벨).
# 라벨은 영문만 쓴다: cv2 Hershey 폰트가 한글 글리프를 못 그린다.
STEPS: list[tuple[str, str | None, str]] = [
    ("started", None, "START"),
    ("marker_centering", "초기 중앙정렬", "CENTER1"),
    ("initial_marker_centered", None, "CENTERED1"),
    ("lateral_plan_ready", None, "LAT PLAN"),
    ("lateral_start", None, "LAT MOVE"),
    ("lateral_complete", None, "LAT DONE"),
    ("marker_centering", "재관측 중앙정렬", "CENTER2"),
    ("reobserve_marker_centered", None, "CENTERED2"),
    ("final_plan_ready", None, "FINAL PLAN"),
    ("final_start", None, "FINAL MOVE"),
    ("final_progress", None, "APPROACH"),
]


def step_index_for(phase: object, stage: object) -> int:
    """`phase`(+marker_centering 이면 `stage`)가 STEPS 의 몇 번째인지. 못 찾으면 -1."""
    for i, (p, s, _label) in enumerate(STEPS):
        if p == phase and (s is None or s == stage):
            return i
    return -1


def failure_reason(message: object) -> str:
    """`message`(예: '초기 비주얼 서보 실패: marker_not_found')에서 마지막 ': ' 뒤 영문
    reason 코드만 뽑는다. cv2 Hershey 폰트가 한글을 못 그리므로 한글 부분은 화면에
    못 띄운다 — 대신 영문 reason 은 항상 정확히 왜 실패했는지를 말해준다."""
    if not isinstance(message, str) or not message:
        return ""
    return message.rsplit(": ", 1)[-1]


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


def scan_front_min(scan: "ScanData | None", front_half_angle_deg: float,
                   max_m: float | None = None) -> float | None:
    """전방 ±`front_half_angle_deg` 안의 **실제 라이다** 최소거리(m). 없으면 `None`.

    도킹의 거리 판정은 `/map` PGM 광선이고 이건 안 쓴다 — 둘이 **얼마나 다른지**를
    남기려고 잰다(PGM 은 SLAM 당시 스냅샷 + 2cm 격자라 실제와 어긋난다).

    `max_m` 은 **화면 표시용 클리핑**이다. 로그에는 넘기지 않는다 — 표시 범위 밖의
    벽을 빼버리면 "없음" 으로 남아서 정작 비교가 안 된다.
    """
    if scan is None:
        return None
    valid = np.isfinite(scan.ranges) & (scan.ranges >= scan.range_min) & (scan.ranges <= scan.range_max)
    if max_m is not None:
        valid &= scan.ranges <= max_m
    ranges, angles = scan.ranges[valid], scan.angles[valid]
    front = ranges[np.abs(angles) <= math.radians(front_half_angle_deg)]
    return float(np.min(front)) if len(front) else None


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
    # 화면에는 표시 범위로 자른 값을 쓴다(로그는 안 자른 값 — scan_front_min 머리말).
    front_m = scan_front_min(scan, front_half_angle_deg, max_m=view_range_m)
    cone_r = int(min(view_range_m, 0.45) * scale)
    p1 = (int(center[0] - math.sin(half) * cone_r), int(center[1] - math.cos(half) * cone_r))
    p2 = (int(center[0] + math.sin(half) * cone_r), int(center[1] - math.cos(half) * cone_r))
    cv2.line(view, center, p1, (110, 130, 145), 1, cv2.LINE_AA)
    cv2.line(view, center, p2, (110, 130, 145), 1, cv2.LINE_AA)
    state = age_label(scan.received_at)
    text(view, f"/scan: {state}", (8, 18), LIDAR_GREEN if state == "live" else MUTED, 0.44, 2)
    if front_m is None:
        text(view, "front: clear / no return", (8, h - 14), MUTED, 0.38)
    else:
        color = HIT_RED if front_m <= clearance else LIDAR_GREEN
        text(view, f"front min: {front_m * 100:.1f}cm", (8, h - 14), color, 0.42, 2)


def step_color(i: int, step_index: int, terminal: str | None) -> tuple[int, int, int]:
    """Groot2 처럼 각 단계 색 — 지난 단계=녹색, 현재=노랑, 남은 단계=회색, 멈춘 그
    단계만 실패면 빨강. `step_index` 는 실패로 phase 가 지워지기 전 마지막으로
    도달한 진행 단계다(ShelfDockLidarViewer._on_status 참고)."""
    if terminal == "failed" and i == step_index:
        return HIT_RED
    if terminal == "completed" and i <= step_index:
        return STEP_COMPLETED
    if i < step_index:
        return STEP_DONE
    if i == step_index and terminal is None:
        return STEP_CURRENT
    return STEP_PENDING


def draw_steps(canvas: np.ndarray, rect: tuple[int, int, int, int],
               step_index: int, terminal: str | None) -> None:
    """왼쪽 세로 STEPS 칼럼. LOG 칼럼(draw_step_log)과 같은 row_h 로 줄을 맞춘다."""
    view = panel(canvas, rect, "STEPS")
    h, w = view.shape[:2]
    row_h = max(16, h // len(STEPS))
    for i, (_p, _s, label) in enumerate(STEPS):
        ry = i * row_h
        color = step_color(i, step_index, terminal)
        cv2.rectangle(view, (0, ry), (w, ry + row_h - 2), color, -1)
        cv2.rectangle(view, (0, ry), (w, ry + row_h - 2), (10, 10, 12), 1)
        text_color = (20, 20, 20) if color in (STEP_CURRENT, STEP_COMPLETED) else WHITE
        text(view, label, (4, ry + row_h - 8), text_color, 0.34, 1)


def format_step_fields(phase: object, fields: dict) -> str:
    """한 단계가 보낸 필드를 한 줄 요약으로. 어떤 값이 검출됐는지/어디 점이 찍혔는지/
    얼마나 움직였는지를 phase 별로 고른다. 모르는 phase 나 새 필드가 와도 raw
    key=value 로 떨어뜨려서 조용히 빠지는 값이 없게 한다(뷰어가 message 를 놓쳤던
    것과 같은 실수를 반복하지 않는다)."""
    def cm(key: str) -> str | None:
        v = fields.get(key)
        return f"{float(v) * 100:.1f}cm" if isinstance(v, (int, float)) else None

    def wall(label: str = "wall detected at") -> str | None:
        """PGM 광선 거리 옆에 **실제 라이다** 전방 최소거리와 그 차이를 같이 적는다.

        도킹은 PGM 만 보고 멈추는데 PGM 은 SLAM 당시 스냅샷 + 2cm 격자라 실제와
        어긋난다. `CLEARANCE_M`(7cm) - 로봇반지름(6cm) = 실여유 1cm 뿐이라 그 차이가
        몇 cm 인지가 곧 안전 여유다 — 그걸 로그에 숫자로 남긴다.

        `diff` 는 `라이다 - PGM`. **음수면 실제 벽이 PGM 보다 가깝다** = 위험한 쪽이다.

        ⚠️ 이름을 `Δ` 가 아니라 ASCII `diff` 로 쓴다 — 화면은 cv2 Hershey 폰트라
        **ASCII 밖 글자를 `?` 로 그린다.** 실기(2026-08-05 19:48 영상)에서 실제로
        `??-5.1cm` 로 나왔다. 같은 문자열이 화면과 저장 로그에 함께 쓰이므로
        (`format_step_fields` 하나가 둘 다 만든다) 양쪽에서 읽히는 ASCII 로 맞춘다.
        """
        pgm, lidar = fields.get("pgm_distance_m"), fields.get("viewer_scan_front_m")
        if not isinstance(pgm, (int, float)):
            return None
        base = f"{label} {float(pgm) * 100:.1f}cm"
        if not isinstance(lidar, (int, float)):
            return f"{base} (lidar -)"       # /scan 이 없거나 전방에 반사가 없다
        return (f"{base} (lidar {float(lidar) * 100:.1f}cm, "
                f"diff{(float(lidar) - float(pgm)) * 100:+.1f}cm)")

    parts: list[str] = []
    if phase == "started":
        parts = [p for p in [cm("clearance_m") and f"clearance={cm('clearance_m')}"] if p]
    elif phase in ("initial_marker_centered", "reobserve_marker_centered"):
        frames = fields.get("frames")
        parts = [f"marker found, centered in {frames} frames"] if frames is not None else ["marker centered"]
    elif phase == "marker_centering":
        err_px, row_px, stable_frames, tol_px = (fields.get("marker_error_px"), fields.get("marker_row_px"),
                                                  fields.get("stable_frames"), fields.get("tol_px"))
        if err_px is not None:
            row_part = f"  row={row_px}px" if row_px is not None else ""
            parts = [f"err {err_px}px (tol {tol_px}px){row_part}  stable_frames={stable_frames}"]
    elif phase == "lateral_plan_ready":
        parts = [p for p in [
            wall(),
            cm("planned_lateral_m") and f"move sideways {cm('planned_lateral_m')}",
        ] if p]
    elif phase == "lateral_start":
        parts = [p for p in [
            cm("planned_lateral_m") and f"moving {cm('planned_lateral_m')}",
            cm("initial_error_m") and f"error {cm('initial_error_m')}",
        ] if p]
    elif phase == "lateral_complete":
        parts = [p for p in [cm("final_error_m") and f"stopped, error {cm('final_error_m')}"] if p]
    elif phase == "final_plan_ready":
        parts = [p for p in [
            wall(),
            cm("planned_forward_m") and f"move forward {cm('planned_forward_m')}",
        ] if p]
    elif phase == "final_start":
        parts = [p for p in [cm("planned_forward_m") and f"moving {cm('planned_forward_m')}"] if p]
    elif phase == "final_progress":
        parts = [p for p in [
            wall("wall at"),
            cm("remaining_to_clearance_m") and f"remaining {cm('remaining_to_clearance_m')}",
            fields.get("marker_error_px") is not None and f"marker err {fields['marker_error_px']}px",
            fields.get("linear_mps") is not None and f"speed {fields['linear_mps']}m/s",
        ] if p]
    if parts:
        return "  ".join(parts)
    # marker_centering(진행 중, 아직 결과 없음) 이나 처음 보는 phase — 원본을 그대로.
    return "  ".join(f"{k}={v}" for k, v in fields.items() if k not in ("event", "shelf", "phase", "stage"))


def draw_step_log(canvas: np.ndarray, rect: tuple[int, int, int, int],
                   step_log: dict, step_index: int, terminal: str | None) -> None:
    """3번째(아래) 패널 — 각 단계가 성공했는지/뭘 검출했는지/얼마나 움직였는지를
    STEPS 순서대로 한 줄씩. `step_log` 는 ShelfDockLidarViewer 가 단계별로 마지막
    필드를 기억해둔 것이다(phase 가 바뀌면 status 전체가 지워지므로)."""
    view = panel(canvas, rect, "LOG (value / move dist.)")
    h, w = view.shape[:2]
    row_h = max(16, h // len(STEPS))
    for i, (_p, _s, label) in enumerate(STEPS):
        ry = i * row_h
        dot = step_color(i, step_index, terminal)
        record = step_log.get(i)
        detail = record["summary"] if record else ""
        text(view, detail or "-", (4, ry + row_h - 8), MUTED if not record else dot, 0.36)


class ShelfDockLidarViewer(Node):
    def __init__(self, args) -> None:
        super().__init__("shelf_dock_lidar_viewer")
        self.map_data: MapData | None = None
        self.scan_data: ScanData | None = None
        self.pose: tuple[float, float, float] | None = None
        self.pose_at: float | None = None
        self.status: dict = {}
        self.status_at: float | None = None   # 이 status 가 언제 왔는지 — 화면에 나이를
        # 같이 찍어서 "이거 방금 거 맞아?"(2026-08-05 실측 질문)를 눈으로 바로 답한다.
        # "failed"/"completed" 는 shelf_dock.py 가 phase 를 그걸로 덮어써서 어느
        # 단계에서 죽었는지가 status 에서 사라진다 — 여기서 따로 기억해둔다.
        self.step_index: int = -1
        # STEPS 인덱스 -> {"phase", "stage", "fields"(원본 status, 한글 그대로),
        # "summary"(화면용 영문 요약)}. 화면(cv2)엔 summary 만 그리고, 'c' 로 덤프할
        # 때는 fields 의 원본 한글 message 를 그대로 쓴다 — cv2 Hershey 폰트가 한글을
        # 못 그리는 것과 별개로 파일/터미널 텍스트는 한글이 멀쩡히 나온다.
        self.step_log: dict[int, dict] = {}
        self.scan_forward = math.radians(args.scan_forward_deg)
        self.front_half_deg = float(args.front_half_angle_deg)
        self.cam: CamReceiver | None = None
        if not args.no_cam:
            self.cam = CamReceiver(args.cam_port)
            if self.cam.error:
                self.get_logger().warning(f"[cam] {self.cam.error} — 라이다 패널로 대체")

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

    def _record_step(self, idx: int, phase, stage, fields: dict, summary: str) -> None:
        """step_log[idx] 를 갱신한다 — "summary"/"fields" 는 최신값으로 덮어쓰지만
        "history" 는 계속 쌓는다(최대 HISTORY_CAP개). marker_centering 같은 단계는
        같은 idx 로 초당 여러 번 오는데, 예전엔 마지막 값(대개 실패로 덮임)만 남아서
        진동인지 드리프트인지 사후에 구분할 방법이 없었다(2026-08-05 실측)."""
        prev = self.step_log.get(idx, {})
        history = prev.get("history", [])
        history = history[-(HISTORY_CAP - 1):] + [{"phase": phase, "fields": fields}]
        self.step_log[idx] = {"phase": phase, "stage": stage, "fields": fields,
                               "summary": summary, "history": history}

    def reset(self) -> None:
        """'r' 키 — 화면을 강제로 비운다. 새 시도의 "started" 가 와야만 지워지던 걸,
        실기 세션에서 "이거 방금 실패한 거 맞아 옛날 기록 아니야?" 라는 질문(2026-08-05)이
        나온 뒤 손으로도 지울 수 있게 뺐다."""
        self.status = {}
        self.status_at = None
        self.step_index = -1
        self.step_log = {}

    def _on_status(self, msg: String) -> None:
        try:
            status = json.loads(msg.data)
        except (TypeError, ValueError):
            return
        if status.get("event") != "shelf_dock":
            return
        # PGM 광선 거리와 **같은 순간의** 실제 라이다 전방거리를 같이 박아둔다.
        # 로봇이 보내주는 값이 아니라 뷰어가 잰 값이라 `viewer_` 접두사를 붙인다
        # (덤프에 원본과 섞여도 출처가 드러나게).
        #
        # ⚠️ 둘은 **같은 광선이 아니다** — PGM 쪽은 카메라 표식 방향으로 쏜 한 줄이고,
        # 이쪽은 로봇 정면 ±`front_half_angle_deg` 원뿔의 최소값이다. 그래서 이 차이는
        # "PGM 이 몇 cm 틀렸다" 의 상한이지 정밀 보정값이 아니다. 크기를 알려는 값이다.
        status["viewer_scan_front_m"] = scan_front_min(self.scan_data, self.front_half_deg)
        self.status = status
        self.status_at = time.monotonic()
        phase = status.get("phase")
        if phase == "started":
            self.step_index = 0     # 새 도킹 시도 — 이전 시도의 진행도를 지운다.
            self.step_log = {}
            self._record_step(0, phase, None, status, format_step_fields(phase, status))
            return
        if phase in ("completed", "failed"):
            # phase 가 "failed"/"completed" 로 바뀌면서 몇 번째 단계였는지가 status 에서
            # 사라진다 — step_index(마지막 진행 단계)에 결과(+원본 message)를 붙여둔다.
            # _record_step 이 history 는 보존하니 실패 직전 궤적이 안 지워진다.
            if self.step_index >= 0:
                mark = "OK" if phase == "completed" else "FAILED"
                message = status.get("message")
                summary = mark + (f": {failure_reason(message)}" if message else "")
                prev_stage = self.step_log.get(self.step_index, {}).get("stage")
                self._record_step(self.step_index, phase, prev_stage, status, summary)
            return
        idx = step_index_for(phase, status.get("stage"))
        if idx < 0:
            return
        if idx > self.step_index:   # "failed"/"completed" 는 위에서 걸러졌으니 여기 idx
            self.step_index = idx   # 는 항상 실제 진행 단계 — 마지막 도달 단계만 올라간다.
        self._record_step(idx, phase, status.get("stage"), status, format_step_fields(phase, status))


CANVAS_W, CANVAS_H = 1320, 600
STEPS_RECT = (12, 12, 110, 500)
LOG_RECT = (132, 12, 320, 500)
MAP_RECT = (462, 12, 560, 500)
LIDAR_RECT = (1032, 12, 276, 500)


def render(node: ShelfDockLidarViewer, args) -> np.ndarray:
    canvas = np.full((CANVAS_H, CANVAS_W, 3), BG, np.uint8)
    map_view = panel(canvas, MAP_RECT, "CAMERA MARKER -> PGM MAP RAY")
    draw_map(map_view, node.map_data, node.pose, node.status)

    # 우상단 작은 패널: 카메라 프레임이 최근에 왔으면 그걸, 아니면(스트림 없음/
    # 아직 안 옴/bind 실패) 라이다로 대체한다 — 둘 중 하나는 항상 뭔가 보여준다.
    cam = getattr(node, "cam", None)
    cam_frame, cam_at = (cam.latest() if cam is not None else (None, None))
    if cam_frame is not None and cam_at is not None and time.monotonic() - cam_at <= 2.0:
        cam_view = panel(canvas, LIDAR_RECT, f"FRONT CAM (udp:{getattr(args, 'cam_port', '?')})")
        draw_camera(cam_view, cam_frame, cam_at, node.status.get("marker_error_px"),
                    node.status.get("marker_row_px"))
    else:
        lidar_view = panel(canvas, LIDAR_RECT, "LIVE LIDAR /scan")
        draw_lidar(lidar_view, node.scan_data, node.status, args.range_m, args.front_half_angle_deg)
        cam_error = getattr(cam, "error", None)
        if cam_error:
            # bind 실패(대개 perception_server.py 가 같은 UDP 포트를 이미 씀) 사유를
            # 화면에 직접 띄운다 — 로그에만 남기면 "왜 캠 안 보여" 가 매번 반복된다.
            h, w = lidar_view.shape[:2]
            text(lidar_view, "camera off:", (8, h - 30), HIT_RED, 0.38, 1)
            text(lidar_view, cam_error, (8, h - 14), HIT_RED, 0.32, 1)

    phase = str(node.status.get("phase", "waiting for shelf_dock_status"))
    terminal = phase if phase in ("completed", "failed") else None
    draw_steps(canvas, STEPS_RECT, node.step_index, terminal)
    draw_step_log(canvas, LOG_RECT, node.step_log, node.step_index, terminal)

    if terminal == "failed":
        reason = failure_reason(node.status.get("message")) or "(no reason field)"
        text(canvas, f"FAILED at step {node.step_index + 1}/{len(STEPS)}: {reason}",
             (12, 528), HIT_RED, 0.52, 2)
    elif terminal == "completed":
        text(canvas, "DOCKED", (12, 528), STEP_COMPLETED, 0.52, 2)
    else:
        text(canvas, "cyan=PGM ray  red=occupied wall  yellow=2cm stop point  green=real lidar points",
             (12, 528), MUTED, 0.40)

    remaining = node.status.get("remaining_to_clearance_m")
    # status_at 나이를 같이 찍는다 — "이거 방금 실패한 거 맞아, 옛날 기록 아니야?"
    # (2026-08-05 실측 질문)를 화면만 보고 바로 답할 수 있게. 오래됐으면(>3초) 빨갛게.
    status_at = getattr(node, "status_at", None)   # 기존 시험용 fake 노드엔 없을 수 있다
    status_age = age_label(status_at)
    age_color = HIT_RED if (status_at is not None
                            and time.monotonic() - status_at > 3.0) else WHITE
    detail = f"robot: {args.robot or 'not specified'} | phase: {phase} ({status_age})"
    if isinstance(remaining, (int, float)):
        detail += f" | PGM remaining to 2cm: {float(remaining) * 100:.1f}cm"
    text(canvas, detail, (12, 552), age_color, 0.48, 1)
    text(canvas, "q/ESC=quit  c=copy full log to /tmp (Korean-safe text file)  r=reset log",
         (12, 572), MUTED, 0.40)
    return canvas


def marker_error_trend(history: list) -> str | None:
    """marker_error_px 궤적에서 진동(부호가 왔다갔다)인지 드리프트(한쪽으로 쭉)인지
    한쪽에 붙어 못 좁혀지는(stuck-offset)인지 구분할 실마리를 뽑는다. 샘플이 모자라면
    (< 2) 아무 판단도 못 하니 None."""
    errs = [h["fields"].get("marker_error_px") for h in history]
    errs = [float(e) for e in errs if isinstance(e, (int, float))]
    if len(errs) < 2:
        return None
    sign_changes = sum(1 for a, b in zip(errs, errs[1:]) if a * b < 0)
    return (f"samples={len(errs)}  range=[{min(errs):.1f}, {max(errs):.1f}]px  "
            f"sign_changes={sign_changes}  last5={[round(e, 1) for e in errs[-5:]]}")


def format_log_text(node: ShelfDockLidarViewer, args) -> str:
    """'c' 로 덤프하는 전체 로그 — 순수 텍스트라 cv2 와 달리 한글 message 가 그대로
    나온다. 화면에 안 보이던 진짜 실패 사유가 여기엔 있다."""
    phase = node.status.get("phase", "?")
    lines = [
        f"shelf_dock run log — robot={args.robot}  saved={time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"phase={phase}  reached step {node.step_index + 1}/{len(STEPS)}",
        "",
    ]
    for i, (_p, _s, label) in enumerate(STEPS):
        rec = node.step_log.get(i)
        mark = "-   " if not rec else ("FAIL" if rec["phase"] == "failed" else
                                        "OK  " if rec["phase"] in ("completed",) or rec["summary"] else "... ")
        lines.append(f"[{i + 1:2d}] {label:<10} {mark} {rec['summary'] if rec else ''}")
        if rec and rec["phase"] in ("failed", "completed") and rec["fields"].get("message"):
            lines.append(f"       message (원문): {rec['fields']['message']}")
        trend = marker_error_trend(rec.get("history", [])) if rec else None
        if trend:
            lines.append(f"       marker_error_px trend: {trend}")
    return "\n".join(lines)


def dump_log(node: ShelfDockLidarViewer, args) -> str:
    content = format_log_text(node, args)
    path = f"/tmp/shelf_dock_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n[dock-viewer] log saved -> {path}\n{content}\n", flush=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="실시간 서가 도킹 PGM/라이다 설명 뷰어")
    parser.add_argument("--scan", default="/scan", help="LaserScan 토픽 (기본 /scan)")
    parser.add_argument("--map", default="/map", help="OccupancyGrid 토픽 (기본 /map)")
    parser.add_argument("--amcl", default="/amcl_pose", help="AMCL pose 토픽")
    parser.add_argument("--status", default="/shelf_dock_status", help="도킹 상태 String 토픽")
    parser.add_argument("--range-m", type=float, default=1.20, help="라이다 패널 최대 거리(m)")
    parser.add_argument("--front-half-angle-deg", type=float, default=15.0, help="전방 최소거리 섹터 반각")
    # 실측(2026-08-05, pinky-3): 라이다가 물리적으로 반대 방향(후방)으로 달려있어
    # 기본 보정각을 180으로 둔다. 다른 로봇/장착이면 --scan-forward-deg로 덮어쓴다.
    parser.add_argument("--scan-forward-deg", type=float, default=180.0, help="센서 +x 대비 로봇 전방 보정각(기본 180 — 라이다 후방 장착)")
    parser.add_argument("--robot", required=True,
                        help="대상 로봇 ID. 생략하면 어떤 ROS 도메인을 볼지 불명확하므로 실행하지 않는다")
    # HTTP도 새 ROS 토픽도 안 쓴다 — camera_sender(Pi)가 이미 이 노트북으로 보내는
    # UDP 영상 스트림을 그대로 받는다. 기본은 **디버그 전용 포트**(프로덕션 6021과
    # 분리, DEBUG_PORT_OFFSET) — Pi에서 `libi_pi.sh --dock-debug`로 띄워야 여기로 온다.
    # 프로덕션 포트(perception_server가 이미 씀)를 굳이 보려면 --cam-port로 직접 준다.
    parser.add_argument("--cam-port", type=int, default=None,
                        help="카메라 UDP 포트. 생략하면 --robot 이름으로 계산한 디버그 "
                             "포트(pinky-3=7021, libi_pi.sh --dock-debug 필요)")
    parser.add_argument("--no-cam", action="store_true", help="카메라 패널 끄고 라이다만 본다")
    args = parser.parse_args()
    if args.range_m <= 0:
        parser.error("--range-m은 0보다 커야 합니다")
    if args.cam_port is None:
        args.cam_port = cam_port_for(args.robot) + DEBUG_PORT_OFFSET

    rclpy.init()
    node = ShelfDockLidarViewer(args)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, CANVAS_W, CANVAS_H)
    print(f"[dock-viewer] robot={args.robot} /map, /amcl_pose, /scan, "
          f"/shelf_dock_status 구독 시작, cam udp:{args.cam_port} (q/ESC 종료, c=로그 저장)")
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.02)
            cv2.imshow(WINDOW, render(node, args))
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("c"):
                dump_log(node, args)
            if key == ord("r"):
                node.reset()
    finally:
        if node.cam is not None:
            node.cam.close()
        node.destroy_node()
        rclpy.shutdown()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
