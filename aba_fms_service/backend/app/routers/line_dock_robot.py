"""
라인트레이싱 미세조정 주차 라우터 — robot_agent(로봇 온보드) 용. (2026-07-07 신규)

배경: 아르코 마커만으로는 최종 주차 정밀도가 부족 → 주차 스팟 바닥에 붙인
흰 테이프를 카메라로 추종해 마지막 구간을 미세조정한다.

기존 aruco_dock.py 와 완전히 분리된 모듈이다(로봇별 실측 튜닝이 다른 파일을
건드리지 않기 위함). 조향 부호(_STEER_SIGN)·최소 구동값 같은 로봇별 캘리브레이션은
각 로봇의 aruco_dock 모듈에서 가져와 상속한다(없으면 안전 기본값).

경로(server.py 에서 /api/robot 프리픽스로 장착 → /api/robot/line/*  · 구버전 /api/admin/robot/line/* 별칭 유지):
  GET  /line/detect  — 현재 프레임 라인 검출 결과 (모터 미동작 — 튜닝용)
  POST /line/start   — 라인 추종 미세조정 루프 시작
  POST /line/stop    — 정지(모터 0)
  GET  /line/status  — 상태/텔레메트리

검출 방식: 화면 하단 ROI → 그레이스케일 → 이진화(고정 임계값 또는 Otsu)
→ 상/하 2개 밴드의 흰 픽셀 무게중심으로 좌우 오프셋(offset)과 라인 기울기(angle)를
계산 → 직진 유지용으로만 사용하고, 실제 조향은 하지 않는다. 종료: 전방 초음파 목표 거리 도달(주차 완료),
라인 상실 지속(안전 정지), 타임아웃.
"""
from __future__ import annotations

import asyncio
import math
import subprocess
import time
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.hardware.camera_stream import camera
from app.routers.driving import _motor_send, _vel_to_speeds, _read_dist, _read_ir

router = APIRouter()

# ── 로봇별 캘리브레이션 상속 (aruco_dock 은 로봇마다 실측 튜닝이 다르다) ──────
try:
    from app.routers.aruco_dock import _STEER_SIGN as _DEFAULT_STEER_SIGN
except Exception:
    _DEFAULT_STEER_SIGN = 1.0
try:
    from app.routers.aruco_dock import _MIN_DRIVE as _DEFAULT_MIN_DRIVE
except Exception:
    _DEFAULT_MIN_DRIVE = 30


