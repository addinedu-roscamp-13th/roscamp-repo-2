"""
아르코(ArUco) 마커 도킹 정렬 라우터.

카메라로 아르코 마커를 검출해 로봇을 마커 정면·일정 거리로 미세 조정(도킹)한다.
차동구동(핑키)의 모터 제어는 drive.py 의 프리미티브를 재사용한다.

GET  /api/robot/dock/detect   — 현재 프레임의 마커 검출 결과 (모터 미동작, 튜닝/확인용)
POST /api/robot/dock/start    — 도킹 제어 루프 시작
POST /api/robot/dock/stop     — 도킹 중지 (모터 정지)
GET  /api/robot/dock/status   — 현재 상태/텔레메트리

캘리브레이션(카메라 내부파라미터 + 마커 실제크기)이 있으면 solvePnP 로
거리를 미터 단위로 제어하고, 없으면 화면 내 마커 픽셀 크기를 거리 대용치로 쓴다.
"""
from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_current_admin
from app.hardware.camera_stream import camera
# 모터 프리미티브 재사용 (별도 데몬을 새로 만들지 않는다)
from app.routers.drive import _motor_send, _vel_to_speeds, _read_dist, _read_ir

router = APIRouter(prefix="/api/robot/dock", tags=["aruco-dock"])

# ── 아르코 사전(dictionary) 이름 → OpenCV 상수 ────────────────────────────────
_ARUCO_DICTS: dict[str, int] = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_6X6_100": cv2.aruco.DICT_6X6_100,
    "DICT_7X7_50": cv2.aruco.DICT_7X7_50,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}

# 선택적 카메라 캘리브레이션 파일 (numpy .npz: camera_matrix, dist_coeffs).
# scripts/calibrate_camera.py 로 생성. 없으면 픽셀 기반으로 동작.
_CALIB_PATH = Path(__file__).parent.parent.parent / "config" / "camera_calib.npz"


def _load_calib() -> tuple[np.ndarray, np.ndarray] | None:
    if not _CALIB_PATH.exists():
        return None
    try:
        data = np.load(str(_CALIB_PATH))
        return data["camera_matrix"], data["dist_coeffs"]
    except Exception:
        return None


def _detector(dict_name: str) -> "cv2.aruco.ArucoDetector":
    key = dict_name if dict_name in _ARUCO_DICTS else "DICT_4X4_50"
    dictionary = cv2.aruco.getPredefinedDictionary(_ARUCO_DICTS[key])
    params = cv2.aruco.DetectorParameters()
    return cv2.aruco.ArucoDetector(dictionary, params)


def _grab_frame() -> np.ndarray | None:
    """카메라 싱글턴에서 최신 JPEG 프레임을 BGR ndarray 로 디코드."""
    jpeg = camera.get_jpeg()
    if not jpeg:
        return None
    arr = np.frombuffer(jpeg, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _measure(corners: np.ndarray, w: int, h: int) -> dict[str, float]:
    """마커 코너(TL,TR,BR,BL)로부터 화면상의 기하 지표를 계산 (기하학적 정렬로 flip/회전 대응)."""
    pts = corners.reshape(4, 2).astype(np.float32)
    # X 좌표 기준 정렬하여 좌측 2개와 우측 2개 포인트 분류
    idx_x = np.argsort(pts[:, 0])
    left_pts = pts[idx_x[:2], :]
    right_pts = pts[idx_x[2:], :]
    
    # 좌측 포인트 중 Y가 작은 것이 TL, 큰 것이 BL
    tl = left_pts[np.argmin(left_pts[:, 1]), :]
    bl = left_pts[np.argmax(left_pts[:, 1]), :]
    
    # 우측 포인트 중 Y가 작은 것이 TR, 큰 것이 BR
    tr = right_pts[np.argmin(right_pts[:, 1]), :]
    br = right_pts[np.argmax(right_pts[:, 1]), :]

    cx = float(pts[:, 0].mean())
    cy = float(pts[:, 1].mean())
    top = np.linalg.norm(tr - tl)
    bottom = np.linalg.norm(br - bl)
    left = np.linalg.norm(bl - tl)
    right = np.linalg.norm(br - tr)
    side = float((top + bottom + left + right) / 4.0)
    ex = (cx - w / 2.0) / (w / 2.0)
    size_frac = side / float(w)
    denom = left + right
    skew = float((right - left) / denom) if denom > 1e-6 else 0.0
    return {"cx": cx, "cy": cy, "ex": ex, "size_frac": size_frac, "skew": skew, "side_px": side}


def _pose(corners: np.ndarray, calib: tuple[np.ndarray, np.ndarray], marker_len_m: float, h: int | None = None):
    """solvePnP 로 마커까지의 거리(z, m)·좌우오프셋(x, m)·정면각(yaw, deg) 추정."""
    K, dist = calib
    s = marker_len_m / 2.0
    obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float32)
    img = corners.reshape(4, 2).astype(np.float32)
    if h is not None:
        img = img.copy()
        img[:, 1] = h - img[:, 1]
        img = img[[3, 2, 1, 0], :]
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        return None
    R, _ = cv2.Rodrigues(rvec)
    yaw = float(np.degrees(np.arctan2(-R[2, 0], np.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))))
    return {"x_m": float(tvec[0]), "z_m": float(tvec[2]), "yaw_deg": yaw}


