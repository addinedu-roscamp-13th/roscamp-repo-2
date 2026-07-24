"""
로봇팔 JetCobot 라우터

⚠️  포트/장치 상수는 최상단에 모아두었음.
    로봇팔(JetCobot, 시리얼)은 이 서버(192.168.0.70)에 직접 연결됨.
    주행 로봇(모터/센서)은 별도 모듈(drive.py / robot.py)로 분리되어 있음.

WS   /api/arm/ws/arm
POST /api/arm/stop
POST /api/arm/color-pick
POST /api/arm/face-track/start
POST /api/arm/face-track/stop
POST /api/arm/gesture/start
POST /api/arm/gesture/stop
"""
from __future__ import annotations

import asyncio
import base64
import time
import json
from typing import Literal

import cv2
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_robot_db
from app.models import MotionSequence, iso_utc
from app.hardware.camera_stream import camera as camera_hw

# ══════════════════════════════════════════════════════════════════════════════
# 하드웨어 상수 — 로봇팔 전용
# ══════════════════════════════════════════════════════════════════════════════
ARM_SERIAL_PORT = "/dev/ttyJETCOBOT"   # 로봇팔 시리얼 포트 (udev 심링크 → /dev/ttyUSB0)
ARM_BAUD_RATE   = 1_000_000            # baud rate (고정값)
# 카메라: 주행 로봇과 동일한 Pi Camera(libcamera) 공유 — camera_hw 싱글톤 사용

HOME_ANGLES        = [0, 0, 0, 0, 0, 0]        # J1~J6 홈 각도 (degrees)
CAMERA_VIEW_ANGLES   = [2.3, -1.7, -15.8, -66.7, -1.2, -3.6]  # 카메라가 바닥(작업공간)을 바라보는 포지션
FACE_VIEW_ANGLES     = [0, 0, -90, 90, 0, 0]                   # 카메라가 정면을 바라보는 포지션 (얼굴 추적용)
GESTURE_VIEW_ANGLES  = [0, 20, -90, 90, 0, 0]                  # 제스처 제어용 — J2=+20으로 카메라를 더 위쪽으로
CAMERA_VIEW_ANGLES_2 = CAMERA_VIEW_ANGLES
CAMERA_VIEW_ANGLES_3 = CAMERA_VIEW_ANGLES
CAMERA_VIEW_ANGLES_4 = CAMERA_VIEW_ANGLES
CAMERA_VIEW_ANGLES_5 = CAMERA_VIEW_ANGLES
CAMERA_VIEW_ANGLES_6 = CAMERA_VIEW_ANGLES
HOME_SPEED      = 40                   # 홈 이동 속도
MOVE_SPEED      = 30                   # 일반 이동 속도
SCAN_SPEED      = 20                   # 탐색 스윕 이동 속도 (느릴수록 카메라 흔들림 감소)
GRIPPER_OPEN    = 100                  # 그리퍼 완전 열림 (0=닫힘 ~ 100=열림)
GRIPPER_CLOSE   = 0                    # 그리퍼 완전 닫힘
SAFE_Z_MM       = 260.0               # 이동 시 충돌 방지 안전 높이 (mm)

# 두리번 탐색 파라미터
# 색상 집기: 중앙→좌→우 순서로 좁은 작업공간 탐색
COLOR_SCAN_J1   = [0, -25, 25, -50, 50]   # degrees (중앙 우선)
# 얼굴/제스처: 더 넓은 범위 탐색
FACE_SCAN_J1    = [0, -30, 30, -60, 60]   # degrees (중앙 우선)
SCAN_SETTLE_S       = 1.2   # 각 탐색 위치 정착 대기 (카메라 흔들림 소멸 대기)
SCAN_FRAMES_PER_POS = 12    # 각 위치에서 감지 시도 프레임 수
FACE_RESCAN_FRAMES  = 30    # 얼굴 추적 중 N프레임 동안 얼굴 없으면 재탐색

# P 제어 이득 (얼굴 추적)
FACE_KP_J1 = 20.0   # 수평 비례 이득 (화면 정규화 오차 → 관절 각도 delta)
FACE_KP_J2 = 10.0   # 수직 비례 이득

# HSV 색상 범위 (color-pick)  lower / upper
HSV_RANGES: dict[str, list[tuple[list[int], list[int]]]] = {
    "red":    [([0, 100, 70], [10, 255, 255]), ([170, 100, 70], [180, 255, 255])],
    "green":  [([40,  40, 40],  [80, 255, 255])],
    "blue":   [([100, 40, 40], [140, 255, 255])],
    "yellow": [([20, 100, 100], [40, 255, 255])],
}

# ══════════════════════════════════════════════════════════════════════════════
# 선택적 SDK 임포트 — 미설치 시 Demo 모드로 동작
# ══════════════════════════════════════════════════════════════════════════════
try:
    from pymycobot.mycobot280 import MyCobot280 as _SDK  # type: ignore
    _HAS_SDK = True
except ImportError:
    _SDK = None
    _HAS_SDK = False

try:
    import mediapipe as mp  # type: ignore
    _mp_hands = mp.solutions.hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.5,
    )
    _HAS_MP = True
except Exception:
    _mp_hands = None
    _HAS_MP = False

try:
    import easyocr  # type: ignore
    _HAS_EASYOCR = True
except ImportError:
    _HAS_EASYOCR = False

ArmMode = Literal["idle", "homing", "color_pick", "face_track", "gesture", "ocr", "playback"]