class LineConfig(BaseModel):
    # ── 검출 ──
    roi_top: float = Field(0.55, ge=0.2, le=0.9)     # ROI 시작(화면 높이 비율) — 하단만 본다
    thresh: int = Field(0, ge=0, le=255)            # 이진화 임계값. 0 = Otsu 자동
    min_area_px: int = Field(120, ge=20, le=5000)     # 밴드당 최소 흰 픽셀(미만이면 라인 없음)
    max_area_ratio: float = Field(0.5, ge=0.1, le=0.9)  # 밴드의 흰 비율이 이보다 크면 과노출/바닥 전체 → 무효
    # ── 직진/조향 ──
    straight_only: bool = False                       # false = IR 3센서로 테이프를 실제 추종(조향). true = 직진만 유지
    # IR 3센서 조향(straight_only=False): 테이프가 좌(우) 센서에 걸리면 좌(우)로 보정.
    # 각속도 + 는 좌회전(오른바퀴 가속) — 테이프가 좌측 센서에 보이면 +.
    steer_soft: float = Field(0.0, ge=0.0, le=0.3)   # 중앙+가장자리 함께 보일 때(살짝 보정)
    steer_hard: float = Field(0.0, ge=0.0, le=0.4)   # 가장자리 센서에만 보일 때(강한 보정)
    steer_lin_scale: float = Field(0.8, ge=0.2, le=1.0)  # 조향 중 전진 감속 비율
    lin: float = Field(0.10, ge=0.03, le=0.3)         # 기본 전진 속도(느리게)
    min_drive: int = Field(0, ge=0, le=70)            # 0 = aruco_dock._MIN_DRIVE 상속
    offset_lpf_alpha: float = Field(0.5, ge=0.05, le=1.0)  # 검출 노이즈 LPF
    # ── 종료 조건 ──
    use_wall_sensor: bool = True
    target_wall_cm: float = Field(3.0, ge=2.0, le=80.0)   # 이 거리 도달 시 주차 완료
    hard_stop_wall_cm: float = Field(3.0, ge=2.0, le=80.0)  # 안전 하한: 목표보다 늦어지면 여기서 강제 정지
    slow_wall_cm: float = Field(18.0, ge=5.0, le=150.0)   # 이 안쪽부터 감속
    lost_grace: int = Field(10, ge=1, le=60)              # 라인 상실 허용 프레임(그동안 직진 유지)
    max_lost_s: float = Field(4.0, ge=0.5, le=30.0)       # 상실 지속 시 안전 정지
    # ── IR 테이프 판정 ──
    # 핑키 IR 은 아날로그 원시값. 흰 테이프는 반사가 강해 값이 뚝 떨어진다
    # (로봇1 실측 2026-07-07: 테이프 위 ~350, 갈색 바닥 1800~2050).
    # 값 ≤ ir_white_max 인 센서 = 테이프 위. 테이프는 스팟으로 들어가는 유도선이라
    # center 가 테이프 위인 건 정상 주행 — stop_ir_sensors 개 이상이 동시에 테이프를
    # 보면(가로 정지선 통과) ir_debounce 프레임 연속일 때 정지한다.
    ir_white_max: int = Field(800, ge=50, le=4000)
    stop_ir_sensors: int = Field(2, ge=1, le=3)
    ir_debounce: int = Field(2, ge=1, le=10)
    # ── 후면 주차 마무리 레코딩 액션 ──
    # 라인 추종으로 벽 target_wall_cm 도달 후, 타이머 기반 고정 시퀀스 실행:
    # 제자리 180° 회전(반대편으로 몸통 전환) → 소폭 후진(꼬리를 벽에 밀착).
    rear_finish: bool = False
    rear_turn_secs: float = Field(2.0, ge=0.2, le=10.0)    # ≈180° 회전 시간(실주행 튜닝값)
    rear_turn_speed: float = Field(0.12, ge=0.03, le=0.5)
    rear_turn_dir: int = Field(1, ge=-1, le=1)             # 1=우회전, -1=좌회전
    rear_turn_min_drive: int = Field(26, ge=0, le=70)      # 제자리 회전 최소 모터 %(정지마찰 극복)
    rear_back_secs: float = Field(0.35, ge=0.05, le=3.0)   # ≈1cm 후진 시간
    rear_back_speed: float = Field(0.08, ge=0.03, le=0.3)
    # 회전 직후 라인 중심 재정렬: 타이머 회전은 정확히 180°가 안 나오므로
    # center IR 가 테이프를 다시 볼 때까지 저속 펄스 회전으로 미세 조정한다.
    rear_align: bool = True
    rear_align_speed: float = Field(0.10, ge=0.03, le=0.3)
    rear_align_timeout_s: float = Field(5.0, ge=0.5, le=20.0)
    line_end_done_wall_cm: float = Field(4.0, ge=2.0, le=80.0)  # 라인 끝났고 벽이 이 안이면 완료로 간주
    loop_hz: float = Field(12.0, ge=2.0, le=30.0)
    timeout_s: float = Field(60.0, ge=5.0, le=300.0)
    # ── 시스템 ──
    manage_nav2: bool = True                # 추종 중 nav2 정지(cmd_vel 충돌 방지)
    stop_camera_on_finish: bool = False     # 미세조정 후 카메라 유지(연속 작업 대비)


_task: asyncio.Task | None = None
_state: dict[str, Any] = {"running": False, "phase": "idle", "message": "", "telemetry": {}}