# ── 색 판별: 마커 주변 pad 색을 한국어 이름으로 추정 ───────────────────────────
def _hue_to_name(hue: int) -> str:
    """OpenCV Hue(0~179) → 한국어 색 이름."""
    if hue < 10 or hue >= 160:
        return "빨강"
    if hue < 20:
        return "주황"
    if hue < 33:
        return "노랑"
    if hue < 78:
        return "초록"
    if hue < 100:
        return "청록"
    if hue < 130:
        return "파랑"
    if hue < 150:
        return "보라"
    return "분홍"


def _region_color(frame: np.ndarray, corners: np.ndarray) -> dict[str, Any] | None:
    """마커 주변(확장 ROI)의 대표 색을 추정한다.

    아르코 마커 자체는 흑백이라, 마커 bbox 를 약 60% 확장한 영역에서 채도 높은
    화소만 골라 우세 색상(Hue)을 뽑는다 → 주차면/라벨의 색을 잡는다.
    """
    h, w = frame.shape[:2]
    pts = corners.reshape(4, 2).astype(np.float32)
    x0, y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
    x1, y1 = float(pts[:, 0].max()), float(pts[:, 1].max())
    side = max(x1 - x0, y1 - y0, 1.0)
    mgn = side * 0.6
    rx0, ry0 = int(max(0, x0 - mgn)), int(max(0, y0 - mgn))
    rx1, ry1 = int(min(w, x1 + mgn)), int(min(h, y1 + mgn))
    if rx1 - rx0 < 3 or ry1 - ry0 < 3:
        return None
    roi = frame[ry0:ry1, rx0:rx1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    sat_mask = (S > 70) & (V > 40) & (V < 245)
    total = int(roi.shape[0] * roi.shape[1])
    n = int(sat_mask.sum())
    if total == 0:
        return None
    ratio = round(n / total, 3)
    if n < max(30, total * 0.04):  # 채도 높은 화소가 적음 → 무채색
        meanv = float(V.mean())
        name = "흰색" if meanv > 180 else ("검정" if meanv < 60 else "회색")
        return {"name": name, "chromatic": False, "ratio": ratio}
    hist = np.bincount(H[sat_mask].astype(np.int64), minlength=180)
    dom = int(hist.argmax())
    return {"name": _hue_to_name(dom), "chromatic": True, "hue": dom,
            "sat": int(S[sat_mask].mean()), "ratio": ratio}


def _detect_in_frame(frame: np.ndarray, dict_name: str, marker_len_m: float | None,
                     with_color: bool = False) -> dict[str, Any]:
    """주어진 BGR 프레임에서 마커(+선택적으로 주변 색)를 검출한다."""
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detector(dict_name).detectMarkers(gray)
    calib = _load_calib()
    markers: list[dict[str, Any]] = []
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            m: dict[str, Any] = {"id": int(i), **_measure(c, w, h)}
            if calib is not None and marker_len_m:
                p = _pose(c, calib, marker_len_m, h)
                if p:
                    m["pose"] = p
            if with_color:
                m["color"] = _region_color(frame, c)
            markers.append(m)
    markers.sort(key=lambda m: m["size_frac"], reverse=True)  # 큰(가까운) 마커 우선
    return {"frame": {"width": w, "height": h}, "calibrated": calib is not None, "markers": markers}


def _detect(dict_name: str, marker_len_m: float | None, with_color: bool = False) -> dict[str, Any]:
    frame = _grab_frame()
    if frame is None:
        raise HTTPException(503, "카메라 프레임 없음 — 먼저 카메라 스트림을 시작하세요.")
    return _detect_in_frame(frame, dict_name, marker_len_m, with_color)


def detect_from_jpeg(jpeg: bytes, dict_name: str = "DICT_4X4_50",
                     marker_len_m: float | None = None, with_color: bool = True) -> dict[str, Any] | None:
    """JPEG 바이트(로봇 스냅샷)에서 마커+색을 검출한다. 중앙 서버가 로봇 프레임을 받아 쓴다."""
    arr = np.frombuffer(jpeg, np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return None
    return _detect_in_frame(frame, dict_name, marker_len_m, with_color)


# ── 도킹 설정/상태 ────────────────────────────────────────────────────────────
class DockConfig(BaseModel):
    dictionary: str = "DICT_4X4_50"
    marker_id: int | None = None            # None 이면 가장 큰(가까운) 마커
    marker_len_m: float | None = None       # 캘리브레이션 있을 때 pose 계산용
    target_size: float = Field(0.40, ge=0.05, le=0.95)   # 픽셀 모드 정지 크기(변/너비)
    target_distance_m: float | None = None  # pose 모드 정지 거리(m)
    center_tol: float = Field(0.06, ge=0.01, le=0.3)     # 중앙 정렬 허용 오차(ex)
    size_tol: float = Field(0.04, ge=0.01, le=0.3)
    dist_tol_m: float = Field(0.03, ge=0.005, le=0.5)
    kp_ang: float = 0.9
    kp_lin: float = 1.2
    ang_max: float = Field(0.35, ge=0.05, le=1.0)        # 정규화 각속도 상한
    lin_max: float = Field(0.25, ge=0.05, le=1.0)        # 정규화 선속도 상한
    approach: str = "front"                               # front=전진 접근, rear=후진 접근
    use_wall_sensor: bool = True                          # 최종 정지는 초음파/IR 벽 센서를 우선 사용
    target_wall_cm: float = Field(8.0, ge=3.0, le=80.0)   # 벽/도킹면 목표 거리
    wall_tol_cm: float = Field(1.5, ge=0.5, le=20.0)
    slow_wall_cm: float = Field(25.0, ge=5.0, le=120.0)   # 이 거리 안쪽에서는 더 천천히 접근
    stop_on_ir: bool = True
    rear_turn_secs: float = Field(2.2, ge=0.0, le=15.0)  # 후면 주차: 접근 후 제자리 회전(≈180°) 시간(초, 실기 튜닝)
    rear_turn_dir: float = Field(1.0, ge=-1.0, le=1.0)   # 회전 방향(+1 좌회전, -1 우회전)
    min_drive: int = Field(22, ge=0, le=60)              # 데드밴드 보정: 움직이려 할 때 보장할 최소 모터%(정지마찰 극복)
    lost_grace: int = Field(8, ge=1, le=60)              # 마커 상실 허용 프레임
    search: bool = True                                   # 상실 시 제자리 회전 재탐색
    perp_approach: bool = True                              # skew 기반 수직 접근 보정 on/off
    skew_kp: float = Field(0.35, ge=0.0, le=2.0)            # skew → 보정 각속도 이득
    skew_deadband: float = Field(0.06, ge=0.0, le=0.3)      # 이보다 작은 skew는 정면으로 간주(무시)
    pose_perp_kp_yaw: float = Field(1.0, ge=0.0, le=5.0)    # heading 오차 이득
    pose_perp_kp_lat: float = Field(1.5, ge=0.0, le=5.0)    # lateral 오차 이득
    loop_hz: float = Field(12.0, ge=2.0, le=30.0)
    timeout_s: float = Field(60.0, ge=2.0, le=600.0)


_task: asyncio.Task | None = None
_state: dict[str, Any] = {"running": False, "phase": "idle", "message": "", "telemetry": {}}


async def _rotate_in_place(cfg: DockConfig, secs: float, direction: float) -> None:
    """제자리 회전 (오도메트리/IMU 없이 시간 기반). 후면 주차의 ≈180° 방향 전환용.

    각도 정밀도가 필요하면 rear_turn_secs 를 실기에서 튜닝하거나, 이후 odom/IMU 기반으로 교체.
    """
    if secs <= 0:
        return
    dt = 1.0 / cfg.loop_hz
    turn = (1.0 if direction >= 0 else -1.0) * cfg.ang_max
    l, r = _vel_to_speeds(0.0, turn)
    peak = max(abs(l), abs(r))                            # 데드밴드 보정
    if 0 < peak < cfg.min_drive:
        scale = cfg.min_drive / peak
        l, r = int(round(l * scale)), int(round(r * scale))
    end = time.time() + secs
    while time.time() < end:
        await _motor_send(l, r)
        await asyncio.sleep(dt)
    await _motor_send(0, 0)


async def _dock_loop(cfg: DockConfig) -> None:
    dt = 1.0 / cfg.loop_hz
    detector = _detector(cfg.dictionary)
    calib = _load_calib()
    use_pose = calib is not None and cfg.marker_len_m and cfg.target_distance_m is not None
    started = time.time()
    lost = 0
    search_dir = 1.0
    try:
        while True:
            if time.time() - started > cfg.timeout_s:
                _state.update(phase="timeout", message="시간 초과로 중단")
                break

            frame = _grab_frame()
            if frame is None:
                _state.update(phase="no_frame", message="카메라 프레임 없음")
                await _motor_send(0, 0)
                await asyncio.sleep(dt)
                continue

            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = detector.detectMarkers(gray)

            target = None
            if ids is not None:
                pairs = list(zip(corners, ids.flatten()))
                if cfg.marker_id is not None:
                    pairs = [p for p in pairs if int(p[1]) == cfg.marker_id]
                if pairs:
                    target = max(pairs, key=lambda p: _measure(p[0], w, h)["size_frac"])

            # ── 마커 상실 처리 ──
            if target is None:
                lost += 1
                if lost <= cfg.lost_grace:
                    await _motor_send(0, 0)
                elif cfg.search:
                    _state.update(phase="search", message="마커 재탐색 중")
                    l, r = _vel_to_speeds(0.0, search_dir * min(0.25, cfg.ang_max))
                    await _motor_send(l, r)
                else:
                    _state.update(phase="lost", message="마커 상실 — 중단")
                    break
                await asyncio.sleep(dt)
                continue
            lost = 0
            search_dir = 1.0 if _measure(target[0], w, h)["ex"] >= 0 else -1.0

            m = _measure(target[0], w, h)
            ex = m["ex"]

            # ── 거리 제어(주 제어): ArUco 마커 기준. pose(미터) 우선, 없으면 마커 크기 ──
            if use_pose:
                p = _pose(target[0], calib, cfg.marker_len_m, h)
                if p is None:
                    await _motor_send(0, 0)
                    await asyncio.sleep(dt)
                    continue
                dist_source = "pose_m"
                dist_err = p["z_m"] - cfg.target_distance_m       # +면 너무 멀다
                dist_ok = abs(dist_err) <= cfg.dist_tol_m
                lin_raw = cfg.kp_lin * dist_err
                tele_dist = round(p["z_m"], 3)
            else:
                dist_source = "marker_size"
                dist_err = cfg.target_size - m["size_frac"]        # +면 너무 멀다(작다)
                dist_ok = abs(dist_err) <= cfg.size_tol
                lin_raw = cfg.kp_lin * dist_err                    # +면 전진, -면 후진(마커에서 멀어짐)
                tele_dist = round(m["size_frac"], 3)

            center_ok = abs(ex) <= cfg.center_tol

            # ── 각/선속도: 마커 정면 수직 직선 정렬을 동시 수행하며 접근 ──
            if use_pose:
                # ── 보정(Pose) 기반 정밀 수직(직선) 접근 ──
                theta = math.radians(p["yaw_deg"])
                X_M = p["z_m"] * math.sin(theta) - p["x_m"] * math.cos(theta)
                phi_des = max(-0.6, min(0.6, cfg.pose_perp_kp_lat * X_M))
                
                ang_limit = cfg.ang_max if cfg.ang_max > 0 else 0.35
                angular = max(-ang_limit, min(ang_limit, cfg.pose_perp_kp_yaw * (theta + phi_des)))
                
                linear_scale = max(0.2, min(1.0, 1.0 - abs(theta + phi_des) / 0.6))
                linear = max(-cfg.lin_max, min(cfg.lin_max, lin_raw)) * linear_scale
            else:
                # ── 무보정(skew) 기반 수직(직선) 접근 ──
                sk = m["skew"]
                if cfg.perp_approach and abs(sk) > cfg.skew_deadband:
                    ang_limit = cfg.ang_max if cfg.ang_max > 0 else 0.35
                    angular = - cfg.kp_ang * ex - cfg.skew_kp * sk
                    angular = max(-ang_limit, min(ang_limit, angular))
                    
                    linear_scale = max(0.2, min(1.0, 1.0 - abs(ex) / 0.6 - abs(sk) / 0.4))
                    linear = max(-cfg.lin_max, min(cfg.lin_max, lin_raw)) * linear_scale
                else:
                    ang_limit = cfg.ang_max if cfg.ang_max > 0 else 0.35
                    angular = max(-ang_limit, min(ang_limit, - cfg.kp_ang * ex))
                    linear = max(-cfg.lin_max, min(cfg.lin_max, lin_raw))

            # ── 벽/IR 센서: 전진 접근(linear>0) 중에만 충돌 안전 정지로 사용 (마커 제어를 덮어쓰지 않음) ──
            wall_cm = None
            ir_hit = False
            safety_stop = False
            if cfg.use_wall_sensor and linear > 0:
                wall_cm = await _read_dist()
                if wall_cm is not None:
                    if wall_cm <= cfg.slow_wall_cm:
                        linear = min(linear, cfg.lin_max * 0.45)   # 벽 근처 감속
                    if wall_cm <= cfg.target_wall_cm:
                        safety_stop = True
                if cfg.stop_on_ir:
                    ir = await _read_ir()
                    ir_hit = bool(ir and (ir.get("left") or ir.get("center") or ir.get("right")))
                    if ir_hit:
                        safety_stop = True

            # ── 성공(마커 목표 도달) 또는 전방 안전 정지 ──
            if (center_ok and dist_ok) or safety_stop:
                await _motor_send(0, 0)
                done_by_marker = center_ok and dist_ok
                # 후면 주차: 마커 정면으로 목표 거리까지 붙은 뒤 제자리 180° 회전 → 후면이 도킹면을 향함
                if cfg.approach == "rear" and done_by_marker:
                    _state.update(phase="turning", message="후면 정렬: 제자리 회전 중")
                    await _rotate_in_place(cfg, cfg.rear_turn_secs, cfg.rear_turn_dir)
                    _state.update(phase="done", message="후면 주차 완료")
                else:
                    _state.update(
                        phase="done",
                        message="도킹 완료" if done_by_marker else "벽/IR 센서 안전 정지",
                    )
                break

            l, r = _vel_to_speeds(linear, angular)
            # ── 데드밴드 보정: 움직이려는 의도가 있으면 실제로 굴러갈 최소 속도를 보장(방향비 유지) ──
            peak = max(abs(l), abs(r))
            if 0 < peak < cfg.min_drive:
                scale = cfg.min_drive / peak
                l = int(round(l * scale))
                r = int(round(r * scale))
            await _motor_send(l, r)

            _state["phase"] = "docking"
            _state["telemetry"] = {
                "id": int(target[1]), "ex": round(ex, 3), "dist": tele_dist,
                "dist_source": dist_source, "skew": round(m["skew"], 3),
                "linear": round(linear, 3), "angular": round(angular, 3),
                "left": l, "right": r, "center_ok": center_ok,
                "dist_ok": dist_ok, "wall_cm": wall_cm,
                "ir_hit": ir_hit, "approach": cfg.approach, "pose": use_pose,
            }
            await asyncio.sleep(dt)
    except asyncio.CancelledError:
        _state.update(phase="stopped", message="사용자 중지")
        raise
    except Exception as exc:  # noqa: BLE001
        _state.update(phase="error", message=f"오류: {exc}")
    finally:
        await _motor_send(0, 0)
        _state["running"] = False


@router.get("/detect")
async def detect(
    dictionary: str = "DICT_4X4_50",
    marker_len_m: float | None = None,
    _=Depends(get_current_admin),
):
    """현재 프레임에서 아르코 마커를 검출해 지표를 반환 (모터 미동작)."""
    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.3)
    return _detect(dictionary, marker_len_m)


@router.post("/start")
async def start(cfg: DockConfig, _=Depends(get_current_admin)):
    global _task
    if _task is not None and not _task.done():
        raise HTTPException(409, "이미 도킹이 진행 중입니다.")
    if cfg.dictionary not in _ARUCO_DICTS:
        raise HTTPException(400, f"지원하지 않는 dictionary: {cfg.dictionary}")
    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.3)
    _state.update(running=True, phase="starting", message="", telemetry={})
    _task = asyncio.create_task(_dock_loop(cfg))
    return {"success": True, "message": "도킹 시작", "config": cfg.model_dump()}


@router.post("/stop")
async def stop(_=Depends(get_current_admin)):
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    await _motor_send(0, 0)
    _state.update(running=False, phase="idle", message="정지됨")
    return {"success": True, "message": "도킹 정지"}


@router.get("/status")
async def status(_=Depends(get_current_admin)):
    return _state
