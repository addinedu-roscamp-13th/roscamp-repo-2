"""
아르코(ArUco) 마커 도킹 정렬 라우터 — robot_agent(로봇 온보드) 용.

로봇 카메라로 마커를 검출해 로봇을 마커 정면·일정 거리로 미세 조정(도킹)한다.
모터 제어는 driving.py 의 프리미티브를 재사용한다. (에이전트 내부용 — 인증 없음)

경로(server.py 에서 /api/admin/robot 로 장착):
  GET  /api/admin/robot/dock/detect   — 현재 프레임 검출 결과 (모터 미동작)
  POST /api/admin/robot/dock/start    — 도킹 루프 시작
  POST /api/admin/robot/dock/stop     — 정지(모터 0)
  GET  /api/admin/robot/dock/status   — 상태/텔레메트리

OpenCV 4.6(구 API) / 4.7+(신 API) 양쪽에서 동작하도록 shim 을 둔다.
config/camera_calib.npz (camera_matrix, dist_coeffs) 가 있고 marker_len_m/
target_distance_m 를 주면 pose(미터) 제어, 없으면 픽셀 크기 기반 제어.
"""
from __future__ import annotations

import asyncio
import math
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.hardware.camera_stream import camera
# 로봇(robot_agent) 온보드 전용 — 로봇의 driving.py 프리미티브를 사용한다.
# (이 저장소 backend 의 drive.py 와는 다름 — 로컬 main.py 에는 마운트되지 않는다.)
from app.routers.driving import _motor_send, _vel_to_speeds, _read_dist

router = APIRouter()

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

_CALIB_PATH = Path(__file__).parent.parent.parent / "config" / "camera_calib.npz"

# 모터 정지 마찰 극복용 최소 구동값 — 이보다 작은(0 아닌) 명령은 이 값까지 끌어올린다.
_MIN_DRIVE = 32

# 조향 부호: ex(화면 좌우 오차, +는 오른쪽) → 마커 쪽으로 붙는 angular 의 부호.
# 기구학 유도(2026-07-06): _vel_to_speeds 는 right=(lin+ang)·S 이므로 ang>0 이면 좌회전.
# 카메라는 좌우반전 없음(CAMERA_FLIP=none) → ex>0(마커 오른쪽) 현장 실측상 왼쪽 벽으로 흐르는 오보정 발생 → 부호 +1.
# (2026-07-06 현장 재검증: -1은 가이드선 반대쪽, 왼쪽 벽으로 감)
_STEER_SIGN = 1.0


def _scale_min_drive(left: int, right: int, min_drive: int) -> tuple[int, int]:
    peak = max(abs(left), abs(right))
    if 0 < peak < min_drive:
        scale = min_drive / peak
        return int(round(left * scale)), int(round(right * scale))
    return left, right


def _boost(v: int) -> int:
    if v == 0:
        return 0
    return int(math.copysign(max(abs(v), _MIN_DRIVE), v))


# ── OpenCV 버전 호환 shim ─────────────────────────────────────────────────────
def _get_dictionary(dict_id: int):
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        return cv2.aruco.getPredefinedDictionary(dict_id)   # 4.7+
    return cv2.aruco.Dictionary_get(dict_id)                # 4.6


def _get_params():
    p = (cv2.aruco.DetectorParameters_create()
         if hasattr(cv2.aruco, "DetectorParameters_create")
         else cv2.aruco.DetectorParameters())
    # 작은/가장자리/흐릿한 마커 검출률 향상
    p.adaptiveThreshWinSizeMin = 3
    p.adaptiveThreshWinSizeMax = 43
    p.adaptiveThreshWinSizeStep = 6
    p.minMarkerPerimeterRate = 0.02
    p.maxMarkerPerimeterRate = 4.0
    p.polygonalApproxAccuracyRate = 0.06
    try:
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    except Exception:
        pass
    return p


def _detect_markers(gray, dictionary, params):
    if hasattr(cv2.aruco, "ArucoDetector"):                 # 4.7+
        det = cv2.aruco.ArucoDetector(dictionary, params)
        return det.detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, dictionary, parameters=params)  # 4.6


def _detect_markers_robust(gray, dictionary, params):
    """1차 검출 실패 시 2배 확대 후 재검출 → 코너를 원본 스케일로 되돌린다.
    멀리 있는 작은 마커(수십 px)의 검출 끊김을 줄인다."""
    corners, ids, rej = _detect_markers(gray, dictionary, params)
    if ids is None:
        big = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)
        c2, ids2, rej = _detect_markers(big, dictionary, params)
        if ids2 is not None:
            corners = tuple(np.asarray(c, dtype=np.float32) / 2.0 for c in c2)
            ids = ids2
    return corners, ids, rej


def _load_calib() -> tuple[np.ndarray, np.ndarray] | None:
    if not _CALIB_PATH.exists():
        return None
    try:
        data = np.load(str(_CALIB_PATH))
        return data["camera_matrix"], data["dist_coeffs"]
    except Exception:
        return None


def _grab_frame() -> np.ndarray | None:
    jpeg = camera.get_jpeg()
    if not jpeg:
        return None
    arr = np.frombuffer(jpeg, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


async def _read_wall_cm() -> float | None:
    """전방 초음파 거리(cm).

    HC-SR04 는 최소 측정거리(2cm) 안쪽(블라인드 존)에서 음수/초저값을 뱉는다.
    이는 '벽에 초근접'이라는 유효한 신호이므로 버리지 말고 2.0cm 로 클램프한다.
    (순간 스파이크 오탐은 호출부의 연속 2회 디바운스가 걸러낸다)"""
    d = await _read_dist()
    if d is None:
        return None
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    return d if d >= 2.0 else 2.0


def _measure(corners: np.ndarray, w: int, h: int) -> dict[str, float]:
    """마커 코너(TL,TR,BR,BL) → 화면 기하 지표 (기하학적 정렬로 flip/회전 대응)."""
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
    top = float(np.linalg.norm(tr - tl))
    bottom = float(np.linalg.norm(br - bl))
    left = float(np.linalg.norm(bl - tl))
    right = float(np.linalg.norm(br - tr))
    side = (top + bottom + left + right) / 4.0
    ex = (cx - w / 2.0) / (w / 2.0)
    size_frac = side / float(w)
    denom = left + right
    skew = (right - left) / denom if denom > 1e-6 else 0.0
    return {"cx": cx, "cy": cy, "ex": ex, "size_frac": size_frac, "skew": skew, "side_px": side}


def _pose(corners: np.ndarray, calib: tuple[np.ndarray, np.ndarray], marker_len_m: float, h: int | None = None):
    """마커까지 거리(z,m)·좌우오프셋(x,m)·정면각(yaw,deg). 4.6/4.13 모두 지원."""
    K, dist = calib
    img = corners.reshape(4, 2).astype(np.float32)
    if h is not None:
        img = img.copy()
        img[:, 1] = h - img[:, 1]
        img = img[[3, 2, 1, 0], :]
    # 항상 IPPE_SQUARE(평면 사각형 전용 정밀 해법) 사용 — 4.6 의 estimatePoseSingleMarkers
    # (ITERATIVE)는 작은 마커에서 yaw 부호가 튀어 축 접근을 망친다.
    s = marker_len_m / 2.0
    obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float32)
    try:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    except Exception:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist)
    if not ok:
        return None
    rvec = rvec.reshape(3)
    tvec = tvec.reshape(3)
    R, _ = cv2.Rodrigues(rvec)
    yaw = float(np.degrees(np.arctan2(-R[2, 0], np.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))))
    # 마커 법선(정면축) 방향 — 카메라 쪽을 향하도록 부호 통일 (수직 접근 경유점 계산용)
    n = R[:, 2]
    if n[2] > 0:
        n = -n
    return {"x_m": float(tvec[0]), "z_m": float(tvec[2]), "yaw_deg": yaw,
            "nx": float(n[0]), "nz": float(n[2])}