async def _rear_finish_action(cfg: LineConfig, min_drive: int) -> None:
    """후면 주차 레코딩 액션: 제자리 180° 회전 → 소폭 후진. 센서 없이 타이머로만 도는
    고정 시퀀스라 회전각/후진량은 rear_turn_secs·rear_back_secs 실주행 튜닝으로 맞춘다."""
    def _boost(v: int, floor: int) -> int:
        if v == 0:
            return 0
        return int(math.copysign(max(abs(v), floor), v))

    _state.update(phase="turning", message="후면 전환 — 몸통 180° 회전 중", telemetry={})
    turn_floor = cfg.rear_turn_min_drive if cfg.rear_turn_min_drive > 0 else min_drive
    tl, tr = _vel_to_speeds(0.0, cfg.rear_turn_speed * cfg.rear_turn_dir)
    tl, tr = _boost(tl, turn_floor), _boost(tr, turn_floor)
    t_end = time.time() + cfg.rear_turn_secs
    while time.time() < t_end:
        await _motor_send(tl, tr)
        _state["telemetry"] = {"left": tl, "right": tr}
        await asyncio.sleep(0.1)
    await _motor_send(0, 0)
    await asyncio.sleep(0.2)

    if cfg.rear_align:
        # ── 라인 중심 재정렬: center IR 가 테이프를 볼 때까지 저속 펄스 회전 ──
        # 좌/우 센서에 테이프가 걸리면 그쪽으로, 아무 센서에도 없으면 회전하던
        # 방향으로 계속 스캔한다. 펄스(돌고-멈추고)로 오버슛을 막는다.
        _state.update(phase="line_align", message="회전 후 라인 중심 재탐색 중", telemetry={})
        align_deadline = time.time() + cfg.rear_align_timeout_s
        aligned = False
        seek_dir = 1 if cfg.rear_turn_dir >= 0 else -1
        while time.time() < align_deadline:
            try:
                ir = await asyncio.wait_for(_read_ir(), timeout=2.0)
            except (TimeoutError, Exception):
                ir = None
            on = _ir_on_tape(ir, cfg.ir_white_max)
            if on["center"]:
                aligned = True
                break
            if on["left"]:
                seek_dir = 1   # 테이프가 좌측 센서에 → 좌회전으로 중앙에 넣는다
            elif on["right"]:
                seek_dir = -1
            al, ar = _vel_to_speeds(0.0, cfg.rear_align_speed * seek_dir)
            al, ar = _boost(al, turn_floor), _boost(ar, turn_floor)
            await _motor_send(al, ar)
            _state.update(message="회전 후 라인 중심 재탐색 중" + (" (좌측 감지)" if on["left"] else " (우측 감지)" if on["right"] else ""), telemetry={"left": al, "right": ar, "on_tape": on})
            await asyncio.sleep(0.08)
            await _motor_send(0, 0)
            await asyncio.sleep(0.12)
        await _motor_send(0, 0)
        _state.update(message="라인 중심 정렬 완료" if aligned else "라인 재탐색 시간 초과 — 후진 진행", telemetry={})
        await asyncio.sleep(0.2)

    _state.update(phase="backing", message="후진 미세 조정(≈1cm) — 꼬리 벽 밀착", telemetry={})
    bl, br = _vel_to_speeds(-cfg.rear_back_speed, 0.0)
    bl, br = _boost(bl, min_drive), _boost(br, min_drive)
    t_end = time.time() + cfg.rear_back_secs
    while time.time() < t_end:
        await _motor_send(bl, br)
        _state["telemetry"] = {"left": bl, "right": br}
        await asyncio.sleep(0.05)
    await _motor_send(0, 0)


def _ir_on_tape(ir: dict[str, Any] | None, white_max: int) -> dict[str, bool]:
    """센서별 '테이프 위' 판정. 값이 없으면(0/None) 미판정=False."""
    out: dict[str, bool] = {}
    for k in ("left", "center", "right"):
        v = (ir or {}).get(k)
        out[k] = v is not None and 0 < int(v) <= white_max
    return out