# ══════════════════════════════════════════════════════════════════════════════
# ArmBridge — 팔 제어 싱글톤 + WS 브로드캐스터
# ══════════════════════════════════════════════════════════════════════════════
class ArmBridge:
    def __init__(self) -> None:
        self._mc = None
        self._sdk_lock = asyncio.Lock()          # pymycobot 직렬화
        self._remote_ip: str | None = None      # 원격 로봇 IP

        self._clients: set[WebSocket] = set()
        self._mode: ArmMode = "idle"
        self._joints: list[float] = [0.0] * 6
        self._gripper: float = 0.0
        self._detection: dict | None = None
        self._connected = False
        self._ocr_reader = None

        # 백그라운드 태스크
        self._robot_frame: bytes | None = None  # 로봇팔이 PUSH한 최신 프레임

        self._poll_task: asyncio.Task | None = None
        self._broadcast_task: asyncio.Task | None = None
        self._mode_task: asyncio.Task | None = None

    def set_remote_robot(self, robot_ip: str | None) -> None:
        if robot_ip:
            robot_ip = robot_ip.replace("http://", "").replace("https://", "").split(":")[0]
        self._remote_ip = robot_ip

    def _call_remote_api(self, path: str, method: str = "POST", data: dict | None = None) -> dict | None:
        import urllib.request
        import json
        if not self._remote_ip:
            return None
        url = f"http://{self._remote_ip}:9001{path}"
        try:
            payload = json.dumps(data).encode("utf-8") if data is not None else None
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"} if payload else {},
                method=method
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            if path != "/api/arm/state":
                print(f"[remote] Failed to call {method} {path} on {self._remote_ip}: {e}", flush=True)
            return None

    def _has_arm_target(self) -> bool:
        return bool(self._remote_ip) or self._mc is not None

    # ── 팔 연결 ──────────────────────────────────────────────────────────────
    async def ensure_connected(self) -> bool:
        if self._remote_ip:
            self._connected = True
            return True
        if self._mc is not None:
            return True
        if not _HAS_SDK:
            return False
        try:
            loop = asyncio.get_running_loop()
            mc = await loop.run_in_executor(
                None, lambda: _SDK(ARM_SERIAL_PORT, ARM_BAUD_RATE)
            )
            mc.thread_lock = True
            self._mc = mc
            self._connected = True
            return True
        except Exception:
            self._mc = None
            self._connected = False
            return False

    # ── 카메라 프레임 취득 ───────────────────────────────────────────────────
    def _get_frame(self) -> np.ndarray | None:
        # 1순위: 로봇팔이 PUSH한 프레임
        if self._robot_frame is not None:
            arr = np.frombuffer(self._robot_frame, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)

        # 2순위: 원격 로봇인 경우 스냅샷 API로 폴링 (PUSH가 안되는 경우 백업)
        if self._remote_ip:
            try:
                from app.security import create_access_token
                import urllib.request
                token = create_access_token(1)  # admin id = 1
                url = f"http://{self._remote_ip}:9001/api/robot/camera/snapshot?token={token}"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    jpeg = resp.read()
                    arr = np.frombuffer(jpeg, dtype=np.uint8)
                    return cv2.imdecode(arr, cv2.IMREAD_COLOR)
            except Exception:
                pass

        # 3순위: 로컬 카메라. API-only 호출에서는 WS add_client()가 실행되지 않으므로
        # 프레임이 필요해지는 시점에 카메라를 보장한다.
        if not self._remote_ip and not camera_hw.is_running():
            camera_hw.start()
        jpeg = camera_hw.get_jpeg()
        if jpeg is None:
            return None
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    # ── WS 클라이언트 관리 ───────────────────────────────────────────────────
    async def add_client(self, ws: WebSocket) -> None:
        self._clients.add(ws)
        if not self._remote_ip and not camera_hw.is_running():
            camera_hw.start()
        if not self._poll_task or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
        if not self._broadcast_task or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._cam_broadcast_loop())

    async def remove_client(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        if not self._clients:
            return
        import json
        data = json.dumps(msg)
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def _push_state(self) -> None:
        await self.broadcast({
            "type": "state",
            "connected": self._connected,
            "demo_mode": not self._connected,
            "mode": self._mode,
            "joints": self._joints,
            "gripper": self._gripper,
            "detection": self._detection,
        })

    async def _push_log(self, level: str, msg: str) -> None:
        await self.broadcast({"type": "log", "level": level, "msg": msg})

    # ── 상태 폴링 루프 (200 ms) ──────────────────────────────────────────────
    async def _poll_loop(self) -> None:
        import math
        loop = asyncio.get_running_loop()
        while self._clients:
            await self.ensure_connected()
            if self._remote_ip:
                state = await loop.run_in_executor(
                    None, lambda: self._call_remote_api("/api/arm/state", "GET")
                )
                if state:
                    self._joints = state.get("joints", [0.0] * 6)
                    self._gripper = state.get("gripper", 0.0)
                    self._connected = state.get("connected", False)
                else:
                    self._connected = False
            elif self._mc is not None:
                try:
                    angles = await loop.run_in_executor(None, self._mc.get_angles)
                    if isinstance(angles, list) and len(angles) >= 6:
                        self._joints = [float(a) for a in angles[:6]]
                    gripper = await loop.run_in_executor(None, self._mc.get_gripper_value)
                    if isinstance(gripper, (int, float)) and gripper >= 0:
                        self._gripper = float(gripper)
                except Exception:
                    self._connected = False
                    self._mc = None
            else:
                # Demo 모드 — 관절 사인파 시뮬레이션
                t = time.time()
                self._joints = [round(5.0 * math.sin(t + i * 1.0), 2) for i in range(6)]
            await self._push_state()
            await asyncio.sleep(0.2)

    # ── 카메라 브로드캐스트 루프 (30 fps) ───────────────────────────────────
    async def _cam_broadcast_loop(self) -> None:
        loop = asyncio.get_running_loop()
        no_cam_warned = False
        no_cam_ticks = 0
        while self._clients:
            frame = await loop.run_in_executor(None, self._get_frame)
            if frame is not None:
                no_cam_ticks = 0
                no_cam_warned = False
                frame = await loop.run_in_executor(None, self._apply_overlay, frame)
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                b64 = base64.b64encode(buf.tobytes()).decode()
                await self.broadcast({"type": "camera", "frame": b64})
            else:
                no_cam_ticks += 1
                # 3초(~90틱) 동안 프레임 없으면 한 번만 경고
                if no_cam_ticks == 90 and not no_cam_warned:
                    no_cam_warned = True
                    await self._push_log("warn", "카메라를 찾을 수 없습니다 (Pi Camera / USB 카메라 연결 확인)")
            await asyncio.sleep(1 / 30)

    # ── 오버레이 (동기, 스레드풀에서 실행) ──────────────────────────────────
    def _apply_overlay(self, frame: np.ndarray) -> np.ndarray:
        if self._mode == "color_pick":
            color = (self._detection or {}).get("color", "red")
            frame, _ = self._detect_color(frame, color)
        elif self._mode == "face_track":
            frame = self._draw_faces(frame)
        elif self._mode == "gesture":
            frame = self._draw_gesture_overlay(frame)
        elif self._mode == "ocr":
            frame = self._draw_ocr_overlay(frame)
        return frame

    def _draw_ocr_overlay(self, frame: np.ndarray) -> np.ndarray:
        det = self._detection
        if not det or "results" not in det:
            return frame
        for item in det["results"]:
            bbox = item.get("bbox")
            text = item.get("text", "")
            if bbox and len(bbox) == 4:
                pts = np.array(bbox, np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], True, (0, 255, 0), 2)
                x, y = int(bbox[0][0]), int(bbox[0][1])
                cv2.putText(frame, text, (x, max(0, y - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        return frame

    def _draw_gesture_overlay(self, frame: np.ndarray) -> np.ndarray:
        det = self._detection
        if not det or "fingers" not in det:
            return frame
        h, w = frame.shape[:2]
        n = det.get("fingers", -1)
        if 0 <= n <= 5:
            # 화면 중앙에 큰 숫자 표시
            cv2.putText(frame, str(n), (w // 2 - 55, h // 2 + 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 14)
            cv2.putText(frame, str(n), (w // 2 - 55, h // 2 + 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 255, 0), 8)
        # 핀치(그리퍼) 인디케이터 바
        pinch = det.get("pinch", 0.0)
        bx0, bx1 = int(w * 0.1), int(w * 0.9)
        by0, by1 = h - 30, h - 14
        cv2.rectangle(frame, (bx0, by0), (bx1, by1), (80, 80, 80), 1)
        cv2.rectangle(frame, (bx0, by0), (bx0 + int((bx1 - bx0) * pinch), by1), (0, 140, 255), -1)
        cv2.putText(frame, f"GRIP {int(pinch*100)}%", (bx0, by0 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 140, 255), 1)
        return frame

    # ── 색상 감지 ────────────────────────────────────────────────────────────
    @staticmethod
    def _detect_color(frame: np.ndarray, color: str) -> tuple[np.ndarray, dict | None]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lower, upper in HSV_RANGES.get(color, []):
            mask |= cv2.inRange(hsv, np.array(lower), np.array(upper))
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return frame, None
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) < 500:
            return frame, None
        x, y, w, h = cv2.boundingRect(c)
        cx, cy = x + w // 2, y + h // 2
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)
        cv2.putText(frame, f"{color} ({cx},{cy})", (x, max(0, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        h_frame, w_frame = frame.shape[:2]
        return frame, {"color": color, "cx": cx, "cy": cy, "w": w, "h": h,
                       "frame_w": w_frame, "frame_h": h_frame}

    # ── 얼굴 감지 오버레이 ───────────────────────────────────────────────────
    _cascade: cv2.CascadeClassifier | None = None

    @classmethod
    def _get_cascade(cls) -> cv2.CascadeClassifier:
        if cls._cascade is None:
            cls._cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
        return cls._cascade

    def _draw_faces(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._get_cascade().detectMultiScale(gray, 1.05, 3, minSize=(40, 40))
        for (x, y, w, h) in faces:
            cx, cy = x + w // 2, y + h // 2
            cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 100, 0), 2)
            cv2.circle(frame, (cx, cy), 4, (0, 0, 255), -1)
            cv2.putText(frame, f"face ({cx},{cy})", (x, max(0, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 100, 0), 2)
        return frame

    # ── SDK 호출 헬퍼 ────────────────────────────────────────────────────────
    async def _arm(self, fn, *args):
        """pymycobot 호출을 스레드풀 + 락으로 직렬화. 원격 모드인 경우 원격 로봇 API를 호출한다."""
        if self._remote_ip:
            fn_name = fn if isinstance(fn, str) else getattr(fn, "__name__", str(fn))
            if fn_name == "send_angles":
                angles, speed = args[0], args[1]
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._call_remote_api("/api/arm/angles", "POST", {"angles": angles, "speed": speed})
                )
            elif fn_name == "set_gripper_value":
                value, speed = args[0], args[1]
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._call_remote_api("/api/arm/gripper", "POST", {"value": value, "speed": speed})
                )
            elif fn_name == "stop":
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self._call_remote_api("/api/arm/stop", "POST")
                )
            return None

        if self._mc is None:
            return None
        actual_fn = fn
        if isinstance(fn, str):
            actual_fn = getattr(self._mc, fn, None)
        if actual_fn is None or not callable(actual_fn):
            return None
        loop = asyncio.get_running_loop()
        async with self._sdk_lock:
            return await loop.run_in_executor(None, lambda: actual_fn(*args))

    # ── 두리번 탐색 헬퍼 ─────────────────────────────────────────────────────
    async def _scan_for(self, base_angles: list[float], detect_fn,
                        label: str = "오브젝트", j1_positions: list[int] | None = None):
        """
        base_angles 기준으로 J1을 j1_positions에 따라 스윕하며 오브젝트를 탐색한다.
        반환: (detection_result, found_angles) — 발견 못하면 (None, 마지막 위치)
        """
        loop = asyncio.get_running_loop()
        scan_angles = list(base_angles)
        for j1 in (j1_positions or FACE_SCAN_J1):
            scan_angles[0] = j1
            await self._push_log("info", f"{label} 탐색 중... J1={j1:+.0f}°")
            if self._has_arm_target():
                await self._arm("send_angles", scan_angles, SCAN_SPEED)
                await asyncio.sleep(SCAN_SETTLE_S)
            for _ in range(SCAN_FRAMES_PER_POS):
                frame = await loop.run_in_executor(None, self._get_frame)
                if frame is not None:
                    det = await loop.run_in_executor(None, detect_fn, frame)
                    if det:
                        return det, list(scan_angles)
                await asyncio.sleep(0.05)
        return None, list(scan_angles)

    # ── 공개 명령 ─────────────────────────────────────────────────────────────
    async def cmd_home(self) -> None:
        await self._cancel_mode()
        self._mode = "homing"
        await self._push_log("info", "홈 포지션으로 이동 중...")
        if self._has_arm_target():
            await self._arm("send_angles", HOME_ANGLES, HOME_SPEED)
            await asyncio.sleep(3)
            await self._arm("set_gripper_value", GRIPPER_OPEN, HOME_SPEED)
            await asyncio.sleep(1)
        self._mode = "idle"
        await self._push_log("success", "홈 포지션 완료")

    async def cmd_camera_view(self, preset: int = 1) -> None:
        angles_map = {
            1: CAMERA_VIEW_ANGLES,
            2: CAMERA_VIEW_ANGLES_2,
            3: CAMERA_VIEW_ANGLES_3,
            4: CAMERA_VIEW_ANGLES_4,
            5: CAMERA_VIEW_ANGLES_5,
            6: CAMERA_VIEW_ANGLES_6,
        }
        angles = angles_map.get(preset, CAMERA_VIEW_ANGLES)
        await self._cancel_mode()
        self._mode = "homing"
        await self._push_log("info", f"카메라 뷰 포지션 {preset} 이동 중... {angles}")
        if self._has_arm_target():
            await self._arm("send_angles", angles, HOME_SPEED)
            await asyncio.sleep(3)
        self._mode = "idle"
        await self._push_log("success", f"포지션 {preset} 완료")

    async def cmd_stop(self) -> None:
        await self._cancel_mode()
        if self._has_arm_target():
            await self._arm("stop")
        self._mode = "idle"
        await self._push_log("warn", "긴급 정지")

    async def cmd_jog_stop(self) -> None:
        """키보드 조그용 — 모드 취소 없이 팔만 즉시 정지."""
        if self._remote_ip:
            await self._arm("stop")
        elif self._mc:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._mc.stop)

    async def _cancel_mode(self) -> None:
        if self._mode_task and not self._mode_task.done():
            self._mode_task.cancel()
            try:
                await self._mode_task
            except asyncio.CancelledError:
                pass
        self._mode = "idle"
        self._detection = None

    # ── 색상 블록 집기 ───────────────────────────────────────────────────────
    async def start_color_pick(self, color: str) -> None:
        await self._cancel_mode()
        self._mode = "color_pick"
        self._detection = {"color": color}
        self._mode_task = asyncio.create_task(self._color_pick_task(color))

    async def _color_pick_task(self, color: str) -> None:
        try:
            await self._push_log("info", f"{color} 블록 탐색 시작...")

            # 1. 카메라 뷰 포지션으로 이동
            if self._has_arm_target():
                await self._arm("send_angles", CAMERA_VIEW_ANGLES, HOME_SPEED)
                await asyncio.sleep(2.5)

            # 2. J1 스윕하며 색상 탐색 (두리번) — 작업공간이 좁으므로 좁은 범위 사용
            det, found_angles = await self._scan_for(
                CAMERA_VIEW_ANGLES,
                lambda frame: self._detect_color(frame, color)[1],
                f"{color} 블록",
                j1_positions=COLOR_SCAN_J1,
            )

            if det is None:
                await self._push_log("warn", f"{color} 블록을 찾을 수 없습니다")
                self._mode = "idle"
                return

            self._detection = det
            await self._push_log("success", f"{color} 감지! 픽셀 ({det['cx']}, {det['cy']})")

            if self._has_arm_target():
                fw = det["frame_w"]
                err_x = (det["cx"] - fw / 2) / (fw / 2)   # -1 ~ +1
                angles = list(found_angles)
                angles[0] = max(-168.0, min(168.0, angles[0] - err_x * 30))
                await self._push_log("info", f"J1 보정 → {angles[0]:.1f}°")
                await self._arm("send_angles", angles, MOVE_SPEED)
                await asyncio.sleep(2.5)
                await self._arm("set_gripper_value", GRIPPER_CLOSE, MOVE_SPEED)
                await asyncio.sleep(1.5)
                await self._arm("send_angles", HOME_ANGLES, HOME_SPEED)
                await asyncio.sleep(3)
                await self._arm("set_gripper_value", GRIPPER_OPEN, HOME_SPEED)
                await asyncio.sleep(1)

            await self._push_log("success", "집기 완료 — 홈 복귀")
        except asyncio.CancelledError:
            pass
        finally:
            self._mode = "idle"
            self._detection = None

    # ── 얼굴 추적 ────────────────────────────────────────────────────────────
    async def start_face_track(self) -> None:
        await self._cancel_mode()
        self._mode = "face_track"
        self._mode_task = asyncio.create_task(self._face_track_task())

    async def _face_track_task(self) -> None:
        loop = asyncio.get_running_loop()
        cascade = self._get_cascade()

        def _detect_face(frame: np.ndarray) -> dict | None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.05, 3, minSize=(40, 40))
            if len(faces) == 0:
                return None
            fx, fy, fw, fh = max(faces, key=lambda r: r[2] * r[3])
            return {"cx": int(fx + fw / 2), "cy": int(fy + fh / 2), "w": fw, "h": fh}

        try:
            await self._push_log("info", "얼굴 탐색 시작...")

            # 1. 얼굴 추적 뷰로 이동
            if self._has_arm_target():
                await self._arm("send_angles", FACE_VIEW_ANGLES, HOME_SPEED)
                await asyncio.sleep(2.5)

            # 2. 스윕 탐색 (두리번)
            det, found_angles = await self._scan_for(
                FACE_VIEW_ANGLES, _detect_face, "얼굴", j1_positions=FACE_SCAN_J1
            )
            if det is None:
                await self._push_log("warn", "얼굴을 찾지 못했습니다. 추적 대기 중...")

            # 3. 추적 루프 — 얼굴 소실 시 재탐색
            j1 = float(found_angles[0])
            j2 = float(self._joints[1])
            no_face_count = 0
            await self._push_log("info", "얼굴 추적 시작")

            while True:
                frame = await loop.run_in_executor(None, self._get_frame)
                if frame is None:
                    await asyncio.sleep(0.05)
                    continue

                h, w = frame.shape[:2]
                face = await loop.run_in_executor(None, _detect_face, frame)

                if face:
                    no_face_count = 0
                    err_x = (face["cx"] - w / 2) / (w / 2)
                    err_y = (face["cy"] - h / 2) / (h / 2)
                    j1 = max(-168.0, min(168.0, j1 - err_x * FACE_KP_J1))
                    j2 = max(-135.0, min(135.0, j2 - err_y * FACE_KP_J2))
                    self._detection = face
                    if self._has_arm_target():
                        angles = list(self._joints)
                        angles[0] = j1
                        angles[1] = j2
                        await self._arm("send_angles", angles, MOVE_SPEED)
                else:
                    no_face_count += 1
                    if no_face_count >= FACE_RESCAN_FRAMES:
                        await self._push_log("info", "얼굴 놓침 — 재탐색 시작")
                        det, found_angles = await self._scan_for(
                            FACE_VIEW_ANGLES, _detect_face, "얼굴", j1_positions=FACE_SCAN_J1
                        )
                        no_face_count = 0
                        if det:
                            j1 = float(found_angles[0])
                            await self._push_log("success", "얼굴 재발견!")

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            self._mode = "idle"
            self._detection = None
            await self._push_log("info", "얼굴 추적 중지")

    # ── 제스처 제어 ──────────────────────────────────────────────────────────
    async def start_gesture(self) -> None:
        await self._cancel_mode()
        self._mode = "gesture"
        self._mode_task = asyncio.create_task(self._gesture_task())

    async def _gesture_task(self) -> None:
        from collections import deque
        loop = asyncio.get_running_loop()
        try:
            await self._push_log("info", "제스처 제어 준비 중 (MediaPipe)...")

            # 1. GESTURE_VIEW_ANGLES로 이동 (J2=+20 → 카메라가 더 위를 향함)
            if self._has_arm_target():
                await self._arm("send_angles", GESTURE_VIEW_ANGLES, HOME_SPEED)
                await asyncio.sleep(2.5)

            # 2. 스윕 탐색으로 손 위치 찾기 (두리번)
            det, _ = await self._scan_for(
                GESTURE_VIEW_ANGLES, self._classify_gesture, "손(제스처)",
                j1_positions=FACE_SCAN_J1,
            )
            if det:
                await self._push_log("success", f"손 감지! fingers={det.get('fingers')} — 제어 시작")
            else:
                await self._push_log("warn", "손을 찾지 못했습니다. 제스처 대기 중...")

            # 3. 제스처 제어 루프
            positions: deque[int] = deque(maxlen=8)  # 핸드 중심 X 위치로 흔들기 감지
            last_fingers = -1
            last_pinch_pct = -1
            while True:
                frame = await loop.run_in_executor(None, self._get_frame)
                if frame is not None:
                    result = await loop.run_in_executor(None, self._classify_gesture, frame)
                    if result:
                        self._detection = result

                        # 흔들기 감지 — X축 이동 범위가 넓으면 정지
                        positions.append(result["cx"])
                        if len(positions) == 8 and (max(positions) - min(positions)) > 80:
                            await self._push_log("warn", "흔들기 감지 — 긴급 정지")
                            if self._has_arm_target():
                                await self._arm("stop")
                            positions.clear()
                            await asyncio.sleep(0.5)
                            continue

                        # 엄지-검지 간격 → 그리퍼 (5% 이상 변화 시만 명령)
                        pinch_pct = int(result["pinch"] * 100)
                        if abs(pinch_pct - last_pinch_pct) >= 5:
                            last_pinch_pct = pinch_pct
                            await self._push_log("info", f"그리퍼 {pinch_pct}%")
                            if self._has_arm_target():
                                await self._arm("set_gripper_value", pinch_pct, MOVE_SPEED)

                        # 손가락 수 → 팔 동작 (변화 시만)
                        fingers = result["fingers"]
                        if fingers != last_fingers:
                            last_fingers = fingers
                            await self._execute_gesture(fingers)
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            self._mode = "idle"
            self._detection = None
            await self._push_log("info", "제스처 제어 중지")

    @staticmethod
    def _classify_gesture(frame: np.ndarray) -> dict | None:
        if not _HAS_MP or _mp_hands is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = _mp_hands.process(rgb)
        if not result.multi_hand_landmarks:
            return None
        lm = result.multi_hand_landmarks[0]
        h, w = frame.shape[:2]

        # 손가락 수 계산
        tip_ids = [8, 12, 16, 20]
        pip_ids = [6, 10, 14, 18]
        extended = sum(1 for t, p in zip(tip_ids, pip_ids)
                       if lm.landmark[t].y < lm.landmark[p].y)
        thumb_up = lm.landmark[4].x < lm.landmark[3].x
        fingers = extended + (1 if thumb_up else 0)

        # 엄지-검지 간격 → 그리퍼 비율 (0.0=붙음 1.0=최대 벌림)
        t4 = lm.landmark[4]   # 엄지 끝
        i8 = lm.landmark[8]   # 검지 끝
        p5 = lm.landmark[5]   # 검지 기저
        p17 = lm.landmark[17] # 새끼 기저
        pinch_dist = ((t4.x - i8.x) ** 2 * w ** 2 + (t4.y - i8.y) ** 2 * h ** 2) ** 0.5
        palm_w = ((p5.x - p17.x) ** 2 * w ** 2 + (p5.y - p17.y) ** 2 * h ** 2) ** 0.5
        pinch = min(1.0, pinch_dist / (palm_w + 1e-6))

        # 손 중심 (landmark 9 = 중지 기저)
        cx = int(lm.landmark[9].x * w)
        cy = int(lm.landmark[9].y * h)

        return {"fingers": fingers, "pinch": pinch, "cx": cx, "cy": cy}

    async def _execute_gesture(self, fingers: int) -> None:
        labels = {
            0: "대기", 1: "인사 ×3", 2: "세로 이동 ×2",
            3: "J2 +15°↑", 4: "J2 -15°↓", 5: "홈 복귀",
        }
        await self._push_log("info", f"제스처 {fingers}손가락 — {labels.get(fingers, '?')}")
        if not self._has_arm_target():
            return

        if fingers == 1:
            # 인사하기 — J2 위아래 3회
            base_j2 = float(self._joints[1])
            for _ in range(3):
                up = list(self._joints)
                up[1] = max(-135.0, min(135.0, base_j2 + 30))
                await self._arm("send_angles", up, MOVE_SPEED)
                await asyncio.sleep(0.6)
                down = list(self._joints)
                down[1] = max(-135.0, min(135.0, base_j2 - 10))
                await self._arm("send_angles", down, MOVE_SPEED)
                await asyncio.sleep(0.6)
            # 원래 J2 위치 복귀
            restore = list(self._joints)
            restore[1] = base_j2
            await self._arm("send_angles", restore, MOVE_SPEED)

        elif fingers == 2:
            # 세로 이동 — J2 위아래 2회
            base_j2 = float(self._joints[1])
            for _ in range(2):
                up = list(self._joints)
                up[1] = max(-135.0, min(135.0, base_j2 + 30))
                await self._arm("send_angles", up, MOVE_SPEED)
                await asyncio.sleep(0.7)
                down = list(self._joints)
                down[1] = max(-135.0, min(135.0, base_j2 - 10))
                await self._arm("send_angles", down, MOVE_SPEED)
                await asyncio.sleep(0.7)
            restore = list(self._joints)
            restore[1] = base_j2
            await self._arm("send_angles", restore, MOVE_SPEED)

        elif fingers == 3:
            angles = list(self._joints)
            angles[1] = max(-135.0, min(135.0, angles[1] + 15))
            await self._arm("send_angles", angles, MOVE_SPEED)
        elif fingers == 4:
            angles = list(self._joints)
            angles[1] = max(-135.0, min(135.0, angles[1] - 15))
            await self._arm("send_angles", angles, MOVE_SPEED)
        elif fingers == 5:
            await self._arm("send_angles", HOME_ANGLES, HOME_SPEED)

    # ── OCR (EasyOCR) ──────────────────────────────────────────────────────────
    async def start_ocr(self) -> None:
        await self._cancel_mode()
        self._mode = "ocr"
        self._mode_task = asyncio.create_task(self._ocr_task())

    async def _ocr_task(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await self._push_log("info", "OCR 모드 준비 중...")
            if not _HAS_EASYOCR:
                await self._push_log("warn", "easyocr 라이브러리가 설치되어 있지 않습니다.")
                self._mode = "idle"
                return

            if self._ocr_reader is None:
                await self._push_log("info", "easyocr 리더 초기화 중 (최초 실행 시 모델 다운로드로 수 초 소요)...")
                self._ocr_reader = await loop.run_in_executor(
                    None, lambda: easyocr.Reader(['ko', 'en'], gpu=False)
                )

            await self._push_log("success", "easyocr 로드 완료!")

            # 1. 카메라 뷰 포지션으로 이동
            if self._has_arm_target():
                await self._arm("send_angles", CAMERA_VIEW_ANGLES, HOME_SPEED)
                await asyncio.sleep(2.5)

            await self._push_log("info", "OCR 텍스트 탐색 시작...")

            print("[ocr] Starting OCR loop...", flush=True)
            while True:
                frame = await loop.run_in_executor(None, self._get_frame)
                if frame is None:
                    print("[ocr] Frame is None, waiting...", flush=True)
                    await asyncio.sleep(0.1)
                    continue

                t0 = time.time()
                results = await loop.run_in_executor(
                    None, lambda: self._ocr_reader.readtext(frame)
                )
                t1 = time.time()
                print(f"[ocr] Scan completed in {t1-t0:.2f}s, found {len(results) if results else 0} results. Raw: {results}", flush=True)

                if results:
                    detection_results = []
                    log_texts = []
                    for bbox, text, conf in results:
                        if conf > 0.3:
                            bbox_list = [[float(pt[0]), float(pt[1])] for pt in bbox]
                            detection_results.append({
                                "text": text,
                                "bbox": bbox_list,
                                "conf": float(conf)
                            })
                            log_texts.append(f'"{text}"({conf:.2f})')

                    if detection_results:
                        self._detection = {"results": detection_results}
                        log_msg = ", ".join(log_texts[:3])
                        if len(log_texts) > 3:
                            log_msg += f" 외 {len(log_texts) - 3}개"
                        await self._push_log("success", f"감지: {log_msg}")

                await asyncio.sleep(0.3)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self._push_log("error", f"OCR 실행 오류: {e}")
        finally:
            self._mode = "idle"
            self._detection = None
            await self._push_log("info", "OCR 모드 중지")

    # ── Playback (Motion Teaching & Playback) ──────────────────────────────────
    async def start_playback(self, waypoints: list[dict]) -> None:
        await self._cancel_mode()
        self._mode = "playback"
        self._mode_task = asyncio.create_task(self._playback_task(waypoints))

    async def _playback_task(self, waypoints: list[dict]) -> None:
        try:
            await self._push_log("info", f"모션 재생 시작 (총 {len(waypoints)}개 웨이포인트)...")
            
            for idx, wp in enumerate(waypoints):
                angles = wp.get("angles")
                gripper = wp.get("gripper")
                speed = wp.get("speed", 20)
                delay = wp.get("delay", 1.0)

                await self._push_log("info", f"[{idx + 1}/{len(waypoints)}] 이동 중: angles={angles}, gripper={gripper}, speed={speed}")

                if self._has_arm_target():
                    if angles and len(angles) >= 6:
                        await self._arm("send_angles", angles, speed)
                        await asyncio.sleep(1.5)
                    if gripper is not None:
                        await self._arm("set_gripper_value", float(gripper), speed)
                        await asyncio.sleep(1.0)
                else:
                    # Demo 모드 시뮬레이션
                    if angles and len(angles) >= 6:
                        self._joints = [float(a) for a in angles[:6]]
                    if gripper is not None:
                        self._gripper = float(gripper)
                    await asyncio.sleep(1.5)

                await asyncio.sleep(delay)

            await self._push_log("success", "모션 재생 완료!")
        except asyncio.CancelledError:
            await self._push_log("warn", "모션 재생 취소됨")
        except Exception as e:
            await self._push_log("error", f"모션 재생 중 오류 발생: {e}")
        finally:
            self._mode = "idle"
            await self._push_state()

    async def cmd_set_angles(self, angles: list[float], speed: int = 20) -> None:
        await self._cancel_mode()
        self._mode = "homing"
        if self._has_arm_target():
            await self._arm("send_angles", angles, speed)
            await asyncio.sleep(0.3)
        self._mode = "idle"

    async def cmd_set_gripper(self, value: int, speed: int = 20) -> None:
        if self._has_arm_target():
            await self._arm("set_gripper_value", value, speed)

    async def stop_mode(self) -> None:
        await self._cancel_mode()
        await self._push_log("info", "모드 중지")


# ── 싱글톤 ───────────────────────────────────────────────────────────────────
_bridge: ArmBridge | None = None


def get_bridge() -> ArmBridge:
    global _bridge
    if _bridge is None:
        _bridge = ArmBridge()
    return _bridge


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI 라우터  (prefix /api/arm — 인증 없음, 프론트 원본 URL 유지)
# ══════════════════════════════════════════════════════════════════════════════
router = APIRouter(prefix="/api/arm", tags=["arm"])


class ColorPickReq(BaseModel):
    color: Literal["red", "green", "blue", "yellow"] = "red"


@router.get("/state")
async def get_arm_state() -> dict:
    bridge = get_bridge()
    return {
        "connected": bridge._connected,
        "mode": bridge._mode,
        "joints": bridge._joints,
        "gripper": bridge._gripper,
        "detection": bridge._detection,
    }


def _is_local_ip(ip: str) -> bool:
    import socket as _socket
    try:
        local_ips = {"127.0.0.1", "127.0.1.1", "localhost", "::1"}
        local_ips.update(_socket.gethostbyname_ex(_socket.gethostname())[2])
        # hostname -I style: all interface addresses
        import subprocess
        out = subprocess.check_output(["hostname", "-I"], text=True).strip()
        local_ips.update(out.split())
        return ip in local_ips
    except Exception:
        return False


def _apply_robot_ip(bridge: ArmBridge, robot_ip: str | None) -> None:
    """robot_ip가 이 서버 자신의 IP면 로컬(_mc) 모드, 외부 IP면 원격 프록시 모드로 설정.

    로봇팔이 AI 서버와 같은 머신에 직접 연결된 경우(robot_ip == 자기 IP)
    원격 모드로 켜면 명령이 자기 자신으로 프록시되어 모드 태스크가 스스로 취소된다.
    WS 핸들러와 동일한 가드를 모든 AI 모드 진입점에 적용한다.
    """
    if robot_ip and not _is_local_ip(robot_ip):
        bridge.set_remote_robot(robot_ip)
    else:
        bridge.set_remote_robot(None)


@router.websocket("/ws/arm")
async def arm_ws(websocket: WebSocket) -> None:
    """
    Server → Client: {type: "state"|"camera"|"log", ...}
    Client → Server: {action: "home"|"stop"}
    """
    robot_ip = websocket.query_params.get("robot_ip")
    bridge = get_bridge()
    _apply_robot_ip(bridge, robot_ip)
    await websocket.accept()
    await bridge.ensure_connected()
    await bridge.add_client(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action == "home":
                asyncio.create_task(bridge.cmd_home())
            elif action == "stop":
                asyncio.create_task(bridge.cmd_stop())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await bridge.remove_client(websocket)


@router.websocket("/ws/robot-camera")
async def robot_camera_ws(websocket: WebSocket) -> None:
    """로봇팔에서 카메라 프레임(JPEG bytes)을 받는 엔드포인트."""
    await websocket.accept()
    bridge = get_bridge()
    try:
        while True:
            data = await websocket.receive_bytes()
            bridge._robot_frame = data
    except (WebSocketDisconnect, Exception):
        bridge._robot_frame = None


@router.post("/stop")
async def arm_stop() -> dict:
    await get_bridge().cmd_stop()
    return {"success": True}


@router.post("/home")
async def arm_home() -> dict:
    asyncio.create_task(get_bridge().cmd_home())
    return {"success": True}


class JointReq(BaseModel):
    angles: list[float]   # J1~J6, degrees
    speed: int = 20

class GripperReq(BaseModel):
    value: int            # 0=닫힘 ~ 100=열림
    speed: int = 20

class CameraViewReq(BaseModel):
    preset: int = 1

@router.post("/face-view")
async def face_view() -> dict:
    bridge = get_bridge()
    await bridge._cancel_mode()
    bridge._mode = "homing"
    await bridge._push_log("info", "얼굴 추적 뷰 포지션으로 이동 중...")
    if bridge._has_arm_target():
        asyncio.create_task(bridge._arm("send_angles", FACE_VIEW_ANGLES, HOME_SPEED))
    bridge._mode = "idle"
    return {"success": True}

@router.post("/jog-stop")
async def jog_stop() -> dict:
    asyncio.create_task(get_bridge().cmd_jog_stop())
    return {"success": True}

@router.post("/angles")
async def set_angles(req: JointReq) -> dict:
    asyncio.create_task(get_bridge().cmd_set_angles(req.angles, req.speed))
    return {"success": True}

@router.post("/gripper")
async def set_gripper(req: GripperReq) -> dict:
    asyncio.create_task(get_bridge().cmd_set_gripper(req.value, req.speed))
    return {"success": True}

@router.post("/camera-view")
async def camera_view(req: CameraViewReq, robot_ip: str | None = None) -> dict:
    from app.database import AdminSessionLocal
    from app.models import Robot
    from sqlalchemy import select
    import urllib.request
    import json

    async with AdminSessionLocal() as db:
        if not robot_ip:
            arm_robot = (await db.execute(
                select(Robot).where(Robot.robot_type == "arm", Robot.is_active == True)
            )).scalar_one_or_none()
            if arm_robot:
                robot_ip = arm_robot.ip_address
        else:
            arm_robot = (await db.execute(
                select(Robot).where(Robot.ip_address == robot_ip)
            )).scalar_one_or_none()

    ai_server_url = arm_robot.ai_server_url if arm_robot else None

    if ai_server_url:
        ai_host = ai_server_url.replace("http://", "").replace("https://", "").split(":")[0]
        if not _is_local_ip(ai_host):
            url = f"{ai_server_url.rstrip('/')}/api/arm/camera-view?robot_ip={robot_ip}"
            try:
                payload = json.dumps({"preset": req.preset}).encode("utf-8")
                req_proxy = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req_proxy, timeout=5.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"success": False, "error": f"AI 서버({ai_server_url}) 전달 실패: {e}"}

    bridge = get_bridge()
    _apply_robot_ip(bridge, robot_ip)
    await bridge.cmd_camera_view(req.preset)
    return {"success": True}



@router.post("/color-pick")
async def color_pick(req: ColorPickReq, robot_ip: str | None = None) -> dict:
    bridge = get_bridge()
    _apply_robot_ip(bridge, robot_ip)
    await bridge.start_color_pick(req.color)
    return {"success": True, "color": req.color}


@router.post("/face-track/start")
async def face_track_start(robot_ip: str | None = None) -> dict:
    bridge = get_bridge()
    _apply_robot_ip(bridge, robot_ip)
    await bridge.start_face_track()
    return {"success": True}


@router.post("/face-track/stop")
async def face_track_stop() -> dict:
    await get_bridge().stop_mode()
    return {"success": True}


@router.post("/gesture/start")
async def gesture_start(robot_ip: str | None = None) -> dict:
    bridge = get_bridge()
    _apply_robot_ip(bridge, robot_ip)
    await bridge.start_gesture()
    return {"success": True}


@router.post("/gesture/stop")
async def gesture_stop() -> dict:
    await get_bridge().stop_mode()
    return {"success": True}


@router.post("/ocr/start")
async def ocr_start(robot_ip: str | None = None) -> dict:
    from app.database import AdminSessionLocal
    from app.models import Robot
    from sqlalchemy import select
    import urllib.request
    import json

    async with AdminSessionLocal() as db:
        if not robot_ip:
            arm_robot = (await db.execute(
                select(Robot).where(Robot.robot_type == "arm", Robot.is_active == True)
            )).scalar_one_or_none()
            if arm_robot:
                robot_ip = arm_robot.ip_address
        else:
            arm_robot = (await db.execute(
                select(Robot).where(Robot.ip_address == robot_ip)
            )).scalar_one_or_none()

    ai_server_url = arm_robot.ai_server_url if arm_robot else None

    if ai_server_url:
        ai_host = ai_server_url.replace("http://", "").replace("https://", "").split(":")[0]
        if not _is_local_ip(ai_host):
            url = f"{ai_server_url.rstrip('/')}/api/arm/ocr/start?robot_ip={robot_ip}"
            try:
                req = urllib.request.Request(url, method="POST")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"success": False, "error": f"AI 서버({ai_server_url}) 전달 실패: {e}"}

    bridge = get_bridge()
    _apply_robot_ip(bridge, robot_ip)
    await bridge.start_ocr()
    return {"success": True}


@router.post("/ocr/stop")
async def ocr_stop(robot_ip: str | None = None) -> dict:
    from app.database import AdminSessionLocal
    from app.models import Robot
    from sqlalchemy import select
    import urllib.request
    import json

    async with AdminSessionLocal() as db:
        if not robot_ip:
            arm_robot = (await db.execute(
                select(Robot).where(Robot.robot_type == "arm", Robot.is_active == True)
            )).scalar_one_or_none()
            if arm_robot:
                robot_ip = arm_robot.ip_address
        else:
            arm_robot = (await db.execute(
                select(Robot).where(Robot.ip_address == robot_ip)
            )).scalar_one_or_none()

    ai_server_url = arm_robot.ai_server_url if arm_robot else None

    if ai_server_url:
        ai_host = ai_server_url.replace("http://", "").replace("https://", "").split(":")[0]
        if not _is_local_ip(ai_host):
            url = f"{ai_server_url.rstrip('/')}/api/arm/ocr/stop?robot_ip={robot_ip}"
            try:
                req = urllib.request.Request(url, method="POST")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"success": False, "error": f"AI 서버({ai_server_url}) 정지 명령 전달 실패: {e}"}

    await get_bridge().stop_mode()
    return {"success": True}


# ── 모션 티칭 및 재생 (Teaching & Playback) ───────────────────────────────────

class WaypointItem(BaseModel):
    angles: list[float]
    gripper: float
    speed: int = 20
    delay: float = 1.0

class MotionSequenceCreateReq(BaseModel):
    name: str
    description: str | None = None
    waypoints: list[WaypointItem]

@router.get("/sequences")
async def get_sequences(db: AsyncSession = Depends(get_robot_db)) -> list[dict]:
    result = await db.execute(select(MotionSequence).order_by(MotionSequence.created_at.desc()))
    seqs = result.scalars().all()
    out = []
    for s in seqs:
        try:
            wps = json.loads(s.waypoints)
        except Exception:
            wps = []
        out.append({
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "waypoints": wps,
            "created_at": iso_utc(s.created_at),
            "updated_at": iso_utc(s.updated_at),
        })
    return out

@router.post("/sequences")
async def save_sequence(req: MotionSequenceCreateReq, db: AsyncSession = Depends(get_robot_db)) -> dict:
    wps_json = json.dumps([wp.model_dump() for wp in req.waypoints])
    
    result = await db.execute(select(MotionSequence).where(MotionSequence.name == req.name))
    existing = result.scalar_one_or_none()
    if existing:
        existing.description = req.description
        existing.waypoints = wps_json
    else:
        new_seq = MotionSequence(
            name=req.name,
            description=req.description,
            waypoints=wps_json
        )
        db.add(new_seq)
    
    await db.commit()
    return {"success": True}

@router.delete("/sequences/{seq_id}")
async def delete_sequence(seq_id: int, db: AsyncSession = Depends(get_robot_db)) -> dict:
    result = await db.execute(select(MotionSequence).where(MotionSequence.id == seq_id))
    seq = result.scalar_one_or_none()
    if not seq:
        return {"success": False, "error": "Sequence not found"}
    await db.delete(seq)
    await db.commit()
    return {"success": True}

@router.post("/sequences/{seq_id}/playback")
async def playback_saved_sequence(seq_id: int, db: AsyncSession = Depends(get_robot_db)) -> dict:
    result = await db.execute(select(MotionSequence).where(MotionSequence.id == seq_id))
    seq = result.scalar_one_or_none()
    if not seq:
        return {"success": False, "error": "Sequence not found"}
    
    try:
        waypoints = json.loads(seq.waypoints)
    except Exception as e:
        return {"success": False, "error": f"Failed to parse waypoints: {e}"}
        
    bridge = get_bridge()
    await bridge.start_playback(waypoints)
    return {"success": True}

@router.post("/playback/preview")
async def playback_preview(waypoints: list[WaypointItem]) -> dict:
    bridge = get_bridge()
    wps_list = [wp.model_dump() for wp in waypoints]
    await bridge.start_playback(wps_list)
    return {"success": True}