def _detect(dict_name: str, marker_len_m: float | None) -> dict[str, Any]:
    frame = _grab_frame()
    if frame is None:
        raise HTTPException(503, "카메라 프레임 없음 — 카메라 스트림을 시작하세요.")
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    dictionary = _get_dictionary(_ARUCO_DICTS.get(dict_name, cv2.aruco.DICT_4X4_50))
    corners, ids, _ = _detect_markers_robust(gray, dictionary, _get_params())
    calib = _load_calib()
    markers: list[dict[str, Any]] = []
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            m: dict[str, Any] = {"id": int(i), **_measure(c, w, h)}
            if m["side_px"] < 8.0:
                continue  # 서브픽셀급 유령 검출(업스케일 재검출 부산물) 제거
            # [2026-07-16] 카메라가 180° 뒤집혀 장착돼 있다. 이 파일의 제어 계산(_measure 의 ex,
            # _pose 의 y 뒤집기, _STEER_SIGN=+1)은 전부 **raw 프레임 기준**으로 현장 튜닝돼
            # 있으므로 그대로 둔다 — 건드리면 도킹이 깨진다.
            # 대신 화면에 그릴 좌표만 정방향(180° 회전)으로 따로 실어 보낸다. 프론트가
            # <img> 를 rotate(180deg) 로 세우므로 오버레이도 같은 좌표계여야 겹친다.
            # (예전엔 프론트가 scaleY(-1) + y 만 뒤집어 좌우가 거울로 남아 있었다.)
            pts = c.reshape(4, 2)
            m["corners_view"] = [[round(float(w - 1 - px), 1), round(float(h - 1 - py), 1)]
                                 for px, py in pts]
            m["cx_view"] = round(float(w - 1 - pts[:, 0].mean()), 1)
            m["cy_view"] = round(float(h - 1 - pts[:, 1].mean()), 1)
            if calib is not None and marker_len_m:
                p = _pose(c, calib, marker_len_m, h)
                if p:
                    m["pose"] = p
            markers.append(m)
    markers.sort(key=lambda m: m["size_frac"], reverse=True)
    return {
        "frame": {"width": w, "height": h},
        "opencv": cv2.__version__,
        "calibrated": calib is not None,
        "count": len(markers),
        "markers": markers,
    }