def _pm2(action: str, name: str) -> None:
    try:
        subprocess.run(["pm2", action, name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
    except Exception:
        pass


def _grab_frame() -> np.ndarray | None:
    jpeg = camera.get_jpeg()
    if not jpeg:
        return None
    arr = np.frombuffer(jpeg, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _detect_line(frame: np.ndarray, cfg: LineConfig) -> dict[str, Any]:
    """하단 ROI 2밴드(near/far) 무게중심으로 흰 라인 offset(-1..1)·angle 을 계산."""
    h, w = frame.shape[:2]
    y0 = int(h * cfg.roi_top)
    roi = frame[y0:h]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    if cfg.thresh <= 0:
        _, binm = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _, binm = cv2.threshold(blur, cfg.thresh, 255, cv2.THRESH_BINARY)
    binm = cv2.morphologyEx(binm, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    rh = binm.shape[0]
    half = w / 2.0
    bands: dict[str, Any] = {}
    for name, band in (("far", binm[: rh // 2]), ("near", binm[rh // 2:])):
        m = cv2.moments(band, binaryImage=True)
        count = int(m["m00"])
        area_ratio = count / float(band.shape[0] * band.shape[1])
        valid = count >= cfg.min_area_px and area_ratio <= cfg.max_area_ratio
        cx = (m["m10"] / m["m00"]) if count > 0 else None
        bands[name] = {
            "count": count,
            "area_ratio": round(area_ratio, 3),
            "cx": round(cx, 1) if cx is not None else None,
            "offset": round((cx - half) / half, 4) if (valid and cx is not None) else None,
            "valid": valid,
        }

    near, far = bands["near"], bands["far"]
    found = near["valid"]
    offset = near["offset"] if found else None
    # 라인 기울기: near→far 중심 이동량(정규화). far 가 없으면 0(직선 가정).
    angle = 0.0
    if found and far["valid"] and far["cx"] is not None and near["cx"] is not None:
        angle = (far["cx"] - near["cx"]) / half
    return {
        "found": found,
        "offset": offset,
        "angle": round(angle, 4),
        "bands": bands,
        "roi_top_px": y0,
        "frame_size": [w, h],
        "thresh_used": "otsu" if cfg.thresh <= 0 else cfg.thresh,
    }


async def _read_wall_cm() -> float | None:
    try:
        d = await _read_dist()
        if d is None:
            return None
        d = float(d)
        if d > 300.0:
            return None
        if d < 0.5:
            # HC-SR04 초근접 블라인드 존: 벽에 붙으면 음수/0 부근을 반환한다.
            # 버리면 가까울수록 벽이 '안 보여' 그대로 박치기 — 초근접(3cm)으로 클램프.
            # (aruco_dock 2026-07-06 실주행 교훈과 동일)
            return 3.0
        return d
    except Exception:
        return None


async def _line_loop(cfg: LineConfig) -> None:
    min_drive = cfg.min_drive if cfg.min_drive > 0 else _DEFAULT_MIN_DRIVE
    dt = 1.0 / cfg.loop_hz
    t_start = time.time()
    wall_f: float | None = None

    def _boost(v: int) -> int:
        if v == 0:
            return 0
        return int(math.copysign(max(abs(v), min_drive), v))

    def _boost_pair(l: int, r: int, floor: int) -> tuple[int, int]:
        """좌우 비율을 유지한 채 최소 구동값까지 스케일 — 바퀴별 개별 floor 는
        좌우 차이를 지워 조향이 소멸한다(아르코 도킹 때 실측된 함정)."""
        m = max(abs(l), abs(r))
        if m == 0:
            return 0, 0
        if m < floor:
            scale = floor / m
            l = int(math.copysign(min(100, round(abs(l) * scale)), l)) if l else 0
            r = int(math.copysign(min(100, round(abs(r) * scale)), r)) if r else 0
        return l, r

    try:
        stop_hits = 0
        wall_hits = 0
        prev_wall: float | None = None
        wall_valid_t = time.time()
        sensor_fails = 0
        tape_seen = False
        lost_since: float | None = None
        last_angular = 0.0

        while True:
            if time.time() - t_start > cfg.timeout_s:
                await _motor_send(0, 0)
                _state.update(phase="timeout", message="시간 초과로 중단")
                return

            # 센서 읽기가 어디선가 걸려도 주차 루프 전체가 멈추지 않도록 타임아웃.
            # (2026-07-07 실측: 첫 _read_ir() await 가 간헐적으로 영원히 미복귀)
            _state["step"] = "loop:read_ir"
            try:
                ir = await asyncio.wait_for(_read_ir(), timeout=2.0)
            except (TimeoutError, Exception):
                ir = None
            if ir is None:
                sensor_fails += 1
                if sensor_fails >= 3:
                    await _motor_send(0, 0)
                    _state.update(phase="error", message="IR 센서 응답 없음(3회 연속) — 안전 정지")
                    return
            else:
                sensor_fails = 0
            left_ir = int((ir or {}).get("left") or 0)
            center_ir = int((ir or {}).get("center") or 0)
            right_ir = int((ir or {}).get("right") or 0)
            ir_now = {"left": left_ir, "center": center_ir, "right": right_ir}
            on_tape = _ir_on_tape(ir_now, cfg.ir_white_max)
            tape_count = sum(on_tape.values())
            stop_hits = stop_hits + 1 if tape_count >= cfg.stop_ir_sensors else 0
            stop_line = stop_hits >= cfg.ir_debounce
            det = {"found": tape_count > 0, "ir": {**ir_now, "obstacle": stop_line}, "on_tape": on_tape, "source": "ir"}

            _state["step"] = "loop:read_wall"
            try:
                wall = await asyncio.wait_for(_read_wall_cm(), timeout=2.0) if cfg.use_wall_sensor else None
            except (TimeoutError, Exception):
                wall = None
            if wall is not None:
                wall_f = wall if wall_f is None else (0.4 * wall + 0.6 * wall_f)
                wall_valid_t = time.time()
            elif cfg.use_wall_sensor and time.time() - wall_valid_t > 4.0:
                # 벽 거리가 유일한 정지 조건인데 초음파가 계속 무효면 눈감고 전진하는 셈 —
                # 평활값이 얼어붙은 채 벽을 지나친 실사고(2026-07-07, 32.9cm 동결) 재발 방지.
                await _motor_send(0, 0)
                _state.update(phase="error", message="초음파 응답 없음 4.0초 지속 — 안전 정지", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1) if wall_f is not None else None, **det})
                return
            elif cfg.use_wall_sensor and wall is None and wall_f is not None and wall_f <= max(cfg.target_wall_cm + 8.0, 12.0):
                # 근접 구간에서 센서가 끊기면 즉시 정지(전진 지속 금지)
                await _motor_send(0, 0)
                _state.update(phase="error", message="근접 구간 초음파 유실 — 안전 정지", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1), **det})
                return

            # 정지 판정은 LPF 평활값이 아닌 원시값으로 한다.
            # 평활값은 접근 중 실제보다 크게 나와(지연) 3cm 설정이 1.5cm 에서 서는 원인이었고,
            # 스파이크 오탐은 "먼 거리에서 갑자기 튄 저값이면 2프레임 확인" 조건이 막아준다.
            # 직전 루프도 목표 근처였던 정상 접근은 1프레임에 즉시 정지해 근접 오버슛을 줄인다.
            stop_wall_cm = max(cfg.target_wall_cm, cfg.hard_stop_wall_cm)
            # 충돌 방지: 목표거리 근처 원시값은 즉시 정지(디바운스 우회).
            # 근접 구간에서는 1프레임 지연도 오버슛으로 이어져 벽 접촉이 발생할 수 있다.
            # 조기정지 방지: 목표점 기준 소폭 안전 마진만 둔다(과도한 10cm 고정 강제정지 금지).
            safety_margin_cm = max(1.2, min(3.0, stop_wall_cm * 0.5))
            safety_stop_wall_cm = stop_wall_cm + safety_margin_cm
            immediate_wall_hit = wall is not None and wall <= safety_stop_wall_cm
            wall_hit = wall is not None and wall <= stop_wall_cm
            near_prev_wall = prev_wall is not None and prev_wall <= stop_wall_cm + max(2.0, stop_wall_cm * 0.75)
            wall_hits = wall_hits + 1 if wall_hit else 0
            prev_wall = wall
            if immediate_wall_hit or (wall_hit and (near_prev_wall or wall_hits >= 2)):
                await _motor_send(0, 0)
                if cfg.rear_finish:
                    _state["step"] = "rear_finish"
                    await _rear_finish_action(cfg, min_drive)
                    _state.update(phase="done", message=f"후면 주차 완료 (벽 {wall_f:.1f}cm 도달 → 180° 회전 → 후진 밀착)", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1), **det})
                    return
                _state.update(phase="done", message=f"바닥 센서 주차 완료 (벽 {wall_f:.1f}cm, 목표 {stop_wall_cm:.1f}cm)", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1), **det})
                return

            if stop_line:
                wall_near_for_line_stop = wall_f is not None and wall_f <= cfg.line_end_done_wall_cm
                if wall_near_for_line_stop:
                    await _motor_send(0, 0)
                    if cfg.rear_finish:
                        _state["step"] = "rear_finish"
                        await _rear_finish_action(cfg, min_drive)
                        _state.update(phase="done", message=f"후면 주차 완료 (정지선 감지 → 180° 회전 → 후진 밀착)", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1) if wall_f is not None else None, **det})
                        return
                    _state.update(phase="done", message=f"가로 정지선 감지(테이프 센서 {tape_count}개) — 정지", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1) if wall_f is not None else None, **det})
                    return

            # ── 조향 결정 (IR 3센서) ──
            linear = cfg.lin
            angular = 0.0
            trace_msg = "테이프 추종 중(직진)"
            if tape_count > 0:
                tape_seen = True
                lost_since = None
                if not cfg.straight_only:
                    l_on, r_on, c_on = on_tape["left"], on_tape["right"], on_tape["center"]
                    if l_on and not r_on:
                        angular = cfg.steer_soft if c_on else cfg.steer_hard
                        trace_msg = "테이프 좌측 — 좌보정 중"
                    elif r_on and not l_on:
                        angular = -(cfg.steer_soft if c_on else cfg.steer_hard)
                        trace_msg = "테이프 우측 — 우보정 중"
                    last_angular = angular
            elif tape_seen and not cfg.straight_only:
                # 라인 순간 상실: 마지막 조향 방향으로 완만히 복귀 시도, 지속되면 안전 정지
                if lost_since is None:
                    lost_since = time.time()
                if time.time() - lost_since > cfg.max_lost_s:
                    await _motor_send(0, 0)
                    _state.update(phase="lost", message=f"라인 상실 {cfg.max_lost_s:.0f}초 지속 — 안전 정지", telemetry={"left": 0, "right": 0, "wall_cm": round(wall_f, 1) if wall_f is not None else None, **det})
                    return
                angular = last_angular * 0.7
                trace_msg = "라인 놓침 — 마지막 방향으로 복귀 시도"
            elif tape_count == 0:
                trace_msg = "테이프 대기 — 직진"

            if angular != 0.0:
                linear *= cfg.steer_lin_scale

            drive_floor = min_drive
            if wall_f is not None and wall_f < cfg.slow_wall_cm:
                # 목표지점 근접 감속: 비례감속 + 크롤 모드(저속 고정 상한)
                span = max(1.0, (cfg.slow_wall_cm - cfg.target_wall_cm))
                frac = max(0.0, min(1.0, (wall_f - cfg.target_wall_cm) / span))
                linear *= max(0.20, frac)
                crawl_cm = max(cfg.target_wall_cm + 6.0, 10.0)
                if wall_f <= crawl_cm:
                    linear = min(linear, cfg.lin * 0.35)
                    drive_floor = max(14, int(min_drive * 0.35))

            left, right = _vel_to_speeds(linear, angular)
            left, right = _boost_pair(left, right, drive_floor)
            _state["step"] = "loop:motor_send"
            await _motor_send(left, right)
            _state["step"] = "loop:sleep"
            _state.update(phase="tracing", message=trace_msg, telemetry={"left": left, "right": right, "angular": round(angular, 3), "wall_cm": round(wall_f, 1) if wall_f is not None else None, **det})
            await asyncio.sleep(dt)
    except asyncio.CancelledError:
        _state.update(phase="stopped", message="사용자 중지")
        raise
    except Exception as exc:
        _state.update(phase="error", message=f"오류: {exc}")
    finally:
        await _motor_send(0, 0)
        _state["running"] = False
        if cfg.manage_nav2:
            _pm2("start", "nav2")


