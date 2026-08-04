"""마커 직접 주차 — P 제어 (탐지 → 계산 → depth+투영 → 주행).

배포 원본. 로봇에는 `robot_agent/app/routers/parkp.py` 로 복사되어
`/api/robot/parkp/*` 로 마운트된다(중앙 FMS 의 main.py 에는 마운트하지 않는다).

기존 `aruco_dock.py` 는 유도마커·라인·후면주차·nav2 토글 등이 얽혀 979줄까지 커졌다.
이 라우터는 "앞에 보이는 마커 하나로 곧장 주차" 한 가지만 한다.

파이프라인
  1) 탐지        detectMarkers (실패 시 2배 확대 재검출) → 설정된 marker_id 만 선택
  2) 계산        _measure → ex(중심 오차, +=오른쪽), skew, size_frac
  3) depth+투영  solvePnP → 마커 3D 위치(x,y,z m)+법선 n → 마커 정면축 위의
                 '진입 경유점(standoff)' 을 투영해 목표점 W 를 만든다
  4) 주행        P 제어 한 줄:  angular = kp_ang · atan2(W.x, W.z)
                                linear  = kp_lin · (z - 목표거리) · 회전우선 감속

  로봇이 마커 정면(직선)이면 W 가 정면에 놓여 bearing≈0 → angular≈0 → 그냥 직진.
  사선이면 W 가 옆으로 밀려 bearing 이 살아 있어 → 곡선으로 정면축에 합류한 뒤 직진.
  목표점은 접근할수록 마커에 붙는다(d_hold → 0) — 단계 전환 없는 연속 제어.

메모리 큐 (마커 분실 대비)
  검출될 때마다 {x,y,z, gx,gy,gz} 를 링버퍼에 쌓는다.
    x,y,z    — 카메라(로봇) 기준 마커 위치 (m)
    gx,gy    — odom 전역 좌표계로 옮긴 마커 위치 (m)
    gz       — 마커 법선의 전역 방위각 (rad)
  마커를 놓치면 최근 K 개의 중앙값으로 마커 전역 위치를 복원하고,
    err = normalize(atan2(gy-ry, gx-rx) - ryaw)
  만큼 P 제어로 제자리 회전해 시야에 다시 넣는다. 가상 경로(전역 경유점 목록)도
  같이 들고 있어, 회전만으로 못 찾으면 마지막 경로점 쪽으로 저속 전진해 재탐색한다.

부호 규약 (2026-07-06 현장 검증된 aruco_dock 의 _STEER_SIGN=+1 과 동일하게 맞춤)
  angular > 0 이 카메라 이미지 +x(오른쪽) 방향으로 로봇을 돌린다.
  즉 로봇 좌표계에서 '왼쪽' = cam_x_sign · x_m 이며 기본값 +1.0.
  실기에서 반대로 흐르면 cam_x_sign 을 -1.0 으로 뒤집는다(UI 에서 조정 가능).
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hardware.camera_stream import camera
from app.routers.driving import (
    _ensure_sensor_daemon,
    _motor_send,
    _vel_to_speeds,
    _read_dist_cached,
    _read_ir,
)

try:  # odom 은 ROS 브리지에서 온다. 브리지가 없으면 메모리 큐의 전역 좌표만 비워둔다.
    from app.core import ros_bridge
except Exception:  # noqa: BLE001
    ros_bridge = None  # type: ignore[assignment]

router = APIRouter()

# driving._vel_to_speeds 의 스케일(= MAX_SPEED). 하드코딩하지 않고 역산해 두어
# 로봇 쪽 값이 바뀌어도 따라가게 한다. _drive 가 float 스케일링에 쓴다.
_MAX_SPEED = abs(_vel_to_speeds(1.0, 0.0)[0]) or 75

_CALIB_PATH = Path(__file__).resolve().parents[2] / "config" / "camera_calib.npz"

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


# ── OpenCV 버전 shim (4.6 / 4.7+ 모두 지원) ───────────────────────────────────
def _get_dictionary(dict_name: str):
    dict_id = _ARUCO_DICTS.get(dict_name, cv2.aruco.DICT_4X4_50)
    if hasattr(cv2.aruco, "getPredefinedDictionary"):
        return cv2.aruco.getPredefinedDictionary(dict_id)
    return cv2.aruco.Dictionary_get(dict_id)


def _get_params():
    p = (cv2.aruco.DetectorParameters_create()
         if hasattr(cv2.aruco, "DetectorParameters_create")
         else cv2.aruco.DetectorParameters())
    p.adaptiveThreshWinSizeMin = 3
    p.adaptiveThreshWinSizeMax = 43
    p.adaptiveThreshWinSizeStep = 6
    p.minMarkerPerimeterRate = 0.02
    p.maxMarkerPerimeterRate = 4.0
    p.polygonalApproxAccuracyRate = 0.06
    try:
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    except Exception:  # noqa: BLE001
        pass
    return p


def _detect_markers(gray, dictionary, params):
    if hasattr(cv2.aruco, "ArucoDetector"):
        return cv2.aruco.ArucoDetector(dictionary, params).detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, dictionary, parameters=params)


def _detect_markers_robust(gray, dictionary, params):
    """1차 실패 시 2배 확대 후 재검출 → 코너를 원본 스케일로 되돌린다."""
    corners, ids, rej = _detect_markers(gray, dictionary, params)
    if ids is None:
        big = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)
        c2, ids2, rej = _detect_markers(big, dictionary, params)
        if ids2 is not None:
            corners = tuple(np.asarray(c, dtype=np.float32) / 2.0 for c in c2)
            ids = ids2
    return corners, ids, rej


# camera_calib.npz 가 만들어진 기준 해상도. K 는 해상도에 선형 비례하므로
# 캡처 해상도가 이와 다르면 그대로 쓰면 안 된다 — fx·cx 가 배율만큼 틀려
# solvePnP 거리가 조용히 어긋난다(marker_dock.py:233 에 같은 취지의 경고가 있다).
_CALIB_REF_WH = (480, 360)


def _load_calib(frame_wh: tuple[int, int] | None = None) -> tuple[np.ndarray, np.ndarray] | None:
    """캘리브레이션 로드. frame_wh 를 주면 그 해상도에 맞춰 K 를 스케일한다.

    캡처 해상도는 camera_stream.CAPTURE_W/H 에서 바뀔 수 있고, 실측상 요청한
    해상도가 그대로 나오지 않은 전력도 있다(2026-07-30: 480x360 요청 → 640x360 반환).
    그래서 설정값이 아니라 **실제 프레임 크기**를 받아 스케일한다.
    """
    if not _CALIB_PATH.exists():
        return None
    try:
        data = np.load(str(_CALIB_PATH))
        K, dist = data["camera_matrix"], data["dist_coeffs"]
    except Exception:  # noqa: BLE001
        return None
    if frame_wh is not None:
        sx = frame_wh[0] / float(_CALIB_REF_WH[0])
        sy = frame_wh[1] / float(_CALIB_REF_WH[1])
        if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6:
            K = K.copy()
            K[0, 0] *= sx
            K[0, 2] *= sx
            K[1, 1] *= sy
            K[1, 2] *= sy
    return K, dist


def _grab_frame() -> np.ndarray | None:
    jpeg = camera.get_jpeg()
    if not jpeg:
        return None
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def _norm(a: float) -> float:
    """각도를 -pi..pi 로 정규화."""
    return math.atan2(math.sin(a), math.cos(a))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── 1단계: 탐지 + 2단계: 계산 ─────────────────────────────────────────────────
def _measure(corners: np.ndarray, w: int, h: int) -> dict[str, float]:
    """마커 코너 → 화면 기하 지표. 코너 순서는 X/Y 정렬로 복원해 회전·flip 에 견딘다."""
    pts = corners.reshape(4, 2).astype(np.float32)
    idx_x = np.argsort(pts[:, 0])
    left_pts, right_pts = pts[idx_x[:2], :], pts[idx_x[2:], :]
    tl = left_pts[np.argmin(left_pts[:, 1]), :]
    bl = left_pts[np.argmax(left_pts[:, 1]), :]
    tr = right_pts[np.argmin(right_pts[:, 1]), :]
    br = right_pts[np.argmax(right_pts[:, 1]), :]

    top = float(np.linalg.norm(tr - tl))
    bottom = float(np.linalg.norm(br - bl))
    left = float(np.linalg.norm(bl - tl))
    right = float(np.linalg.norm(br - tr))
    side = (top + bottom + left + right) / 4.0
    cx = float(pts[:, 0].mean())
    cy = float(pts[:, 1].mean())
    denom = left + right
    return {
        "cx": cx, "cy": cy,
        "ex": (cx - w / 2.0) / (w / 2.0),          # 화면 중심 오차 (+ = 오른쪽)
        "ey": (cy - h / 2.0) / (h / 2.0),
        "size_frac": side / float(w),
        "skew": (right - left) / denom if denom > 1e-6 else 0.0,
        "side_px": side,
    }


# ── 3단계: depth (solvePnP) ──────────────────────────────────────────────────
def _pose3(corners: np.ndarray, calib: tuple[np.ndarray, np.ndarray],
           marker_len_m: float, h: int) -> dict[str, float] | None:
    """마커의 3D 위치(x,y,z m)와 정면 법선(nx,nz)을 구한다.

    좌표 규약은 기존 aruco_dock._pose 와 동일하게 유지한다(이미지 y 뒤집기 + 코너 역순).
    현장 튜닝된 값들이 이 규약 위에 서 있어서 바꾸면 게인이 전부 어긋난다.
    다만 여기서는 메모리 큐용으로 y(tvec[1])까지 함께 돌려준다.
    """
    K, dist = calib
    img = corners.reshape(4, 2).astype(np.float32).copy()
    img[:, 1] = h - img[:, 1]
    img = img[[3, 2, 1, 0], :]

    s = marker_len_m / 2.0
    obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float32)
    try:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    except Exception:  # noqa: BLE001
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist)
    if not ok:
        return None

    tvec = tvec.reshape(3)
    R, _ = cv2.Rodrigues(rvec.reshape(3))
    yaw = float(np.degrees(np.arctan2(-R[2, 0], math.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))))
    # 마커 법선을 항상 카메라 쪽(-z)으로 향하게 통일 → 경유점이 마커와 로봇 사이에 생긴다.
    n = R[:, 2]
    if n[2] > 0:
        n = -n
    return {
        "x_m": float(tvec[0]), "y_m": float(tvec[1]), "z_m": float(tvec[2]),
        "yaw_deg": yaw, "nx": float(n[0]), "nz": float(n[2]),
    }


# ── 3단계: 투영 — 마커 정면축 위의 진입 경유점 ────────────────────────────────
def _project(p: dict[str, float], target_distance_m: float, standoff_m: float) -> dict[str, float]:
    """마커 정면축(법선) 위로 로봇을 투영해 목표점 W 와 횡오프셋을 만든다.

    d_hold 는 남은 거리에 따라 standoff→0 으로 줄어든다. 멀 때는 마커 앞쪽
    경유점을 보고 곡선으로 축에 합류하고, 가까워지면 목표점이 마커 자체로
    수렴해 그대로 직진 진입한다(단계 전환·제자리 회전 없음).
    """
    x, z, nx, nz = p["x_m"], p["z_m"], p["nx"], p["nz"]
    # ★ 남은 거리는 z(전방 성분)가 아니라 range=hypot(x,z) 로 재야 한다.
    #   z 는 로봇이 제자리 회전만 해도 값이 바뀌어(물리 거리는 그대로) 경유점 W 가 따라 움직이고,
    #   그 결과 좌우로 진동한다(시뮬레이션에서 확인, 2026-07-29). range 는 회전 불변이라 W 가 고정된다.
    rng = math.hypot(x, z)
    d_hold = _clamp(rng - target_distance_m, 0.0, standoff_m)
    wx = x + nx * d_hold
    wz = z + nz * d_hold
    # 마커 정면축에서 로봇이 옆으로 벗어난 거리 (+ = 축의 오른쪽)
    axis_lat = x * nz - z * nx
    return {
        "wx": wx, "wz": wz, "d_hold": d_hold,
        "bearing": math.atan2(wx, max(wz, 1e-3)),          # 경유점 방위 (+ = 오른쪽)
        "marker_bearing": math.atan2(x, max(z, 1e-3)),
        "axis_lat": axis_lat,
        "marker_range": rng,                                # 마커까지 실제 거리(회전 불변)
        "range": math.hypot(wx, wz),                        # 경유점까지 거리
    }


# ── 설정 ─────────────────────────────────────────────────────────────────────
class ParkPConfig(BaseModel):
    # 1단계 — 탐지
    marker_id: int = Field(..., description="주차 대상 마커 id (UI 설정값)")
    dictionary: str = "DICT_4X4_50"
    marker_len_m: float = Field(0.05, gt=0.0, le=1.0)

    # 3단계 — 투영
    target_distance_m: float = Field(0.20, ge=0.03, le=2.0)   # 마커 앞 정지 거리
    standoff_m: float = Field(0.40, ge=0.0, le=1.0)           # 진입 경유점 최대 거리
    dist_tol_m: float = Field(0.03, ge=0.005, le=0.3)
    bearing_tol_deg: float = Field(6.0, ge=0.5, le=45.0)
    # 근접 완료를 '성공'으로 부르기 위한 정면축 이탈 허용치. 거리만으로 완료를 판정하면
    # 삐뚤게 선 것도 전부 성공이 된다(2026-08-03 실측 8.9cm) — 그 구분에 쓴다.
    axis_tol_m: float = Field(0.04, ge=0.005, le=0.3)
    # 접근을 '경유점 회전 → 직진'으로 분해할지. 곡선 주행이 불가능한 구동계에 맞춘 구조지만,
    # align_wp 는 마커가 아니라 **경유점**을 향해 돌기 때문에 마커가 정면이어도 축이탈이
    # 있으면 회전한다 — 그 회전 중 마커를 흘려 분실이 잦아졌다(2026-08-03). 기본은 끔.
    stage_approach: bool = False

    # 4단계 — P 제어
    # ⚠️ angular 는 linear 보다 실효 권한이 훨씬 크다: _vel_to_speeds 는
    #    left=(lin-ang)·75, right=(lin+ang)·75 이고 트랙폭이 9.6cm 뿐이라
    #    ang 1 단위가 만드는 각속도가 lin 1 단위의 전진속도보다 20배 가까이 세다.
    #    그래서 ang_max 는 lin_max 의 1/2 이하로 둔다(기존 aruco_dock 실기 튜닝값과 동일 범위).
    kp_ang: float = Field(0.65, ge=0.0, le=5.0)
    # 축 복귀(cross-track) 이득. 마커가 기울어져 있을 때 경유점 조향만으로는
    # 정면축에 못 붙고 비스듬히 도착한다(시뮬레이션: 30° 기울임에서 5.8cm 잔차).
    # 이 항을 넣으면 2.3cm 로 줄고, 축 위(axis_lat=0)에서는 0 이라 직선 접근을 방해하지 않는다.
    kp_cross: float = Field(0.35, ge=0.0, le=3.0)
    kp_lin: float = Field(0.60, ge=0.0, le=5.0)
    ang_max: float = Field(0.07, ge=0.01, le=1.0)
    lin_max: float = Field(0.13, ge=0.02, le=1.0)
    turn_priority_rad: float = Field(0.90, ge=0.05, le=2.0)   # 이 각오차에서 전진 0
    min_drive: int = Field(45, ge=0, le=70)                   # peak 바퀴를 여기까지 끌어올린다
    # 바퀴 하나라도 이 PWM 밑이면 로봇이 안 돈다(실측 2026-07-29, aruco_dock._MIN_DRIVE 와 동일값).
    # _drive 가 이 값을 기준으로 '곡선 불가' 구간을 회전/직진으로 분해한다.
    stall_pwm: int = Field(32, ge=0, le=70)
    # 안쪽 바퀴가 stall_pwm 을 '넘도록' 얹는 여유. 0 이면 경계에 딱 붙어 실제로는 안 굴러간다.
    stall_margin: int = Field(5, ge=0, le=20)
    # 제자리 회전 펄스 듀티. min_drive 가 stall 위라 연속 회전은 무조건 빠르다 → 돌고-멈춤으로 낸다.
    #
    # ★ 각속도를 '크기'로는 못 줄인다. 회전 구간에서 _drive 는 peak 를 항상 min_drive 까지
    #   끌어올리므로 ang 을 아무리 작게 줘도 PWM 은 ±45 로 나간다. 즉 평균 각속도를 정하는
    #   것은 오직 듀티비 = pulse/(pulse+pause) 다.
    #   2026-08-03: 0.16/0.34(듀티 32%)에서 "확확 돈다"는 관찰 — 근접 정렬 중 22.7° 를
    #   넘겨 지나쳐 마커를 놓쳤다. 먼저 pause 만 늘려 듀티를 20% 로 낮췄으나 체감이
    #   그대로였다. 당연한 결과다 — 듀티는 '얼마나 자주 도는가'만 바꾸고, 한 펄스가
    #   내는 각도는 그대로라 '확' 하는 크기가 안 줄어든다.
    #   그래서 pulse 폭을 줄여 **한 번에 도는 각도**를 깎는다(0.16→0.10→0.08).
    #   ⚠️ 펄스가 짧을수록 정지마찰을 못 깨고 '아예 안 도는' 쪽으로 넘어갈 수 있다.
    #      회전이 멈춰 버리면 이 값을 되돌릴 것 — 실측된 하한은 아직 없다.
    turn_pulse_s: float = Field(0.08, ge=0.0, le=1.0)
    turn_pause_s: float = Field(0.70, ge=0.0, le=2.0)
    # 제자리 회전에 쓰는 PWM. min_drive(45)는 '전진 시 안쪽 바퀴까지 굴리기 위한' 값이라
    # 회전에는 과하다 — 회전은 양 바퀴가 대칭이라 stall 위이기만 하면 된다.
    # stall_pwm(32)보다 확실히 위이면서 45보다 낮게 잡아 회전 속도를 직접 낮춘다.
    turn_pwm: int = Field(38, ge=0, le=70)
    # 이 거리 안쪽에서 마커를 놓치면 '분실'이 아니라 '도착'으로 본다.
    # 카메라가 위를 향해 있어 근접하면 마커가 화각 아래로 빠진다 — 정상이며, 이때 찾아 헤매면 안 된다.
    near_done_m: float = Field(0.32, ge=0.05, le=1.0)
    # 경유점 방위각을 마커 방위각 ±이 값 안으로 제한 → 마커가 화각 밖으로 나가지 않게 한다.
    # 이 카메라 수평 반화각은 atan(240/471)≈27°. 제한이 없으면 회전 중 마커를 놓친다.
    bearing_limit_deg: float = Field(20.0, ge=5.0, le=60.0)
    cam_x_sign: float = Field(1.0, ge=-1.0, le=1.0)           # 조향 극성 (부호 규약 참고)

    # 무보정(캘리브 없음) 대체 제어
    target_size: float = Field(0.40, ge=0.05, le=0.95)
    size_tol: float = Field(0.04, ge=0.01, le=0.3)

    # 안전
    use_wall_sensor: bool = True
    target_wall_cm: float = Field(8.0, ge=3.0, le=80.0)
    slow_wall_cm: float = Field(18.0, ge=5.0, le=120.0)
    # IR 은 바닥 테이프 센서라 장애물을 못 본다 → 기본 끔. 위 _park_loop 주석 참고.
    stop_on_ir: bool = False

    # 메모리 큐 / 분실 복구
    memory_len: int = Field(60, ge=5, le=500)
    recall_samples: int = Field(7, ge=1, le=50)               # 중앙값에 쓸 최근 샘플 수
    recall_enable: bool = True
    kp_recall: float = Field(0.35, ge=0.0, le=5.0)
    recall_ang_max: float = Field(0.07, ge=0.01, le=1.0)
    recall_timeout_s: float = Field(12.0, ge=1.0, le=60.0)
    # 카메라 전용 복구에서 한 방향으로 훑는 시간. 지나면 반대 방향으로 더 넓게 훑는다.
    recall_sweep_s: float = Field(2.5, ge=0.3, le=20.0)
    # odom 기반 전역좌표 복구 사용 여부. 위 _park_loop 주석의 실측 이슈 때문에 기본 꺼짐.
    recall_use_odom: bool = False
    recall_creep_lin: float = Field(0.10, ge=0.0, le=0.4)     # 회전으로 못 찾을 때 전진 속도
    lost_grace: int = Field(6, ge=1, le=60)                   # 이 프레임까지는 그냥 정지 대기

    loop_hz: float = Field(12.0, ge=2.0, le=30.0)
    timeout_s: float = Field(120.0, ge=2.0, le=600.0)


# ── 상태 / 메모리 큐 ─────────────────────────────────────────────────────────
_task: asyncio.Task | None = None
_memory: deque[dict[str, Any]] = deque(maxlen=60)
_state: dict[str, Any] = {
    "running": False, "phase": "idle", "message": "",
    "telemetry": {}, "path": [], "memory_len": 0,
}


def _reset_memory(maxlen: int) -> None:
    """메모리 큐를 비우고 길이를 재설정한다(주차 시작마다 호출)."""
    global _memory
    _memory = deque(maxlen=maxlen)


def _odom() -> tuple[float, float, float] | None:
    """로봇 전역 pose (x, y, yaw). odom 우선, 없으면 TF(map->base_link)."""
    if ros_bridge is None:
        return None
    for key in ("odom", "tf_pose"):
        try:
            d = ros_bridge.get_topic(key)
        except Exception:  # noqa: BLE001
            d = None
        if isinstance(d, dict) and d.get("x") is not None and d.get("yaw") is not None:
            return float(d["x"]), float(d["y"]), float(d["yaw"])
    return None


def _to_global(p: dict[str, float], odom: tuple[float, float, float],
               cam_x_sign: float) -> dict[str, float]:
    """카메라 기준 마커 위치 → odom 전역 좌표 (gx, gy) + 법선 방위 gz.

    로봇 좌표계는 ROS REP-103 (x 전방, y 좌측, yaw CCW).
    카메라 z = 전방, 카메라 x = cam_x_sign 만큼 로봇 좌측.
    """
    rx, ry, ryaw = odom
    fwd = p["z_m"]
    lft = cam_x_sign * p["x_m"]
    c, s = math.cos(ryaw), math.sin(ryaw)
    return {
        "gx": rx + fwd * c - lft * s,
        "gy": ry + fwd * s + lft * c,
        # 마커 법선(카메라 쪽을 향함)의 전역 방위각
        "gz": _norm(ryaw + math.atan2(cam_x_sign * p["nx"], p["nz"])),
    }


def _remember(p: dict[str, float], proj: dict[str, float], cfg: ParkPConfig) -> dict[str, Any]:
    """검출 1건을 메모리 큐에 적재하고 그 샘플을 돌려준다."""
    odom = _odom()
    sample: dict[str, Any] = {
        "t": round(time.time(), 3),
        "x": round(p["x_m"], 4), "y": round(p["y_m"], 4), "z": round(p["z_m"], 4),
        "gx": None, "gy": None, "gz": None,
        "axis_lat": round(proj["axis_lat"], 4),
        "bearing": round(proj["bearing"], 4),
    }
    if odom is not None:
        g = _to_global(p, odom, cfg.cam_x_sign)
        sample.update(gx=round(g["gx"], 4), gy=round(g["gy"], 4), gz=round(g["gz"], 4))
        sample["rx"], sample["ry"], sample["ryaw"] = (round(v, 4) for v in odom)
    _memory.append(sample)
    return sample


def _recalled_marker(n: int) -> tuple[float, float, float] | None:
    """최근 n 개 샘플의 중앙값으로 마커 전역 위치를 복원한다(단발 튐 방지)."""
    pts = [s for s in list(_memory)[-n:] if s.get("gx") is not None]
    if not pts:
        return None
    gx = float(np.median([s["gx"] for s in pts]))
    gy = float(np.median([s["gy"] for s in pts]))
    gz = float(np.median([s["gz"] for s in pts]))
    return gx, gy, gz


def _virtual_path(p: dict[str, float], proj: dict[str, float], cfg: ParkPConfig) -> list[dict[str, float]]:
    """가상 경로 — 로봇 → 진입 경유점 → 정지점. 전역 좌표가 있으면 함께 넣는다.

    마커를 놓쳐도 이 경로가 남아 있어 '어디로 가려던 참이었는지' 를 잃지 않는다.
    """
    odom = _odom()
    x, z, nx, nz = p["x_m"], p["z_m"], p["nx"], p["nz"]
    stop = {"x_m": x + nx * cfg.target_distance_m, "z_m": z + nz * cfg.target_distance_m}
    local = [
        {"x_m": 0.0, "z_m": 0.0, "label": "robot"},
        {"x_m": proj["wx"], "z_m": proj["wz"], "label": "approach"},
        {"x_m": stop["x_m"], "z_m": stop["z_m"], "label": "stop"},
        {"x_m": x, "z_m": z, "label": "marker"},
    ]
    out: list[dict[str, float]] = []
    for pt in local:
        item = {"x_m": round(pt["x_m"], 4), "z_m": round(pt["z_m"], 4), "label": pt["label"]}
        if odom is not None:
            g = _to_global({"x_m": pt["x_m"], "z_m": pt["z_m"], "nx": nx, "nz": nz}, odom, cfg.cam_x_sign)
            item["gx"], item["gy"] = round(g["gx"], 4), round(g["gy"], 4)
        out.append(item)
    return out


# ── 파이프라인 1~3 단계를 한 번 돌린다 (모터 미동작) ──────────────────────────
def _perceive(cfg: ParkPConfig) -> dict[str, Any]:
    frame = _grab_frame()
    if frame is None:
        raise HTTPException(503, "카메라 프레임 없음 — 카메라 스트림을 먼저 시작하세요.")
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detect_markers_robust(gray, _get_dictionary(cfg.dictionary), _get_params())
    calib = _load_calib((w, h))

    seen: list[int] = [] if ids is None else [int(i) for i in ids.flatten()]
    target = None
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            if int(i) == cfg.marker_id:
                # 같은 id 가 여러 개면 가장 큰(가까운) 것
                if target is None or _measure(c, w, h)["size_frac"] > _measure(target, w, h)["size_frac"]:
                    target = c

    out: dict[str, Any] = {
        "frame": {"width": w, "height": h},
        "calibrated": calib is not None,
        "marker_id": cfg.marker_id,
        "seen_ids": seen,
        "found": target is not None,
        "detect": None, "pose": None, "projection": None, "path": [],
    }
    if target is None:
        return out

    m = _measure(target, w, h)
    out["detect"] = {**{k: round(v, 4) for k, v in m.items()},
                     "corners": target.reshape(4, 2).astype(float).round(1).tolist()}
    if calib is None:
        return out

    p = _pose3(target, calib, cfg.marker_len_m, h)
    if p is None:
        return out
    proj = _project(p, cfg.target_distance_m, cfg.standoff_m)
    out["pose"] = {k: round(v, 4) for k, v in p.items()}
    out["projection"] = {k: round(v, 4) for k, v in proj.items()}
    out["projection"]["bearing_deg"] = round(math.degrees(proj["bearing"]), 2)
    out["projection"]["marker_bearing_deg"] = round(math.degrees(proj["marker_bearing"]), 2)
    out["path"] = _virtual_path(p, proj, cfg)
    return out


# ── 4단계: P 제어 주행 루프 ──────────────────────────────────────────────────
async def _drive(linear: float, angular: float, cfg: ParkPConfig) -> tuple[int, int]:
    """정규화 (linear, angular) → 모터 PWM. 단, 이 구동계의 정지마찰을 반드시 존중한다.

    ★ 2026-07-29 실측으로 확인한 하드웨어 제약:
      _vel_to_speeds 는 left=(lin-ang)·75, right=(lin+ang)·75 이고, 아래 데드밴드 보정은
      **peak 만** min_drive 로 끌어올린다. 안쪽 바퀴 비율 r=(lin-|ang|)/(lin+|ang|) 는
      그대로 남으므로 안쪽 = min_drive·r 이 정지마찰(≈stall_pwm) 밑이면 **로봇이 아예 안 움직인다.**
      실측: lin 0.045 / ang 0.03 → r=0.2 → L=32,R=6 → 12초간 완전 정지(카메라로 확인).
      즉 이 로봇은 '저속 곡선 주행'이 물리적으로 불가능하다.

    그래서 곡선이 성립하지 않는 구간에서는 곡선을 포기하고 둘 중 하나로 떨어뜨린다.
      · 회전 의도가 우세하면 → 순수 제자리 회전 (lin=0, 양 바퀴 ±min_drive)
      · 전진 의도가 우세하면 → angular 를 줄여 안쪽 바퀴가 정지마찰을 넘게 만든다
    결과적으로 '회전 → 직진'으로 자연스럽게 분해되며, 어느 쪽이든 양 바퀴가 모두 stall 위에 있다.
    """
    lin, ang = float(linear), float(angular)
    # ★ need 를 stall_pwm 그대로 쓰면 보정 결과가 안쪽 바퀴 = stall_pwm '정확히 경계'가 된다.
    #   경계는 못 넘는 것과 같다 — 2026-08-03 실측: L32/R45 로 75초에 전진 1.5cm,
    #   yaw 만 6° 쌓이다 마커를 흘렸다(L32 는 stall_pwm 32 와 동일값). 마진을 얹어
    #   안쪽 바퀴가 정지마찰을 확실히 넘게 만든다.
    need = (cfg.stall_pwm + cfg.stall_margin) / max(cfg.min_drive, 1)
    if lin > 0 and abs(ang) > 0 and need < 1.0:
        ratio = (lin - abs(ang)) / (lin + abs(ang))
        if ratio < need:
            if abs(ang) >= lin:
                lin = 0.0                                   # 회전 우세 → 순수 제자리 회전
            else:
                cap = lin * (1.0 - need) / (1.0 + need)     # 전진 우세 → 조향을 깎는다
                ang = math.copysign(min(abs(ang), cap), ang)
    # ★ 제자리 회전은 '펄스(돌고-멈춤)'로 낸다.
    #   min_drive 를 정지마찰 위(45)로 올린 대가로 **최저 속도가 곧 최고 속도**가 됐다.
    #   연속으로 주면 미세 조정이 불가능해 좌우로 폭주한다(실측 2026-07-29: recall 중 ±45 연속).
    #   순간 PWM 은 stall 위로 유지하면서 평균 각속도만 듀티비로 낮춘다.
    #   (기존 aruco_dock / park_dock 도 같은 이유로 turn_pulse/turn_pause 를 쓴다)
    if lin == 0.0 and ang != 0.0 and cfg.turn_pulse_s > 0:
        cycle = cfg.turn_pulse_s + cfg.turn_pause_s
        if (time.time() % cycle) > cfg.turn_pulse_s:
            await _motor_send(0, 0)
            return 0, 0

    # ⚠️ 데드밴드 보정은 반드시 **정수화 전(float)** 에 해야 한다.
    #    _vel_to_speeds 는 int() 로 잘라버리는데, 저속 구간에서는 값이 한 자릿수라
    #    (2.925, 3.825) → (2, 3) 처럼 좌우 비율이 0.765 → 0.667 로 붕괴한다.
    #    그 상태로 peak 만 min_drive 로 올리면 안쪽 바퀴가 다시 stall 밑으로 떨어진다.
    lf = (lin - ang) * _MAX_SPEED
    rf = (lin + ang) * _MAX_SPEED
    peak = max(abs(lf), abs(rf))
    # 순수 제자리 회전은 전진과 달리 안쪽 바퀴 비율 문제가 없다 — 양 바퀴가 같은 크기로
    # 반대로 돌 뿐이라, stall 위이기만 하면 된다. min_drive(45)까지 끌어올릴 이유가 없고
    # 그게 '확확 도는' 원인이었다(2026-08-03). 회전에는 더 낮은 turn_pwm 을 쓴다.
    floor_pwm = cfg.turn_pwm if lin == 0.0 and ang != 0.0 else cfg.min_drive
    if 0 < peak < floor_pwm:                # 데드밴드 보정 — 방향비는 유지
        k = floor_pwm / peak
        lf, rf = lf * k, rf * k
    l = max(-100, min(100, int(round(lf))))
    r = max(-100, min(100, int(round(rf))))
    await _motor_send(l, r)
    return l, r


async def _park_loop(cfg: ParkPConfig) -> None:
    dt = 1.0 / cfg.loop_hz
    dictionary, params = _get_dictionary(cfg.dictionary), _get_params()
    # K 는 프레임 해상도에 맞춰 스케일해야 하므로 첫 프레임을 받은 뒤에 로드한다.
    calib: tuple[np.ndarray, np.ndarray] | None = None
    calib_wh: tuple[int, int] | None = None
    started = time.time()
    lost = 0
    recall_since: float | None = None
    last_range: float | None = None   # 마지막으로 본 마커까지 거리 — 근접 분실 판정에 쓴다
    last_bearing: float = 0.0        # 마지막으로 본 마커 방위각(rad) — 카메라 전용 복구 방향
    last_axis_lat: float = 0.0       # 마지막으로 본 정면축 이탈(m) — 근접 완료의 정렬 판정에 쓴다
    # 곡선 주행이 불가능한 구동계라 접근을 회전/직진으로 분해한다. 상세는 §4 주석 참고.
    # ⚠️ 2026-08-03: 켠 직후 분실이 잦아졌다는 관찰이 있어 기본값을 끔으로 되돌렸다.
    #    align_wp 가 제자리 회전만 하는 구간이라 회전 중 마커를 흘리면 그대로 recall 로 간다.
    #    stage_approach=True 로 다시 켤 수 있다.
    stage = "align_wp" if cfg.stage_approach else "approach"
    last_wall_cm: float | None = None  # 마지막으로 **유효했던** 초음파 값(cm)

    # 캐시를 채우는 건 센서 상주 데몬이다. 루프 안에서는 캐시만 읽으므로(논블로킹),
    # 데몬은 여기서 미리 띄워 둔다 — 안 띄우면 캐시가 영원히 비어 벽 정지가 안 걸린다.
    if cfg.use_wall_sensor:
        try:
            await _ensure_sensor_daemon()
        except Exception as exc:  # noqa: BLE001 — 센서가 없어도 마커 기준 주차는 계속한다
            _state["message"] = f"초음파 데몬 기동 실패({exc}) — 마커 거리로만 정지합니다"

    try:
        while True:
            if time.time() - started > cfg.timeout_s:
                _state.update(phase="timeout", message="시간 초과로 중단")
                break

            # ── 1) 탐지 ──
            frame = _grab_frame()
            if frame is None:
                _state.update(phase="no_frame", message="카메라 프레임 없음")
                await _motor_send(0, 0)
                await asyncio.sleep(dt)
                continue

            h, w = frame.shape[:2]
            if calib_wh != (w, h):        # 첫 프레임 / 해상도가 바뀐 경우에만 다시 만든다
                calib, calib_wh = _load_calib((w, h)), (w, h)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = _detect_markers_robust(gray, dictionary, params)
            target = None
            if ids is not None:
                for c, i in zip(corners, ids.flatten()):
                    if int(i) == cfg.marker_id:
                        if target is None or _measure(c, w, h)["size_frac"] > _measure(target, w, h)["size_frac"]:
                            target = c

            # ── 마커 분실: 메모리 큐로 복구 회전 ──
            if target is None:
                lost += 1
                # ★ 근접에서의 분실은 '도착'이다. 카메라가 위를 향해 있어 마커에 가까워지면
                #   마커가 화각 아래로 빠진다 — 정상 현상이다. 이걸 분실로 보고 복구 회전을 걸면
                #   다 온 로봇이 제자리에서 좌우로 헤맨다(실측 2026-07-29).
                if last_range is not None and last_range <= cfg.near_done_m:
                    await _motor_send(0, 0)
                    # ★ 거리만 보고 완료로 부르면 안 된다. 거리는 들어왔는데 정렬이 덜 끝난
                    #   상태로 마커를 흘려도 전부 '주차 완료'가 됐다(2026-08-03 실측:
                    #   방위 22.7°/축이탈 3.1cm, 방위 -2.1°/축이탈 8.9cm 두 판 모두 done).
                    #   자세가 허용치 밖이면 실패로 구분해 성공률 집계를 오염시키지 않는다.
                    bearing_off = abs(math.degrees(last_bearing))
                    lat_off = abs(last_axis_lat)
                    if bearing_off <= cfg.bearing_tol_deg and lat_off <= cfg.axis_tol_m:
                        _state.update(
                            phase="done",
                            message=f"근접({round(last_range, 2)}m)에서 마커가 화각을 벗어남 — 주차 완료",
                        )
                    else:
                        _state.update(
                            phase="misaligned",
                            message=(f"근접({round(last_range, 2)}m) 도달했으나 정렬 미완 — "
                                     f"방위 {bearing_off:.1f}°(허용 {cfg.bearing_tol_deg:g}°), "
                                     f"축이탈 {lat_off * 100:.1f}cm(허용 {cfg.axis_tol_m * 100:g}cm)"),
                        )
                    break
                if lost <= cfg.lost_grace:
                    await _motor_send(0, 0)
                    _state.update(phase="coast", message="마커 순간 놓침 — 대기")
                    await asyncio.sleep(dt)
                    continue
                if not cfg.recall_enable:
                    _state.update(phase="lost", message="마커 상실 — 중단")
                    break
                if recall_since is None:
                    recall_since = time.time()
                if time.time() - recall_since > cfg.recall_timeout_s:
                    _state.update(phase="lost", message="메모리 큐로도 마커를 못 찾음 — 중단")
                    break

                # ★ 카메라 전용 복구를 기본으로 쓴다(recall_use_odom=False).
                #   odom 기반 전역좌표 복구는 실측에서 마커 위치를 엉뚱하게 복원했다
                #   (2026-07-29: 로봇 (1.761,0.522) yaw -31.9°, 실제 마커는 앞쪽 0.2~0.8m 인데
                #    recalled=(1.707,-0.644) = 뒤쪽 -y 1.17m → -60.8° 로 헛돌다 timeout).
                #   같은 시점 path 의 gx/gy 는 정상이라 _to_global/odom 조합에 부호·드리프트
                #   문제가 있는 것으로 보인다. 원인 규명 전까지는 켜지 않는다.
                recalled = _recalled_marker(cfg.recall_samples) if cfg.recall_use_odom else None
                odom = _odom() if cfg.recall_use_odom else None
                if recalled is None or odom is None:
                    # ★ odom 이 없다고 포기하면 안 된다. 주행 중 분실은 정상적으로 일어나고,
                    #   그때마다 중단되면 주차가 성립하지 않는다(실측 2026-07-29: memory_len=35 인데
                    #   전부 gx=None — ros_bridge 의 odom/tf_pose 가 비어 전역 좌표를 못 만들었다).
                    #   도킹 복구가 ROS 스택 건강 상태에 의존해선 안 되므로, 카메라 정보만으로 복구한다.
                    #   마커는 마지막으로 본 '쪽'으로 프레임을 벗어난다 → 그 방향으로 펄스 회전하며 훑고,
                    #   일정 시간 안에 못 찾으면 반대로 더 넓게 훑는다(부호만 쓰므로 odom 불필요).
                    elapsed = time.time() - recall_since
                    direction = 1.0 if last_bearing >= 0 else -1.0
                    sweeps = int(elapsed // max(cfg.recall_sweep_s, 0.1))
                    if sweeps % 2 == 1:
                        direction = -direction          # 한 방향으로 훑고 못 찾으면 반대로
                    ang = cfg.cam_x_sign * math.copysign(cfg.recall_ang_max, direction)
                    l, r = await _drive(0.0, ang, cfg)
                    # ★ 좌/우 표기는 **로봇 기준**으로 낸다. 부호 규약상 카메라 이미지 +x 는
                    #   로봇의 왼쪽이다(모듈 docstring: "로봇 좌표계에서 '왼쪽' = cam_x_sign·x_m").
                    #   direction>0 → 이미지 +x 쪽으로 회전 = 로봇 좌회전이므로 '좌' 다.
                    #   이전에는 이걸 '우' 로 찍어 화면과 실제가 정반대였다(2026-08-03 지적).
                    side = "좌" if direction > 0 else "우"
                    # 스윕이 뒤집힌 회차(홀수)에는 '마지막으로 본 쪽'이 아니라 그 반대쪽을 훑는다.
                    origin = "마지막으로 본 쪽" if sweeps % 2 == 0 else "반대쪽"
                    _state.update(
                        phase="recall_cam",
                        message=(f"마커 분실 — {origin}({side})으로 펄스 회전 탐색 중 "
                                 f"({elapsed:.0f}/{cfg.recall_timeout_s:.0f}s)"),
                    )
                    _state["telemetry"] = {
                        "recall_mode": "camera",
                        "last_bearing_deg": round(math.degrees(last_bearing), 1),
                        "sweep": sweeps, "direction": direction,
                        "angular": round(ang, 3), "left": l, "right": r,
                    }
                    await asyncio.sleep(dt)
                    continue
                gx, gy, _gz = recalled
                rx, ry, ryaw = odom
                err = _norm(math.atan2(gy - ry, gx - rx) - ryaw)
                # 로봇 좌측(+err)으로 도는 각속도 부호를 카메라 규약에 맞춰 되돌린다.
                ang = cfg.cam_x_sign * _clamp(cfg.kp_recall * err,
                                              -cfg.recall_ang_max, cfg.recall_ang_max)
                # 이미 그 방향을 보고 있는데도 안 보이면 기억한 경로 쪽으로 저속 전진
                lin = cfg.recall_creep_lin if abs(err) < math.radians(cfg.bearing_tol_deg) else 0.0
                l, r = await _drive(lin, ang, cfg)
                _state.update(phase="recall",
                              message=f"메모리 큐 복구 — 마커 방향으로 {math.degrees(err):+.0f}° 회전")
                _state["telemetry"] = {
                    "recall_err_deg": round(math.degrees(err), 1),
                    "recalled": {"gx": round(gx, 3), "gy": round(gy, 3)},
                    "odom": {"x": round(rx, 3), "y": round(ry, 3), "yaw": round(ryaw, 3)},
                    "linear": round(lin, 3), "angular": round(ang, 3), "left": l, "right": r,
                }
                _state["memory_len"] = len(_memory)
                await asyncio.sleep(dt)
                continue

            lost = 0
            recall_since = None

            # ── 2) 계산 ──
            m = _measure(target, w, h)

            # ── 3) depth + 투영 ──
            p = _pose3(target, calib, cfg.marker_len_m, h) if calib is not None else None
            if p is not None:
                proj = _project(p, cfg.target_distance_m, cfg.standoff_m)
                sample = _remember(p, proj, cfg)
                _state["path"] = _virtual_path(p, proj, cfg)

                # 경유점 방위각을 마커 방위각 ±bearing_limit 안으로 제한 → 화각 이탈 방지.
                lim = math.radians(cfg.bearing_limit_deg)
                mb = proj["marker_bearing"]
                bearing = _clamp(proj["bearing"], mb - lim, mb + lim)
                # 남은 거리도 z 가 아니라 range 로 재야 회전 시 값이 흔들리지 않는다.
                last_range = proj["marker_range"]
                last_bearing = proj["marker_bearing"]
                last_axis_lat = proj["axis_lat"]
                dist_err = proj["marker_range"] - cfg.target_distance_m
                dist_ok = abs(dist_err) <= cfg.dist_tol_m
                src, dist_show = "pose_m", round(proj["marker_range"], 3)
            else:
                # 무보정 대체: 화면 오차/크기로만 제어 (투영 없음)
                # bearing 0 / axis_lat 0 이라 아래 단계 분해는 즉시 approach 로 떨어진다
                # — 투영이 없으면 경유점 개념 자체가 없으므로 기존 제어를 그대로 쓴다.
                proj = {"wx": 0.0, "wz": 1.0, "axis_lat": 0.0, "d_hold": 0.0,
                        "marker_bearing": 0.0, "range": 0.0, "bearing": 0.0}
                sample = None
                bearing = m["ex"] * 0.6            # ex(-1..1) → 대략적인 각오차(rad)
                dist_err = cfg.target_size - m["size_frac"]
                dist_ok = abs(dist_err) <= cfg.size_tol
                src, dist_show = "marker_size", round(m["size_frac"], 3)

            bearing_ok = abs(bearing) <= math.radians(cfg.bearing_tol_deg)

            # ── 4) 제어 ──
            # ★ 이 구동계는 곡선 주행이 사실상 불가능하다. _drive 의 정지마찰 보정 때문에
            #   전진 중 조향 상한이 |ang| ≤ lin·(1-need)/(1+need) = lin·0.169 로 묶이고,
            #   실제로 접근 종료 시 L32/R45 — 안쪽 바퀴가 stall 에 붙은 한계 상태였다.
            #   그 곡률로는 남은 0.3m 안에서 축이탈 7.4cm 를 지울 수 없다(2026-08-03 실측).
            #   제자리 회전으로는 축이탈이 줄지 않으므로(돌아도 옆으로 안 감), 곡선 대신
            #   **회전 → 직진**으로 분해한다.
            #     align_wp : 경유점을 향해 순수 회전   (linear = 0)
            #     drive_wp : 경유점까지 순수 직진      (angular = 0) — 여기서 축이탈이 사라진다
            #     approach : 기존 P 제어로 마무리      (3~4단계는 아직 미분리)
            wp_bearing = proj["bearing"]          # 경유점 방위 (bearing_limit 클램프 전 원값)
            reenter = math.radians(cfg.bearing_tol_deg * 2.0)   # 히스테리시스 — 경계 진동 방지
            turn_scale = 1.0
            steer = 0.0                           # approach 단계에서만 채워진다(텔레메트리용)

            if stage == "align_wp":
                if abs(wp_bearing) > math.radians(cfg.bearing_tol_deg):
                    linear = 0.0
                    angular = cfg.cam_x_sign * math.copysign(cfg.ang_max, wp_bearing)
                else:
                    stage = "drive_wp"

            if stage == "drive_wp":
                if abs(wp_bearing) > reenter:      # 직진 중 틀어졌으면 다시 정렬부터
                    stage = "align_wp"
                    linear = 0.0
                    angular = cfg.cam_x_sign * math.copysign(cfg.ang_max, wp_bearing)
                elif abs(proj["axis_lat"]) > cfg.axis_tol_m:
                    linear = _clamp(cfg.kp_lin * dist_err, -cfg.lin_max, cfg.lin_max)
                    angular = 0.0
                else:
                    stage = "approach"             # 축 위에 올라탔다 → 기존 제어로 인계

            if stage == "approach":
                # 정면축 위에 정면으로 서 있으면 bearing≈0, axis_lat≈0 → angular≈0 → 그대로 직진.
                steer = cfg.kp_ang * bearing - cfg.kp_cross * proj["axis_lat"]
                angular = cfg.cam_x_sign * _clamp(steer, -cfg.ang_max, cfg.ang_max)
                turn_scale = max(0.0, 1.0 - abs(bearing) / cfg.turn_priority_rad)
                linear = _clamp(cfg.kp_lin * dist_err, -cfg.lin_max, cfg.lin_max) * turn_scale

            # ── 정지 판단: 전진 중에만 초음파/IR 을 본다 ──
            # ★ target_wall_cm 은 이름 그대로 '목표 벽 거리'다. 위험 임계값이 아니라 **완료 조건**이다.
            #   여기 도달하면 주차가 성공한 것이므로 '안전 정지'가 아니라 '주차 완료'로 찍어야 한다
            #   (2026-07-29: 잘 도착해 놓고 "초음파 안전 정지" 로 떠서 실패처럼 보였다).
            #   진짜 안전 정지는 예기치 못한 장애물 신호(ir obstacle)일 때만이다.
            wall_cm: float | None = None
            ir_hit = False
            wall_done = False
            wall_blind_stop = False
            if cfg.use_wall_sensor and linear > 0:
                # ★ 캐시만 읽는다(논블로킹). _read_dist() 는 캐시가 비거나 오래되면
                #   `sudo python3 sensor_ctrl.py ultrasonic` 을 띄워 최대 6초를 기다린다.
                #   그 await 동안 이 루프는 멈추지만 **모터는 직전 명령대로 계속 굴러간다**
                #   (전진은 회전과 달리 펄스가 아니다) — 3cm 앞에서 서야 할 로봇이
                #   그대로 벽을 밀고 들어간다. 센서 데몬은 0.1초마다 값을 갱신하므로
                #   제어 루프(12Hz)에는 캐시만으로 충분하다.
                wall_cm = _read_dist_cached()
                if wall_cm is not None:
                    last_wall_cm = wall_cm
                    if wall_cm <= cfg.slow_wall_cm:
                        linear = min(linear, cfg.lin_max * 0.45)
                    if wall_cm <= cfg.target_wall_cm:
                        wall_done = True
                elif last_wall_cm is not None and last_wall_cm <= cfg.slow_wall_cm:
                    # 초음파는 최소 측정거리(≈2cm) 밑이거나 반사가 빗나가면 0/무효를 낸다
                    #   (driving._read_dist_cached 가 0 을 None 으로 돌려준다).
                    #   목표가 3cm 라 '거의 다 왔을 때' 정확히 이 구간에 들어간다.
                    #   감속 구간까지 붙어 있다가 값을 잃었다 = 최소거리 밑이므로 멈춘다.
                    #   (아직 멀리 있을 때의 무효값은 그냥 다음 프레임을 기다린다)
                    wall_blind_stop = True
                if cfg.stop_on_ir:
                    ir = await _read_ir()
                    # ★ IR 은 '바닥을 보는 테이프 센서'다. 장애물 센서가 아니다.
                    #   _read_ir() 는 {left, center, right} 아날로그 원값만 준다(실측 236/300/237,
                    #   ir_white_max 800 기준). 원값을 그대로 truthy 로 쓰면 **항상 참**이라
                    #   주차 시작하자마자 "안전 정지"로 끝난다(2026-07-29 실측 버그).
                    #   장애물 판정은 초음파(_read_dist)만 신뢰한다. 센서 API 가 나중에 명시적인
                    #   obstacle 플래그를 주면 그때만 반응하도록 남겨 둔다.
                    ir_hit = bool(ir and ir.get("obstacle"))

            if (dist_ok and bearing_ok) or wall_done or ir_hit or wall_blind_stop:
                await _motor_send(0, 0)
                wall_txt = f"벽 {round(wall_cm, 1)}cm" if wall_cm is not None else "벽 미측정"
                if ir_hit:
                    # 예기치 못한 장애물 — 이것만 진짜 '안전 정지'다.
                    _state.update(phase="safety_stop", message=f"장애물 감지 — 안전 정지 ({wall_txt})")
                elif wall_blind_stop:
                    # 감속 구간까지 붙은 뒤 초음파가 값을 잃었다 = 최소 측정거리 밑.
                    # 더 가면 벽을 민다. 목표에 '도달했다'고 단정하지 않고 그렇게 적는다.
                    _state.update(
                        phase="done",
                        message=(f"주차 완료 — 초음파 최소거리 밑(마지막 {round(last_wall_cm, 1)}cm)"
                                 f"에서 정지. 목표 {cfg.target_wall_cm}cm"),
                    )
                elif dist_ok and bearing_ok:
                    _state.update(
                        phase="done",
                        message=f"주차 완료 — 마커 정면 {round(proj['marker_range'], 2)}m ({wall_txt})"
                        if p is not None else f"주차 완료 ({wall_txt})",
                    )
                else:
                    _state.update(phase="done", message=f"주차 완료 — {wall_txt} 정위치 도달")
                break

            l, r = await _drive(linear, angular, cfg)

            _state["phase"] = "parking"
            _state["message"] = "P 제어 접근 중"
            _state["memory_len"] = len(_memory)
            _state["telemetry"] = {
                "id": cfg.marker_id,
                "ex": round(m["ex"], 3), "skew": round(m["skew"], 3),
                "dist": dist_show, "dist_source": src, "dist_err": round(dist_err, 3),
                "bearing_deg": round(math.degrees(bearing), 2),
                "marker_bearing_deg": round(math.degrees(proj["marker_bearing"]), 2),
                "axis_lat": round(proj["axis_lat"], 4),
                "waypoint": {"x_m": round(proj["wx"], 3), "z_m": round(proj["wz"], 3),
                             "d_hold": round(proj["d_hold"], 3)},
                "pose": ({k: round(v, 4) for k, v in p.items()} if p else None),
                "sample": sample,
                "linear": round(linear, 3), "angular": round(angular, 3),
                "stage": stage,
                "wp_bearing_deg": round(math.degrees(proj["bearing"]), 2),
                "steer_raw": round(steer, 4), "turn_scale": round(turn_scale, 3),
                "left": l, "right": r,
                "bearing_ok": bearing_ok, "dist_ok": dist_ok,
                "wall_cm": wall_cm, "ir_hit": ir_hit,
                # 마지막 유효값도 같이 낸다 — wall_cm 이 None 일 때 '얼마에서 잃었는지'가
                # 화면에 보여야 '왜 안 섰나'를 사후에 로그로 캐지 않는다.
                "wall_last_cm": last_wall_cm,
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


# ── 엔드포인트 ───────────────────────────────────────────────────────────────
@router.get("/parkp/plan")
async def plan(
    marker_id: int,
    dictionary: str = "DICT_4X4_50",
    marker_len_m: float = 0.05,
    target_distance_m: float = 0.20,
    standoff_m: float = 0.40,
    cam_x_sign: float = 1.0,
):
    """1~3 단계(탐지·계산·depth+투영)만 실행해 계획을 돌려준다. 모터 미동작."""
    if dictionary not in _ARUCO_DICTS:
        raise HTTPException(400, f"지원하지 않는 dictionary: {dictionary}")
    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.3)
    cfg = ParkPConfig(marker_id=marker_id, dictionary=dictionary, marker_len_m=marker_len_m,
                      target_distance_m=target_distance_m, standoff_m=standoff_m,
                      cam_x_sign=cam_x_sign)
    res = await asyncio.to_thread(_perceive, cfg)
    res["odom"] = _odom()
    res["memory"] = list(_memory)[-10:]
    return res


@router.post("/parkp/start")
async def start(cfg: ParkPConfig):
    """4단계 — P 제어 주차 시작. ⚠️ 모터가 실제로 움직인다."""
    global _task
    if _task is not None and not _task.done():
        raise HTTPException(409, "이미 주차가 진행 중입니다.")
    if cfg.dictionary not in _ARUCO_DICTS:
        raise HTTPException(400, f"지원하지 않는 dictionary: {cfg.dictionary}")
    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.3)

    _reset_memory(cfg.memory_len)
    _state.update(running=True, phase="starting", message="",
                  telemetry={}, path=[], memory_len=0)
    _task = asyncio.create_task(_park_loop(cfg))
    return {"success": True, "message": f"id {cfg.marker_id} 마커 P 제어 주차 시작",
            "config": cfg.model_dump()}


@router.post("/parkp/stop")
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
    _state.update(running=False, phase="idle", message="정지됨")
    return {"success": True, "message": "주차 정지"}


@router.get("/parkp/status")
async def status(memory: int = 20):
    return {**_state, "memory": list(_memory)[-memory:], "memory_len": len(_memory)}