class DockConfig(BaseModel):
    dictionary: str = "DICT_4X4_50"
    marker_id: int | None = None
    marker_len_m: float | None = None
    target_size: float = Field(0.40, ge=0.05, le=0.95)
    target_distance_m: float | None = None
    center_tol: float = Field(0.06, ge=0.01, le=0.3)
    size_tol: float = Field(0.04, ge=0.01, le=0.3)
    dist_tol_m: float = Field(0.03, ge=0.005, le=0.5)
    kp_ang: float = 0.9
    kp_lin: float = 1.2
    ang_max: float = Field(0.0, ge=0.0, le=1.0)
    lin_max: float = Field(0.22, ge=0.05, le=1.0)
    approach: str = "front"
    rear_turn_secs: float = Field(2.0, ge=0.0, le=8.0)
    rear_turn_speed: float = Field(0.12, ge=0.03, le=0.4)
    rear_turn_dir: float = Field(1.0, ge=-1.0, le=1.0)
    rear_turn_min_drive: int = Field(24, ge=0, le=60)
    # 벽 센서(전방 초음파 + IR)로 충돌 전 감속·정지 — 마커 크기만으로 밀어붙이지 않게
    use_wall_sensor: bool = True
    target_wall_cm: float = Field(10.0, ge=2.0, le=80.0)   # 벽/도킹면 앞 목표 정지 거리
    slow_wall_cm: float = Field(35.0, ge=5.0, le=150.0)    # 이 거리 안쪽부터 감속
    min_drive: int = Field(22, ge=0, le=70)         # 정지마찰 극복용 최소 모터%
    lost_grace: int = Field(2, ge=0, le=60)
    search: bool = False # 마커 상실 시 임의 회전 탐색 금지: 계산된 segment만 수행
    search_turn_speed: float = Field(0.18, ge=0.03, le=0.4)  # 재탐색 제자리 회전 각속도(느리게)
    search_sweep_s: float = Field(8.0, ge=0.0, le=15.0)      # 이 시간만 회전 재탐색, 이후엔 정지 대기(무한 제자리 회전 금지)
    search_ang: float = Field(0.0, ge=0.0, le=0.3)        # (구) 미사용
    search_pulse_s: float = Field(0.26, ge=0.0, le=1.0)    # 재탐색 회전 펄스 시간
    search_pause_s: float = Field(0.35, ge=0.05, le=3.0)    # 재탐색 프레임 확인 정지 시간
    lost_reacquire_turn_speed: float = Field(0.024, ge=0.0, le=0.2) # 선택 target id 분실 후 마지막 관측 bearing 방향 재획득 회전
    drive_ex_tol: float = Field(0.30, ge=0.02, le=0.5)     # 이 범위 안에서는 직진
    steer_ex_tol: float = Field(0.50, ge=0.05, le=0.9)     # 이보다 벗어나면 감속+강조향
    # ex 조향 PID: angular = -( kp·ex + ki·∫ex + kd·dex/dt ), 상한 steer_ang_max.
    # MAX_SPEED=75 기준 ex=0.5 에서 좌우 바퀴 차이가 뚜렷해지도록 상향(2026-07-06).
    steer_ang_max: float = Field(0.08, ge=0.0, le=0.3)
    steer_kp: float = Field(0.22, ge=0.0, le=1.0)
    steer_ki: float = Field(0.01, ge=0.0, le=0.5)          # 잔류 오차 제거(미세 조정)
    steer_kd: float = Field(0.0, ge=0.0, le=1.0)           # 단순 가이드 추종에서는 D 튐 방지
    steer_deadband: float = Field(0.012, ge=0.0, le=0.1)    # 중앙 무시폭을 작게: 모터 드리프트 즉시 보정
    steer_ang_min: float = Field(0.025, ge=0.0, le=0.1)     # 작은 오차도 실제 바퀴 차이로 나오게 최소 조향
    steer_i_max: float = Field(0.5, ge=0.0, le=2.0)        # 적분 누적 상한(anti-windup)
    # 센서값 LPF(저역통과) — 검출 픽셀 노이즈·초음파 스파이크 방어. 1.0 = 필터 없음.
    ex_lpf_alpha: float = Field(0.45, ge=0.05, le=1.0)
    wall_lpf_alpha: float = Field(0.35, ge=0.05, le=1.0)
    # 측면에 크게 벗어난 마커(steer_ex_tol~align_ex_tol): turn-and-stop 으로 중앙에 넣는다.
    # (연속 회전은 마커를 시야에서 날림 → 널리 쓰이는 '조금 돌고 멈춰 재검출' 방식 채택)
    align_ex_tol: float = Field(0.85, ge=0.3, le=0.98)      # 이보다 더 벗어나면(거의 측/후면) 정지 대기
    align_turn_speed: float = Field(0.14, ge=0.05, le=0.4)  # 측면 정렬용 제자리 회전 각속도
    align_turn_min_drive: int = Field(12, ge=0, le=60)      # 제자리 회전 정지마찰 극복 최소 모터%
    ang_slew: float = Field(0.012, ge=0.005, le=0.3)         # 루프당 각속도 변화 상한(슬루 제한, 급회전 방지)
    turn_pulse_s: float = Field(0.22, ge=0.05, le=1.0)      # 회전 펄스 길이(조금 돈다)
    turn_pause_s: float = Field(0.40, ge=0.1, le=2.0)       # 회전 후 정지·재검출 시간(모션블러 회피)
    # 무보정 수직 접근: 마커 원근왜곡(skew)을 각도 대용으로 써서 정면축으로 곡선 접근.
    perp_approach: bool = False                              # skew 기반 수직 접근 보정 on/off
    skew_kp: float = Field(0.35, ge=0.0, le=2.0)            # skew → 보정 각속도 이득
    perp_ang_max: float = Field(0.05, ge=0.0, le=0.2)       # 수직 보정 각속도 상한(급회전 방지)
    skew_deadband: float = Field(0.06, ge=0.0, le=0.3)      # 이보다 작은 skew는 정면으로 간주(무시)
    skew_check_s: float = Field(1.5, ge=0.3, le=5.0)        # skew 부호 자동학습 판정 주기
    # 보정(Pose) 기반 수직 접근 제어 이득
    pose_perp_kp_yaw: float = Field(1.0, ge=0.0, le=5.0)    # heading 오차 이득 (K_heading)
    pose_perp_kp_lat: float = Field(1.5, ge=0.0, le=5.0)    # lateral 오차 이득 (K_lateral)
    pose_axis_enable: bool = False                            # 마커 법선축(가이드 선) 기반 가상 경유점 추종
    pose_axis_tol_m: float = Field(0.08, ge=0.01, le=0.4)    # 이 이상 축에서 벗어나면 축 합류 우선
    pose_axis_standoff_m: float = Field(0.35, ge=0.12, le=1.2) # 마커 정면축 위 가상 경유점 거리
    pose_axis_bearing_limit_deg: float = Field(22.0, ge=5.0, le=60.0) # 경유점 방위각 제한(FOV 이탈 방지)
    pose_center_enable: bool = True                          # pose x/z로 마커 중심과 카메라 중심선을 일치시키는 주 제어
    pose_center_kp_bearing: float = Field(0.0, ge=0.0, le=3.0) # atan2(x,z) → angular
    pose_center_kp_yaw: float = Field(0.0, ge=0.0, le=1.0)     # yaw 보조 보정(마커 면 정렬용, 작게)
    pose_center_ang_max: float = Field(0.0, ge=0.0, le=0.3)    # pose 중심선 조향 상한
    pose_center_sign: float = Field(-1.0, ge=-1.0, le=1.0)      # raw pose x 부호 보정: 현장 기준 마커 쪽으로 회전
    pose_segment_s: float = Field(0.45, ge=0.1, le=2.0)         # pose 스냅샷으로 계산한 짧은 주행 segment 시간
    lpark_bearing_tol_deg: float = Field(2.0, ge=1.0, le=20.0) # ㄱ자 주차: 이 각도 밖이면 전진 금지, 먼저 회전
    lpark_turn_speed: float = Field(0.024, ge=0.005, le=0.12)  # ㄱ자 주차 회전 속도
    lpark_forward_scale: float = Field(0.28, ge=0.05, le=0.8)  # 정렬 후 짧은 직선 접근 속도 비율
    lpark_x_tol_m: float = Field(0.015, ge=0.002, le=0.08)      # 카메라 중심선-마커 중심 좌우 허용 오차
    # 벽 도달 후 최종 정지 전에 마커 정면으로 헤딩을 맞춘다(초음파 거리만으로 멈추면 옆을 볼 수 있음).
    final_center_tol: float = Field(0.10, ge=0.02, le=0.3)  # 최종 헤딩 정렬 목표(|ex|)
    final_align_s: float = Field(6.0, ge=0.0, le=20.0)      # 최종 정렬 최대 시간(초과 시 그대로 정지)
    move_pulse_s: float = Field(0.10, ge=0.03, le=0.5)     # 아주 조금만 이동
    move_pause_s: float = Field(0.90, ge=0.1, le=3.0)      # 이동 후 정지 확인
    lost_done_wall_cm: float = Field(10.5, ge=2.0, le=80.0) # 마커를 놓쳐도 이 거리 안이면 주차 완료
    lost_crawl_wall_cm: float = Field(20.0, ge=5.0, le=80.0) # 마커 상실 후 초음파만 보고 조금 더 접근
    lost_hold_s: float = Field(0.65, ge=0.0, le=5.0)          # 분실 직후 직전 계산 모터값 유지 시간
    max_lost_s: float = Field(30.0, ge=1.0, le=60.0)        # 이 시간 넘게 못 찾으면 안전 정지
    loop_hz: float = Field(12.0, ge=2.0, le=30.0)
    timeout_s: float = Field(180.0, ge=2.0, le=600.0)
    manage_nav2: bool = True
    stop_camera_on_finish: bool = True


_task: asyncio.Task | None = None
_state: dict[str, Any] = {"running": False, "phase": "idle", "message": "", "telemetry": {}}