async def _line_detect_payload(
    ir_white_max: int = 800,
    source: str = "ir",
    roi_top: float = 0.55,
    thresh: int = 0,
    min_area_px: int = 120,
) -> dict[str, Any]:
    source = (source or "ir").lower()
    if source not in {"ir", "camera", "both"}:
        source = "ir"

    wall = await _read_wall_cm()
    ir: dict[str, Any] | None = None
    on_tape: dict[str, bool] = {"left": False, "center": False, "right": False}
    ir_found = False
    if source in {"ir", "both"}:
        try:
            ir = await asyncio.wait_for(_read_ir(), timeout=1.2)
        except (TimeoutError, Exception):
            ir = None
        on_tape = _ir_on_tape(ir, ir_white_max)
        ir_found = any(on_tape.values())

    camera_det: dict[str, Any] | None = None
    camera_found = False
    if source in {"camera", "both"}:
        cfg = LineConfig(roi_top=roi_top, thresh=thresh, min_area_px=min_area_px, ir_white_max=ir_white_max)
        if not camera.is_running():
            camera.start()
            await asyncio.sleep(0.12)
        frame = _grab_frame()
        if frame is not None:
            camera_det = _detect_line(frame, cfg)
            camera_found = bool(camera_det.get("found"))
        else:
            camera_det = {"found": False, "error": "camera frame unavailable"}

    return {
        "found": camera_found if source == "camera" else (ir_found if source == "ir" else (camera_found or ir_found)),
        "ir": ir,
        "on_tape": on_tape,
        "camera": camera_det,
        "ir_white_max": ir_white_max,
        "wall_cm": round(wall, 1) if wall is not None else None,
        "source": source,
    }