def _pm2(action: str, name: str) -> None:
    try:
        subprocess.run(["pm2", action, name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
    except Exception:
        pass


def _enter_low_cpu_mode(cfg: DockConfig) -> None:
    if cfg.manage_nav2:
        _pm2("stop", "nav2")


def _leave_low_cpu_mode(cfg: DockConfig) -> None:
    if cfg.stop_camera_on_finish:
        try:
            camera.stop()
        except Exception:
            pass
    if cfg.manage_nav2:
        _pm2("start", "nav2")


async def _rear_turn_if_needed(cfg: DockConfig) -> None:
    if cfg.approach != "rear" or cfg.rear_turn_secs <= 0:
        return
    _state.update(phase="turning", message="후면 주차: 마지막 몸통 회전")
    turn = (1.0 if cfg.rear_turn_dir >= 0 else -1.0) * cfg.rear_turn_speed
    l, r = _vel_to_speeds(0.0, turn)
    l, r = _scale_min_drive(l, r, cfg.rear_turn_min_drive)
    end = time.time() + cfg.rear_turn_secs
    while time.time() < end:
        await _motor_send(l, r)
        await asyncio.sleep(1.0 / cfg.loop_hz)
    await _motor_send(0, 0)


async def _complete_parking(cfg: DockConfig, message: str, telemetry: dict[str, Any] | None = None) -> None:
    await _motor_send(0, 0)
    final_telemetry = telemetry or _state.get("telemetry", {})
    if cfg.approach == "rear":
        await _rear_turn_if_needed(cfg)
        message = "후면 주차 완료"
    _state.update(phase="done", message=message, telemetry=final_telemetry)


def _motion_window(cfg: DockConfig, wall_cm: float | None) -> tuple[float, float]:
    # 멀 때는 끊지 말고 거의 연속 전진(거리 빨리 좁힘), 벽 근처에서만 짧게 끊어 정밀 접근.
    if wall_cm is None or wall_cm > cfg.slow_wall_cm:
        return max(cfg.move_pulse_s, 0.60), min(cfg.move_pause_s, 0.15)
    if wall_cm > max(cfg.target_wall_cm + 10.0, 20.0):
        return max(cfg.move_pulse_s, 0.30), min(cfg.move_pause_s, 0.35)
    return cfg.move_pulse_s, cfg.move_pause_s


async def _dock_loop(cfg: DockConfig) -> None:
    dt = 1.0 / cfg.loop_hz
    dictionary = _get_dictionary(_ARUCO_DICTS.get(cfg.dictionary, cv2.aruco.DICT_4X4_50))
    params = _get_params()
    calib = _load_calib()
    use_pose = calib is not None and cfg.marker_len_m and cfg.target_distance_m is not None
    # pose 조향(축 접근)은 target_distance_m 없이도 calib+마커크기만 있으면 쓴다
    pose_ok = calib is not None and bool(cfg.marker_len_m)
    started = time.time()
    lost = 0
    search_dir = 1.0
    reached = False        # 목표 거리 도달 여부 (도달 후 놓치면 완료로 간주)
    last_l = last_r = 0     # 마지막 명령 표시용
    last_move_l = last_move_r = 0  # 순간 마커 끊김 시 마지막 실제 이동값 유지용
    last_calc_l = last_calc_r = 0  # 마커가 보일 때 마지막으로 계산된 모터값
    last_calc_linear = 0.0
    last_calc_angular = 0.0
    last_calc_t = 0.0
    segment_until = 0.0
    segment_l = segment_r = 0
    segment_linear = 0.0
    segment_angular = 0.0
    segment_pose: dict[str, float] = {}
    prev_ang = 0.0          # 각속도 슬루 제한용 이전 명령(급회전 방지)
    turn_sign = _STEER_SIGN # 회전 극성(카메라 장착 좌우 방향) 자동 학습값 ±1
    last_pulse_abs_ex: float | None = None  # 직전 회전 펄스 시작 시 |ex| (정렬이 나아지는지 판정)
    prev_turning = False    # 직전 루프가 회전 펄스였는지(펄스 상승엣지 검출용)
    skew_sign = 1.0         # 수직 접근 skew 보정 부호(카메라 좌우) 자동 학습값 ±1
    skew_ref: float | None = None   # skew 부호 학습용 |skew| 기준값
    skew_ref_t = 0.0        # skew 기준값 샘플 시각
    final_align_since: float | None = None  # 벽 도달 후 최종 헤딩 정렬 시작 시각
    at_wall = False         # 벽 목표거리 도달 → 최종 몸통 정렬(final_align) 단계
    pre_aligned = False     # 근접 사전 정렬 완료 여부(마커가 시야에서 빠지기 전에 몸통 정렬)
    pre_align_since: float | None = None
    yaw_hist: list[float] = []  # 최근 yaw 이력 — 축 접근은 yaw 가 안정적일 때만 발동
    lost_since: float | None = None
    seen_once = False
    last_seen_id: int | None = None
    last_seen_ex = 0.0
    last_seen_turn_dir = 1.0
    last_seen_bearing = 0.0
    last_seen_wall_cm: float | None = None
    last_seen_t = 0.0
    near_count = 0          # 초음파 근접 연속 횟수(스파이크 오탐 방지 디바운스)
    lost_near_count = 0     # 마커 상실 중 근접 연속 횟수(완료 오판 방지 디바운스)
    push_s = 0.0            # 진전 없는 전진 명령 누적 시간(벽 박치기 감시)
    progress_size = 0.0     # 지금까지 관측한 최대 마커 크기
    progress_wall: float | None = None  # 지금까지 관측한 최소 벽 거리
    # LPF·PID 상태 — 센서 노이즈 방어(저역통과) + 미세 조정(적분/미분)
    ex_f: float | None = None      # 필터된 ex
    ex_i = 0.0                     # ex 적분 누적
    ex_prev_f: float | None = None # 미분용 직전 필터값
    wall_f: float | None = None    # 필터된 초음파 거리(cm)

    async def _wall_filtered() -> float | None:
        """초음파 LPF — 순간 스파이크(예: 0.4cm 오검출)를 완충해 오판을 막는다."""
        nonlocal wall_f
        raw = await _read_wall_cm()
        if raw is None:
            return wall_f
        wall_f = raw if wall_f is None else (cfg.wall_lpf_alpha * raw + (1.0 - cfg.wall_lpf_alpha) * wall_f)
        return wall_f
    try:
        _state.update(phase="docking", message="가이드선 PID 시작 — 마커 확인 중", telemetry={"left": 0, "right": 0})
        while True:
            # 공통 회전 펄스 제어 변수 (너무 확확 돌지 않게 제자리 회전 시 Turn-and-Pause 적용)
            turn_pulse_s = 0.12  # 회전 시간 (짧게 톡톡)
            turn_pause_s = 0.48  # 대기 시간 (카메라 프레임 갱신 대기)
            turn_cycle = turn_pulse_s + turn_pause_s
            is_turning_pulse = (time.time() - started) % turn_cycle <= turn_pulse_s

            if time.time() - started > cfg.timeout_s:
                _state.update(phase="timeout", message="시간 초과로 중단")
                break

            _state.update(phase="docking", message="카메라 프레임 확인 중", telemetry={"left": 0, "right": 0, "lost": lost})
            frame = _grab_frame()
            if frame is None:
                _state.update(phase="no_frame", message="카메라 프레임 없음")
                await _motor_send(0, 0)
                await asyncio.sleep(dt)
                continue

            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = _detect_markers_robust(gray, dictionary, params)

            target = None
            guide_pairs = []
            if ids is not None:
                # 8px 미만 유령 검출 제거 — 오검출을 따라가면 존재하지 않는 마커를 쫓는다
                all_pairs = [p for p in zip(corners, ids.flatten())
                             if _measure(p[0], w, h)["side_px"] >= 8.0]
                guide_pairs = all_pairs
                pairs = all_pairs
                if cfg.marker_id is not None:
                    pairs = [p for p in all_pairs if int(p[1]) == cfg.marker_id]
                if pairs:
                    target = max(pairs, key=lambda p: _measure(p[0], w, h)["size_frac"])

            if target is None:
                lost += 1
                skew_ref = None
                lost_since = lost_since or time.time()
                wall_cm = await _wall_filtered() if cfg.use_wall_sensor and seen_once else None
                ex_i = 0.0
                ex_prev_f = None
                ex_f = None

                if wall_cm is not None and wall_cm <= cfg.target_wall_cm:
                    await _motor_send(0, 0)
                    await _complete_parking(
                        cfg,
                        f"벽 도달 마커 유실 — 벽 {round(wall_cm, 1)}cm, 주차 완료",
                        {"wall_cm": wall_cm, "lost": lost},
                    )
                    break

                # 마커가 사라져도 새로 돌지 않는다. 마지막 pose 스냅샷으로 만든 segment만 끝까지 수행한다.
                if False and seen_once and time.time() <= segment_until and (segment_l != 0 or segment_r != 0):
                    await _motor_send(segment_l, segment_r)
                    prev_ang = segment_angular
                    last_move_l, last_move_r = segment_l, segment_r
                    _state.update(
                        phase="segment_blind",
                        message="마커 순간 상실 — 저장 pose segment 수행 중",
                        telemetry={
                            "wall_cm": wall_cm,
                            "lost": lost,
                            "left": segment_l,
                            "right": segment_r,
                            "linear": round(segment_linear, 3),
                            "angular": round(segment_angular, 3),
                            "segment_left_s": round(segment_until - time.time(), 2),
                            "last_seen_id": last_seen_id,
                            "last_seen_age_s": round(time.time() - last_seen_t, 2) if last_seen_t > 0 else None,
                            **segment_pose,
                        },
                    )
                elif seen_once and last_seen_t > 0 and (wall_cm is None or wall_cm > cfg.target_wall_cm):
                    lost_elapsed = time.time() - lost_since
                    cycle_s = max(0.05, cfg.search_pulse_s + cfg.search_pause_s)
                    in_pulse = (lost_elapsed % cycle_s) <= cfg.search_pulse_s
                    if in_pulse:
                        turn_hint = last_seen_bearing if abs(last_seen_bearing) >= 0.01 else last_seen_ex or 1.0
                        remembered_ang = math.copysign(cfg.lost_reacquire_turn_speed, turn_hint)
                        reacquire_linear = 0.0
                        l, r = _vel_to_speeds(reacquire_linear, remembered_ang)
                        l, r = _scale_min_drive(l, r, 10)
                    else:
                        remembered_ang = 0.0
                        l, r = 0, 0
                    await _motor_send(l, r)
                    prev_ang = remembered_ang
                    last_move_l = last_move_r = 0
                    _state.update(
                        phase="lost_reacquire",
                        message="선택 마커 분실 — 마지막 관측 bearing 방향으로 재획득 회전",
                        telemetry={
                            "wall_cm": wall_cm, "lost": lost, "left": l, "right": r,
                            "angular": round(remembered_ang, 3),
                            "last_calc_angular": round(last_calc_angular, 3),
                            "turn_source": "last_seen_bearing",
                            "last_seen_id": last_seen_id,
                            "last_seen_bearing_deg": round(math.degrees(last_seen_bearing), 1),
                            "last_seen_age_s": round(time.time() - last_seen_t, 2),
                            "marker_seen": False,
                        },
                    )
                else:
                    await _motor_send(0, 0)
                    prev_ang = 0.0
                    last_move_l = last_move_r = 0
                    _state.update(
                        phase="no_marker",
                        message="마커 없음 — 마지막 위치 정보 없음, 정지 대기",
                        telemetry={"wall_cm": wall_cm, "lost": lost, "left": 0, "right": 0, "marker_seen": False},
                    )

                at_wall = False
                final_align_since = None

                # 마커가 없으면 절대 이동하지 않는다.
                # 단, 초음파 센서 기준으로 이미 벽에 도달한 상태라면 (카메라 근접 사각지대 유실) 주차 완료로 판정
                if wall_cm is not None and wall_cm <= cfg.target_wall_cm:
                    await _complete_parking(
                        cfg,
                        f"벽 도달 마커 유실 — 벽 {round(wall_cm, 1)}cm, 주차 완료",
                        {"wall_cm": wall_cm, "lost": lost},
                    )
                    break
                if time.time() - lost_since > cfg.max_lost_s:
                    _state.update(
                        phase="lost",
                        message="마커 상실 시간 초과 — 안전 정지",
                        telemetry={"wall_cm": wall_cm, "lost": lost},
                    )
                    break
                await asyncio.sleep(dt)
                continue
            lost = 0
            lost_since = None
            lost_near_count = 0
            seen_once = True

            m = _measure(target[0], w, h)
            marker_ex = m["ex"]
            guide_ex = marker_ex
            guide_count = 1
            guide_cx = m["cx"]
            if len(guide_pairs) >= 2:
                centers = []
                for gc, _gid in guide_pairs:
                    gm = _measure(gc, w, h)
                    centers.append((gm["cx"], gm["cy"]))
                ys = np.array([p[1] for p in centers], dtype=np.float32)
                xs = np.array([p[0] for p in centers], dtype=np.float32)
                if float(np.ptp(ys)) >= 8.0:
                    a, b = np.polyfit(ys, xs, 1)
                    lookahead_y = h * 0.72
                    guide_cx = float(a * lookahead_y + b)
                    guide_cx = max(0.0, min(float(w), guide_cx))
                    guide_ex = (guide_cx - w / 2.0) / (w / 2.0)
                    guide_count = len(centers)
            ex = guide_ex
            # LPF: 검출 픽셀 노이즈 방어 — 이후 모든 제어·판정은 필터된 ex 를 쓴다
            ex_f = ex if ex_f is None else (cfg.ex_lpf_alpha * ex + (1.0 - cfg.ex_lpf_alpha) * ex_f)
            ex = ex_f
            search_dir = 1.0 if ex >= 0 else -1.0

            p = _pose(target[0], calib, cfg.marker_len_m, h) if pose_ok else None
            last_seen_id = int(target[1])
            last_seen_ex = ex
            last_seen_turn_dir = 1.0 if ex >= 0 else -1.0
            last_seen_bearing = math.atan2(p["x_m"], p["z_m"]) if p is not None else ex
            last_seen_wall_cm = wall_f
            last_seen_t = time.time()
            if p is not None:
                yaw_hist.append(p["yaw_deg"])
                if len(yaw_hist) > 5:
                    yaw_hist.pop(0)
            else:
                yaw_hist.clear()
            if use_pose:
                if p is None:
                    await _motor_send(0, 0)
                    await asyncio.sleep(dt)
                    continue
                dist_err = p["z_m"] - cfg.target_distance_m
                dist_ok = abs(dist_err) <= cfg.dist_tol_m
                lin_raw = cfg.kp_lin * dist_err
                tele_dist = round(p["z_m"], 3)
            else:
                dist_err = cfg.target_size - m["size_frac"]
                dist_ok = abs(dist_err) <= cfg.size_tol
                lin_raw = cfg.kp_lin * dist_err
                tele_dist = round(m["size_frac"], 3)

            reached = reached or dist_ok
            center_ok = abs(ex) <= cfg.center_tol

            # ── 벽 도달 후 최종 몸통 정렬: 마커가 화면 정중앙에 오도록 저속 미세 회전 펄스 ──
            if at_wall:
                aligned = abs(ex) <= cfg.final_center_tol
                if aligned or time.time() - (final_align_since or 0.0) > cfg.final_align_s:
                    msg = ("주차 완료 — 몸통 정렬 됨" if aligned
                           else "주차 완료 — 정렬 시간 초과(현 자세 유지)")
                    await _complete_parking(cfg, msg, {"ex": round(ex, 3), "aligned": aligned})
                    break
                await _complete_parking(cfg, "벽 도달 — 회전 없이 정지", {"ex": round(ex, 3)})
                break

            if center_ok and dist_ok:
                await _complete_parking(cfg, "도킹 완료")
                break

            angular = 0.0
            wall_cm = await _wall_filtered() if cfg.use_wall_sensor else None

            # ── 근접 사전 정렬: 마커가 시야에서 빠지기 전(벽 ~lost_crawl_wall_cm)에
            #    몸통을 마커 중심으로 맞춰두고, 이후 직진으로 마무리.
            #    (카메라가 위로 들려 있으면 근접 시 마커가 화면 아래로 사라지므로 미리 정렬)
            if False and not pre_aligned and cfg.use_wall_sensor and wall_cm is not None and wall_cm <= cfg.lost_crawl_wall_cm:
                if abs(ex) <= cfg.final_center_tol:
                    pre_aligned = True  # 이미 마커 정면 — 그대로 접근 계속
                else:
                    pre_align_since = pre_align_since or time.time()
                    if time.time() - pre_align_since > cfg.final_align_s:
                        pre_aligned = True  # 정렬 시간 초과 — 그대로 접근 계속
                    else:
                        turning = (time.time() - pre_align_since) % (cfg.turn_pulse_s + cfg.turn_pause_s) <= cfg.turn_pulse_s
                        if turning:
                            ang = _STEER_SIGN * math.copysign(cfg.align_turn_speed, ex)
                            l, r = _vel_to_speeds(0.0, ang)
                            l, r = _scale_min_drive(l, r, cfg.align_turn_min_drive)
                        else:
                            l = r = 0
                        await _motor_send(l, r)
                        _state.update(
                            phase="pre_align",
                            message=f"근접 사전 정렬 — 몸통을 마커 중심으로 ({round(wall_cm, 1)}cm)",
                            telemetry={"id": int(target[1]), "ex": round(ex, 3), "wall_cm": wall_cm, "left": l, "right": r},
                        )
                        await asyncio.sleep(dt)
                        continue

            pulse_s, pause_s = _motion_window(cfg, wall_cm)
            move_cycle = pulse_s + pause_s
            moving_pulse = (time.time() - started) % move_cycle <= pulse_s

            axis_lat = None
            axis_bearing = None
            axis_target = None
            small_marker = m["side_px"] < 25.0
            center_align_tol = 0.30 if small_marker else 0.22
            # 1단계: 마커가 화면 극단(97% 이상)에 치우쳐 분실 직전인 경우 안전 정지 대기
            if abs(ex) > 0.97:
                await _motor_send(0, 0)
                prev_ang = 0.0
                last_move_l = last_move_r = 0
                _state.update(
                    phase="align_wait",
                    message="마커가 화면 극단 가장자리 — 이동 중지, 수동 정렬 필요",
                    telemetry={"id": int(target[1]), "ex": round(ex, 3), "marker_ex": round(marker_ex, 3), "guide_count": guide_count, "guide_cx": round(guide_cx, 1), "dist": tele_dist, "side_px": round(m["side_px"], 1), "left": 0, "right": 0},
                )
                await asyncio.sleep(dt)
                continue

            # 2단계: 마커가 측면에 크게 벗어난 경우 (align_ex_tol = 0.85): 
            #        전진을 멈추고 제자리에서 turn-and-pause(펄스 제어) 방식으로 안전하게 회전 정렬
            if False and p is None and abs(ex) > cfg.align_ex_tol:
                edge_pulse_s = max(cfg.turn_pulse_s, 0.12)  # 너무 짧으면 35% 파워로도 안 돌 수 있으므로 최소 0.12초 보장
                edge_pause_s = max(cfg.turn_pause_s, 0.40)
                edge_cycle = edge_pulse_s + edge_pause_s
                turning = (time.time() - started) % edge_cycle <= edge_pulse_s
                if turning:
                    ang = _STEER_SIGN * math.copysign(cfg.align_turn_speed, ex)
                    l, r = _vel_to_speeds(0.0, ang)
                    l, r = _scale_min_drive(l, r, cfg.align_turn_min_drive)
                else:
                    l = r = 0
                await _motor_send(l, r)
                prev_ang = 0.0
                ex_i = 0.0
                ex_prev_f = None
                last_move_l = last_move_r = 0
                _state.update(
                    phase="edge_align",
                    message=f"마커 가장자리 ({round(ex, 2)}) — 시야 확보를 위해 제자리 회전 정렬 중",
                    telemetry={"id": int(target[1]), "ex": round(ex, 3), "marker_ex": round(marker_ex, 3), "guide_count": guide_count, "guide_cx": round(guide_cx, 1), "dist": tele_dist, "left": l, "right": r},
                )
                await asyncio.sleep(dt)
                continue

            # ── Pose 기반 마커 축(가이드 선) 추종 ──
            # 화면에 보이는 가이드 선은 마커 중심점이 아니라 마커 면의 법선축이다.
            # 옆에서 시작하면 마커 중심으로 바로 돌진하지 말고, 마커 앞 standoff 지점
            # (법선축 위의 가상 경유점)을 먼저 향해 축 위로 합류한 뒤 최종 직진 도킹한다.
            if cfg.pose_axis_enable and p is not None and p.get("nx") is not None and p.get("nz") is not None:
                nx = float(p["nx"])
                nz = float(p["nz"])
                axis_lat = p["x_m"] * nz - p["z_m"] * nx
                standoff = min(cfg.pose_axis_standoff_m, max(0.12, p["z_m"] - 0.10))
                wx = p["x_m"] + nx * standoff
                wz = p["z_m"] + nz * standoff
                if wz > 0.08:
                    marker_bearing = math.atan2(p["x_m"], p["z_m"])
                    raw_bearing = math.atan2(wx, wz)
                    limit = math.radians(cfg.pose_axis_bearing_limit_deg)
                    axis_bearing = max(marker_bearing - limit, min(marker_bearing + limit, raw_bearing))
                    axis_target = {"x_m": wx, "z_m": wz}

            if axis_lat is not None and axis_bearing is not None and abs(axis_lat) > cfg.pose_axis_tol_m:
                linear = cfg.lin_max * 0.55
                angular = _STEER_SIGN * max(
                    -cfg.steer_ang_max,
                    min(min(cfg.steer_ang_max, 0.08), cfg.pose_perp_kp_yaw * axis_bearing),
                )
                if abs(axis_bearing) > math.radians(12):
                    linear = cfg.lin_max * 0.35
                angular = 0.0
                prev_ang = 0.0

                if cfg.use_wall_sensor:
                    if wall_cm is None:
                        wall_cm = await _wall_filtered()
                    if wall_cm is not None and wall_cm <= cfg.target_wall_cm:
                        await _complete_parking(cfg, f"벽 앞 주차 완료 — {round(wall_cm, 1)}cm", {"wall_cm": wall_cm})
                        break
                    if wall_cm is not None and wall_cm <= cfg.slow_wall_cm:
                        linear = min(linear, cfg.lin_max * 0.45)

                # 곡선 주행만 허용한다. angular가 linear보다 크면 한쪽 바퀴가 역회전해 제자리 회전이 된다.
                max_curve_ang = max(0.0, linear * 0.20)
                angular = max(-max_curve_ang, min(max_curve_ang, angular))

                l, r = _vel_to_speeds(linear, angular)
                l, r = _scale_min_drive(l, r, cfg.min_drive)
                last_l, last_r = l, r
                if l or r:
                    last_move_l, last_move_r = l, r
                await _motor_send(l, r)

                _state["phase"] = "axis_guidance"
                _state["message"] = "마커 가이드 축 합류 중"
                _state["telemetry"] = {
                    "id": int(target[1]), "ex": round(ex, 3), "marker_ex": round(marker_ex, 3), "guide_count": guide_count, "guide_cx": round(guide_cx, 1), "dist": tele_dist,
                    "skew": round(m["skew"], 3), "linear": round(linear, 3),
                    "angular": round(angular, 3), "left": l, "right": r,
                    "wall_cm": wall_cm,
                    "yaw_deg": round(p["yaw_deg"], 1),
                    "axis_lat_m": round(axis_lat, 3),
                    "axis_bearing_deg": round(math.degrees(axis_bearing), 1),
                    "axis_target": axis_target,
                    "center_ok": center_ok, "dist_ok": dist_ok, "pose": True,
                }
                await asyncio.sleep(dt)
                continue

            # ── ㄱ자 주차 상태머신: 회전 → 직선 접근 → 재검출 ──
            if cfg.pose_center_enable and p is not None:
                bearing = math.atan2(p["x_m"], p["z_m"])
                yaw_rad = math.radians(p["yaw_deg"])
                bearing_abs = abs(bearing)
                bearing_tol = math.radians(cfg.lpark_bearing_tol_deg)
                wall_cm = wall_cm if wall_cm is not None else (await _wall_filtered() if cfg.use_wall_sensor else None)

                if cfg.use_wall_sensor and wall_cm is not None and wall_cm <= cfg.target_wall_cm:
                    await _complete_parking(
                        cfg,
                        f"벽 앞 주차 완료 — {round(wall_cm, 1)}cm",
                        {"wall_cm": wall_cm, "x_m": round(p["x_m"], 3), "z_m": round(p["z_m"], 3)},
                    )
                    break

                # ㄱ자 주차 원칙:
                # 1) 마커 중심 방향(bearing)이 틀어져 있으면 전진하지 않고 먼저 몸통만 튼다.
                # 2) bearing이 정렬된 짧은 순간에만 직선 접근한다.
                # 3) 전진하다 마커가 시야에서 빠지면 target is None 분기에서 마지막 bearing 방향으로 재획득 회전한다.
                x_abs = abs(p["x_m"])
                center_ready = bearing_abs <= bearing_tol and x_abs <= cfg.lpark_x_tol_m
                if not center_ready:
                    linear = 0.0
                    turn_hint = bearing if abs(bearing) >= math.radians(0.5) else p["x_m"]
                    angular = math.copysign(cfg.lpark_turn_speed, turn_hint)
                    turning = (time.time() - started) % (cfg.turn_pulse_s + cfg.turn_pause_s) <= cfg.turn_pulse_s
                    if turning:
                        l, r = _vel_to_speeds(0.0, angular)
                        l, r = _scale_min_drive(l, r, cfg.align_turn_min_drive)
                    else:
                        angular = 0.0
                        l, r = 0, 0
                    phase = "lpark_turn"
                    message = "ㄱ자 주차 — 마커 중심 방향으로 회전 정렬 중"
                else:
                    angular = 0.0
                    prev_ang = 0.0
                    linear = cfg.lin_max * cfg.lpark_forward_scale
                    if guide_count < 2:
                        linear = min(linear, cfg.lin_max * 0.30)
                    if wall_cm is not None and wall_cm <= cfg.slow_wall_cm:
                        linear = min(linear, cfg.lin_max * 0.24)
                    if dist_err <= (cfg.dist_tol_m * 3 if use_pose else cfg.size_tol * 3):
                        linear = min(linear, cfg.lin_max * 0.20)
                    l, r = _vel_to_speeds(linear, 0.0)
                    l, r = _scale_min_drive(l, r, cfg.min_drive)
                    phase = "lpark_forward"
                    message = "ㄱ자 주차 — bearing 정렬, 짧은 직선 접근 중"

                last_l, last_r = l, r
                last_calc_l, last_calc_r = l, r
                last_calc_linear, last_calc_angular = linear, angular
                last_calc_t = time.time()
                # 분실 시 앞으로 계속 밀지 않는다. 마지막 pose는 재획득 회전 방향 판단에만 쓴다.
                segment_l = segment_r = 0
                segment_linear = 0.0
                segment_angular = 0.0
                segment_until = 0.0
                segment_pose = {
                    "x_m": round(p["x_m"], 3),
                    "z_m": round(p["z_m"], 3),
                    "bearing_deg": round(math.degrees(bearing), 1),
                    "yaw_deg": round(p["yaw_deg"], 1),
                }
                if l or r:
                    last_move_l, last_move_r = l, r
                await _motor_send(l, r)

                _state["phase"] = phase
                _state["message"] = message
                _state["telemetry"] = {
                    "id": int(target[1]), "ex": round(ex, 3), "marker_ex": round(marker_ex, 3),
                    "x_m": round(p["x_m"], 3), "z_m": round(p["z_m"], 3),
                    "yaw_deg": round(p["yaw_deg"], 1), "bearing_deg": round(math.degrees(bearing), 1),
                    "bearing_tol_deg": cfg.lpark_bearing_tol_deg,
                    "x_tol_m": cfg.lpark_x_tol_m,
                    "linear": round(linear, 3), "angular": round(angular, 3),
                    "left": l, "right": r, "wall_cm": wall_cm,
                    "center_ok": center_ready, "dist_ok": dist_ok, "pose": True,
                    "mode": "l_parking",
                }
                await asyncio.sleep(dt)
                continue

            # ── 연속 비주얼 서보잉 (마커 위치 PID 조향 주행) ──
            # 마커가 화면 왼쪽/가운데/오른쪽 어디에 있는지(ex, -1..1)와 거리를 보고,
            # PID(비례+적분+미분) 각속도를 주면서 전진한다. 마커가 중앙으로 올수록
            # 조향이 줄어들어 자연히 마커 중심축 위로 수렴한다.
            #   P: ex 크기에 비례해 조향  I: 잔류 오차 미세 제거  D: 오버슛·진동 억제
            linear = cfg.lin_max
            if guide_count < 2:
                # 마커가 1개만 검출된 경우: 전진 속도를 낮추어 안전하게 접근하되 조향(angular)은 계속 수행하여 정렬함
                linear = min(cfg.lin_max * 0.35, cfg.lin_max)

            if abs(ex) <= cfg.steer_deadband:
                angular = 0.0
                ex_i *= 0.8
                ex_prev_f = ex
            else:
                ex_i = max(-cfg.steer_i_max, min(cfg.steer_i_max, ex_i + ex * dt))
                d_ex = 0.0 if ex_prev_f is None else (ex - ex_prev_f) / dt
                ex_prev_f = ex
                pid = cfg.steer_kp * ex + cfg.steer_ki * ex_i + cfg.steer_kd * d_ex
                cmd = max(-cfg.steer_ang_max, min(cfg.steer_ang_max, pid))
                if abs(cmd) < cfg.steer_ang_min:
                    cmd = math.copysign(cfg.steer_ang_min, cmd)
                angular = _STEER_SIGN * cmd
                if abs(ex) > cfg.steer_ex_tol:
                    linear = min(linear, cfg.lin_max * 0.5)

            # 목표 거리 근접 시 감속 — 오버슛·박치기 방지
            if dist_err <= (cfg.dist_tol_m * 3 if use_pose else cfg.size_tol * 3):
                linear = min(linear, cfg.lin_max * 0.6)

            # 급회전 방지 슬루 제한(루프당 각속도 변화 상한)
            angular = max(prev_ang - cfg.ang_slew, min(prev_ang + cfg.ang_slew, angular))
            prev_ang = angular

            # ── 벽 도달 판정 (초음파 센서 거리 기준) ──
            if cfg.use_wall_sensor:
                if wall_cm is None:
                    wall_cm = await _wall_filtered()

                # 벽 목표거리 도달 시 즉시 정지 및 주차 완료 (물리 충돌 방지)
                if wall_cm is not None and wall_cm <= cfg.target_wall_cm:
                    msg = f"벽 앞 주차 완료 — {round(wall_cm, 1)}cm"
                    await _complete_parking(cfg, msg, {"wall_cm": wall_cm, "ex": round(ex, 3)})
                    break

                # 벽 근접(slow_wall_cm 이내)부터는 펄스 크롤로 전진 — 센서 계측 시간 확보
                if wall_cm is not None and wall_cm <= cfg.slow_wall_cm:
                    crawl_cycle = 0.60
                    crawl_pulse_s = 0.12
                    is_crawl_pulse = (time.time() - started) % crawl_cycle <= crawl_pulse_s
                    if not is_crawl_pulse:
                        linear = 0.0
                        angular = 0.0
                        prev_ang = 0.0

            l, r = _vel_to_speeds(linear, angular)
            peak = max(abs(l), abs(r))
            if 0 < peak < cfg.min_drive:
                s = cfg.min_drive / peak
                l, r = int(round(l * s)), int(round(r * s))

            last_l, last_r = l, r
            last_calc_l, last_calc_r = l, r
            last_calc_linear, last_calc_angular = linear, angular
            last_calc_t = time.time()
            if l or r:
                last_move_l, last_move_r = l, r
            await _motor_send(l, r)

            # ── 박치기 감시(최후 방어선): 전진하는데 마커도 안 커지고 벽도 안 가까워지면 막힌 것 ──
            if linear > 0:
                push_s += dt
                progressed = False
                if m["size_frac"] > progress_size + 0.004:
                    progress_size = m["size_frac"]
                    progressed = True
                if wall_cm is not None and (progress_wall is None or wall_cm < progress_wall - 1.0):
                    progress_wall = wall_cm
                    progressed = True
                if progressed:
                    push_s = 0.0
                elif push_s > 2.5:
                    near = reached or (progress_wall is not None and progress_wall <= max(cfg.lost_done_wall_cm, cfg.target_wall_cm + 5.0))
                    if wall_cm is None or wall_cm > cfg.slow_wall_cm:
                        # 아직 벽이 먼 구간에서는 카메라/초음파 변화가 작아도 막힘으로 보지 않는다.
                        # 이 구간은 계속 전진해서 벽 센서가 유의미해질 때까지 기다린다.
                        push_s = 0.0
                        progress_size = max(progress_size, m["size_frac"])
                        if wall_cm is not None:
                            progress_wall = wall_cm if progress_wall is None else min(progress_wall, wall_cm)
                        continue
                    if near:
                        if abs(ex) > cfg.final_center_tol and cfg.final_align_s > 0:
                            # 접촉 추정이지만 마커가 보임 → 완료 전에 몸통 정렬부터
                            at_wall = True
                            final_align_since = time.time()
                            await _motor_send(0, 0)
                            prev_ang = 0.0
                            _state.update(phase="final_align", message="벽 접촉 추정 — 몸통 정렬 시작")
                            await asyncio.sleep(dt)
                            continue
                        await _complete_parking(
                            cfg, "벽 접촉 추정 — 전진 진전 없음, 주차 완료",
                            {"ex": round(ex, 3), "size": round(m["size_frac"], 3), "wall_cm": wall_cm},
                        )
                    else:
                        await _motor_send(0, 0)
                        _state.update(phase="blocked", message="전진해도 접근이 안 됨 — 장애물 추정, 안전 정지")
                    break

            _state["phase"] = "docking"
            _state["telemetry"] = {
                "id": int(target[1]), "ex": round(ex, 3), "marker_ex": round(marker_ex, 3), "guide_count": guide_count, "guide_cx": round(guide_cx, 1), "dist": tele_dist,
                "skew": round(m["skew"], 3), "linear": round(linear, 3),
                "angular": round(angular, 3), "left": l, "right": r,
                "wall_cm": wall_cm,
                "yaw_deg": round(p["yaw_deg"], 1) if p else None,
                "axis_lat_m": round(axis_lat, 3) if axis_lat is not None else None,
                "center_ok": center_ok, "dist_ok": dist_ok, "pose": p is not None,
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
        _leave_low_cpu_mode(cfg)



async def _dock_detect_payload(dictionary: str = "DICT_4X4_50", marker_len_m: float | None = None) -> dict[str, Any]:
    """현재 프레임에서 마커 검출 결과 반환 (모터 미동작 - 검증/튜닝용)."""
    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.5)

    # 카메라 프레임 준비 대기 (최대 3초)
    for _ in range(60):
        try:
            return _detect(dictionary, marker_len_m)
        except HTTPException as e:
            if e.status_code == 503 and "카메라 프레임 없음" in str(e.detail):
                await asyncio.sleep(0.05)
                continue
            raise
    raise HTTPException(503, "카메라 프레임을 받지 못했습니다. 카메라 연결을 확인하세요.")


@router.get("/dock/detect")
async def detect(dictionary: str = "DICT_4X4_50", marker_len_m: float | None = None):
    return await _dock_detect_payload(dictionary, marker_len_m)


@router.websocket("/dock/ws/detect")
async def detect_ws(ws: WebSocket, dictionary: str = "DICT_4X4_50", marker_len_m: float | None = None):
    await ws.accept()
    try:
        while True:
            await ws.send_json(await _dock_detect_payload(dictionary, marker_len_m))
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


@router.post("/dock/start")
async def start(cfg: DockConfig):
    global _task
    if cfg.dictionary not in _ARUCO_DICTS:
        raise HTTPException(400, f"지원하지 않는 dictionary: {cfg.dictionary}")

    # 직접주차는 항상 깨끗한 상태에서 시작한다. 이전 태스크/모터/카메라 상태가 남으면
    # starting 고착, stale marker, 반대 조향 같은 현장 문제가 바로 난다.
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    await _motor_send(0, 0)
    _state.update(running=False, phase="initializing", message="직접주차 초기화 중", telemetry={"left": 0, "right": 0})

    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.7)

    _enter_low_cpu_mode(cfg)
    _state.update(running=True, phase="starting", message="카메라 시작 완료 — 가이드선 확인 중", telemetry={"left": 0, "right": 0})
    _task = asyncio.create_task(_dock_loop(cfg))
    return {"success": True, "message": "도킹 시작", "config": cfg.model_dump()}


@router.post("/dock/stop")
async def stop():
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    await _motor_send(0, 0)
    try:
        camera.stop()
    except Exception:
        pass
    _pm2("start", "nav2")
    _state.update(running=False, phase="idle", message="정지됨")
    return {"success": True, "message": "도킹 정지"}


def _dock_status_payload() -> dict[str, Any]:
    task_info = {}
    if _task is not None:
        task_info = {
            "done": _task.done(),
            "cancelled": _task.cancelled(),
        }
        if _task.done():
            try:
                exc = _task.exception()
                task_info["exception"] = str(exc) if exc else None
            except Exception as e:
                task_info["exception"] = f"Error: {e}"
    return {"state": _state, "task": task_info}


@router.get("/dock/status")
async def status():
    return _dock_status_payload()


@router.websocket("/dock/ws/status")
async def dock_status_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(_dock_status_payload())
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