@router.get("/line/detect")
async def line_detect(ir_white_max: int = 800, source: str = "ir", roi_top: float = 0.55, thresh: int = 0, min_area_px: int = 120):
    return await _line_detect_payload(ir_white_max, source, roi_top, thresh, min_area_px)

@router.websocket("/line/ws/detect")
async def line_detect_ws(ws: WebSocket, ir_white_max: int = 800, source: str = "ir", roi_top: float = 0.55, thresh: int = 0, min_area_px: int = 120):
    await ws.accept()
    try:
        while True:
            await ws.send_json(await _line_detect_payload(ir_white_max, source, roi_top, thresh, min_area_px))
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


@router.post("/line/start")
async def line_start(cfg: LineConfig):
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    await _motor_send(0, 0)
    _state.update(running=False, phase="initializing", message="바닥 센서 확인 및 초기화 중", telemetry={"left": 0, "right": 0})

    if cfg.manage_nav2:
        _pm2("stop", "nav2")

    _state.update(running=True, phase="starting", message="바닥 센서 확인 중 - 직진만 유지", telemetry={"left": 0, "right": 0})
    cfg.rear_finish = False
    _task = asyncio.create_task(_line_loop(cfg))
    return {"success": True, "message": "바닥 센서 미세조정 시작", "config": cfg.model_dump()}


@router.post("/line/stop")
async def line_stop():
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    await _motor_send(0, 0)
    _pm2("start", "nav2")
    _state.update(running=False, phase="idle", message="정지됨")
    return {"success": True, "message": "라인 미세조정 정지"}


@router.get("/line/debug")
async def line_debug():
    """멈춤 진단용: 주행 태스크가 현재 어느 await 에 서 있는지 코루틴 체인 전체를 덤프한다."""
    frames: list[str] = []
    if _task is not None and not _task.done():
        # Task.get_stack() 은 최상위 프레임만 주므로 cr_await 체인을 직접 따라간다
        obj: Any = _task.get_coro()
        depth = 0
        while obj is not None and depth < 20:
            fr = getattr(obj, "cr_frame", None) or getattr(obj, "gi_frame", None)
            if fr is not None:
                frames.append(f"{fr.f_code.co_filename}:{fr.f_lineno} in {fr.f_code.co_name}")
            nxt = getattr(obj, "cr_await", None) or getattr(obj, "gi_yieldfrom", None)
            if nxt is None:
                frames.append(f"(awaiting) {type(obj).__name__}: {obj!r}"[:200])
            obj = nxt
            depth += 1
    return {"state": _state, "stack": frames}


def _line_status_payload() -> dict[str, Any]:
    task_info = {}
    if _task is not None:
        task_info = {"done": _task.done(), "cancelled": _task.cancelled()}
        if _task.done():
            try:
                exc = _task.exception()
                task_info["exception"] = str(exc) if exc else None
            except Exception as e:
                task_info["exception"] = f"Error: {e}"
    return {"state": _state, "task": task_info}


@router.get("/line/status")
async def line_status():
    return _line_status_payload()


@router.websocket("/line/ws/status")
async def line_status_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(_line_status_payload())
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
