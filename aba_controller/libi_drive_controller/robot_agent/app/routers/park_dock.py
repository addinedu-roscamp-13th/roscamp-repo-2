"""
주차 오케스트레이션 라우터 — robot_agent(로봇 온보드) 용. (2026-07-10 신규)

배경: 기존에는 주차(진입각 정렬 · 테이프 탐색 · 벽 거리 정지)의 제어 폐루프가
프론트엔드(admin/parking)에서 `motor/move` + `setTimeout` 펄스로 돌았다. 그 결과
감지→판단→구동이 매 사이클 네트워크를 왕복(로봇 센서 → HTTP → 브라우저 → HTTP →
로봇 모터)해 제어 주기가 2~3Hz로 느리고 정지-출발이 덜컹거렸다.

이 모듈은 그 폐루프들을 로봇 위로 옮긴다. 중앙(FMS)은 파라미터(각도 · 거리 · zone)와
start/stop 만 주고, 로봇이 로컬 고속 루프로 완주한다. 통신이 끊겨도 로봇이 자체 완주
또는 자체 안전정지한다.

프리미티브(각각 로봇 로컬 폐루프):
  - _rotate_to_odom    : 목표 각도로 회전 (odom yaw P제어)
  - _rotate_to_marker  : 아루코 마커를 정면으로 향하도록 회전 (마커 인식)
  - _search_line       : 좌우 펄스 스캔으로 테이프 획득
  - _approach_wall     : 초음파로 벽 target_cm 에서 정지
  - (라인 추종 + 최종 정지는 line_dock._line_loop 재사용)

오케스트레이터 _park_loop: 진입각 정렬 → 테이프 탐색 → 라인 추종(=_line_loop) 순차 실행.

경로(server.py 에서 /api/robot 프리픽스 → /api/robot/park/*):
  POST /park/start          — 주차 시퀀스 시작(파라미터, 비동기 실행)
  POST /park/stop           — 비상정지(모든 루프 중단 + 모터 0)
  GET  /park/status         — 상태/텔레메트리
  WS   /park/ws/status
  POST /park/rotate         — (디버그) 회전 프리미티브 단독 실행
  POST /park/wall_approach  — (디버그) 벽 접근 프리미티브 단독 실행
  POST /park/search_line    — (디버그) 테이프 탐색 프리미티브 단독 실행
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.routers.driving import _motor_send, _vel_to_speeds, _read_ir
from app.routers.line_dock import (
    LineConfig,
    _ir_on_tape,
    _line_loop,
    _pm2,
    _read_wall_cm,
)
from app.routers.line_dock import _state as _line_state

router = APIRouter()

# 로봇별 캘리브레이션 상속(aruco_dock 은 로봇마다 실측 튜닝이 다르다).
try:
    from app.routers.aruco_dock import _MIN_DRIVE as _DEFAULT_MIN_DRIVE
except Exception:
    _DEFAULT_MIN_DRIVE = 30
try:
    from app.routers.aruco_dock import _STEER_SIGN as _DEFAULT_STEER_SIGN
except Exception:
    _DEFAULT_STEER_SIGN = 1.0


class ParkConfig(BaseModel):
    # ── 진입각 정렬 ──
    approach_yaw_deg: float | None = None            # None 이면 회전 생략
    rotate_ref: str = Field("odom")                  # "odom"(TF yaw) | "marker"(아루코)
    rotate_tol_deg: float = Field(5.0, ge=0.5, le=30.0)
    rotate_speed: float = Field(0.14, ge=0.03, le=0.5)
    rotate_min_drive: int = Field(26, ge=0, le=70)   # 제자리 회전 최소 모터 %(정지마찰 극복)
    rotate_timeout_s: float = Field(12.0, ge=1.0, le=60.0)
    # ── 마커 기준 회전(rotate_ref="marker") ──
    marker_id: int | None = None
    marker_dict: str = "DICT_4X4_50"
    marker_len_m: float = Field(0.05, ge=0.01, le=0.5)
    marker_center_tol: float = Field(0.06, ge=0.01, le=0.3)  # |ex| 이하면 정렬 완료
    # ── 테이프 탐색 ──
    do_search: bool = True
    search_speed: float = Field(0.12, ge=0.03, le=0.4)
    search_min_drive: int = Field(26, ge=0, le=70)
    search_timeout_s: float = Field(12.0, ge=1.0, le=40.0)
    # ── 라인 추종/최종 정지(line_dock._line_loop 위임) ──
    line: LineConfig = Field(default_factory=LineConfig)
    # ── 벽 접근 프리미티브(직선 접근·디버그 엔드포인트용) ──
    wall_target_cm: float = Field(3.0, ge=2.0, le=80.0)
    wall_tol_cm: float = Field(1.0, ge=0.3, le=10.0)
    wall_speed: float = Field(0.10, ge=0.03, le=0.3)
    wall_slow_cm: float = Field(18.0, ge=5.0, le=150.0)  # 이 안쪽부터 감속
    wall_timeout_s: float = Field(30.0, ge=2.0, le=120.0)
    # ── 시스템 ──
    loop_hz: float = Field(12.0, ge=2.0, le=30.0)
    manage_nav2: bool = True                          # 시퀀스 중 nav2 정지(cmd_vel 충돌 방지)


_task: asyncio.Task | None = None
_state: dict[str, Any] = {"running": False, "phase": "idle", "message": "", "telemetry": {}}


def _norm_rad(a: float) -> float:
    """각도(rad)를 (-π, π] 로 정규화."""
    while a > math.pi:
        a -= 2 * math.pi
    while a <= -math.pi:
        a += 2 * math.pi
    return a


def _boost(v: int, floor: int) -> int:
    """0이 아닌 모터값을 최소 구동값(floor)까지 끌어올려 정지마찰을 극복."""
    if v == 0:
        return 0
    return int(math.copysign(max(abs(v), floor), v))


# ────────────────────────────── 프리미티브 ──────────────────────────────

async def _rotate_to_odom(cfg: ParkConfig) -> bool:
    """odom(TF) yaw 를 목표 각도로 맞추는 P제어 회전. 목표±tol 안에서 2프레임 유지 시 완료."""
    from app.core import ros_bridge

    assert cfg.approach_yaw_deg is not None
    target = math.radians(cfg.approach_yaw_deg)
    tol = math.radians(cfg.rotate_tol_deg)
    floor = cfg.rotate_min_drive if cfg.rotate_min_drive > 0 else _DEFAULT_MIN_DRIVE
    dt = 1.0 / cfg.loop_hz
    deadline = time.time() + cfg.rotate_timeout_s
    ok_hits = 0
    while time.time() < deadline:
        pose = ros_bridge.get_current_pose()
        if pose is None:
            _state.update(phase="rotate", message="pose(odom) 없음 — TF/AMCL 확인 중")
            await asyncio.sleep(dt)
            continue
        yaw = float(pose[2])
        err = _norm_rad(target - yaw)
        if abs(err) <= tol:
            ok_hits += 1
            if ok_hits >= 2:
                await _motor_send(0, 0)
                _state.update(phase="rotate", message=f"진입각 정렬 완료 (오차 {math.degrees(err):.1f}°)")
                return True
        else:
            ok_hits = 0
        # 비례 회전 속도(가까울수록 감속) + 최소 회전속도 보장.
        # angular + 는 좌회전(CCW, yaw 증가) → err>0(yaw 를 키워야 함)이면 +.
        mag = cfg.rotate_speed * min(1.0, abs(err) / math.radians(30.0))
        mag = max(mag, cfg.rotate_speed * 0.35)
        angular = math.copysign(mag, err)
        left, right = _vel_to_speeds(0.0, angular)
        left, right = _boost(left, floor), _boost(right, floor)
        await _motor_send(left, right)
        _state.update(
            phase="rotate",
            message=f"진입각 정렬 중 (오차 {math.degrees(err):.0f}°)",
            telemetry={"left": left, "right": right, "yaw_err_deg": round(math.degrees(err), 1)},
        )
        await asyncio.sleep(dt)
    await _motor_send(0, 0)
    _state.update(phase="rotate", message="진입각 정렬 시간 초과")
    return False


async def _rotate_to_marker(cfg: ParkConfig) -> bool:
    """아루코 마커(marker_id)가 화면 중앙(정면)에 오도록 회전. 마커가 안 보이면 저속 스캔."""
    from app.routers import aruco_dock

    floor = cfg.rotate_min_drive if cfg.rotate_min_drive > 0 else _DEFAULT_MIN_DRIVE
    dt = 1.0 / cfg.loop_hz
    deadline = time.time() + cfg.rotate_timeout_s
    ok_hits = 0
    while time.time() < deadline:
        # aruco 검출은 동기(cv2) → 스레드로 돌려 루프 블로킹 방지.
        try:
            det = await asyncio.to_thread(aruco_dock._detect, cfg.marker_dict, cfg.marker_len_m)
        except Exception:
            det = None
        marker = None
        if det:
            for mk in det.get("markers", []):
                if mk.get("id") == cfg.marker_id:
                    marker = mk
                    break
        if marker is None:
            al, ar = _vel_to_speeds(0.0, cfg.rotate_speed * 0.6 * _DEFAULT_STEER_SIGN)
            al, ar = _boost(al, floor), _boost(ar, floor)
            await _motor_send(al, ar)
            _state.update(phase="rotate_marker", message=f"마커 {cfg.marker_id} 탐색 중")
            await asyncio.sleep(dt)
            continue
        ex = float(marker.get("ex", 0.0))  # 좌우 오프셋(양수=마커가 화면 우측)
        if abs(ex) <= cfg.marker_center_tol:
            ok_hits += 1
            if ok_hits >= 2:
                await _motor_send(0, 0)
                _state.update(phase="rotate_marker", message=f"마커 {cfg.marker_id} 정면 정렬 완료 (ex={ex:+.2f})")
                return True
        else:
            ok_hits = 0
        # ex>0(우측)이면 우회전으로 중앙에. 회전 부호는 로봇별 _STEER_SIGN 반영.
        mag = cfg.rotate_speed * min(1.0, abs(ex) / 0.3)
        mag = max(mag, cfg.rotate_speed * 0.35)
        angular = -math.copysign(mag, ex) * _DEFAULT_STEER_SIGN
        al, ar = _vel_to_speeds(0.0, angular)
        al, ar = _boost(al, floor), _boost(ar, floor)
        await _motor_send(al, ar)
        _state.update(
            phase="rotate_marker",
            message=f"마커 정렬 중 (ex={ex:+.2f})",
            telemetry={"left": al, "right": ar, "ex": round(ex, 3)},
        )
        await asyncio.sleep(dt)
    await _motor_send(0, 0)
    _state.update(phase="rotate_marker", message="마커 정렬 시간 초과")
    return False


async def _search_line(cfg: ParkConfig) -> bool:
    """좌우 펄스 회전으로 IR 센서가 테이프를 볼 때까지 스캔. 한쪽을 충분히 돌면 반대로 더 넓게."""
    floor = cfg.search_min_drive if cfg.search_min_drive > 0 else _DEFAULT_MIN_DRIVE
    white_max = cfg.line.ir_white_max
    deadline = time.time() + cfg.search_timeout_s
    seek_dir = 1  # 우선 우측 탐색
    swept = 0.0
    while time.time() < deadline:
        try:
            ir = await asyncio.wait_for(_read_ir(), timeout=2.0)
        except (TimeoutError, Exception):
            ir = None
        on = _ir_on_tape(ir, white_max)
        if any(on.values()):
            await _motor_send(0, 0)
            _state.update(phase="search", message="테이프 감지 — 라인 추종으로 전환", telemetry={"on_tape": on})
            return True
        # 펄스(돌고-멈춤)로 오버슛 방지.
        al, ar = _vel_to_speeds(0.0, cfg.search_speed * seek_dir)
        al, ar = _boost(al, floor), _boost(ar, floor)
        await _motor_send(al, ar)
        _state.update(
            phase="search",
            message=f"테이프 탐색 중 ({'우' if seek_dir > 0 else '좌'})",
            telemetry={"left": al, "right": ar},
        )
        await asyncio.sleep(0.10)
        await _motor_send(0, 0)
        await asyncio.sleep(0.10)
        swept += 0.10
        if swept > 1.2:  # 한쪽 충분히 돌았으면 반대로 더 넓게 스윕
            seek_dir *= -1
            swept = 0.0
    await _motor_send(0, 0)
    _state.update(phase="search", message="테이프 탐색 시간 초과")
    return False


async def _approach_wall(cfg: ParkConfig) -> bool:
    """초음파로 벽 target_cm(±tol)에서 정지하는 직선 접근. 근접 구간 비례 감속 + 센서 유실 안전정지."""
    floor = _DEFAULT_MIN_DRIVE
    dt = 1.0 / cfg.loop_hz
    deadline = time.time() + cfg.wall_timeout_s
    wall_f: float | None = None
    fails = 0
    while time.time() < deadline:
        try:
            wall = await asyncio.wait_for(_read_wall_cm(), timeout=2.0)
        except (TimeoutError, Exception):
            wall = None
        if wall is None:
            fails += 1
            if fails >= 3:
                await _motor_send(0, 0)
                _state.update(phase="wall", message="초음파 응답 없음(3회) — 안전 정지")
                return False
            await asyncio.sleep(dt)
            continue
        fails = 0
        wall_f = wall if wall_f is None else (0.4 * wall + 0.6 * wall_f)
        # 정지 판정은 원시값으로(평활값 지연이 오버슛/조기정지를 유발).
        if wall <= cfg.wall_target_cm + cfg.wall_tol_cm:
            await _motor_send(0, 0)
            _state.update(phase="wall", message=f"벽 {wall:.1f}cm 도달 — 정지 (목표 {cfg.wall_target_cm:.0f}cm)", telemetry={"wall_cm": round(wall, 1)})
            return True
        linear = cfg.wall_speed
        if wall_f < cfg.wall_slow_cm:
            span = max(1.0, cfg.wall_slow_cm - cfg.wall_target_cm)
            frac = max(0.0, min(1.0, (wall_f - cfg.wall_target_cm) / span))
            linear *= max(0.25, frac)
        left, right = _vel_to_speeds(linear, 0.0)
        left, right = _boost(left, floor), _boost(right, floor)
        await _motor_send(left, right)
        _state.update(
            phase="wall",
            message=f"벽 접근 중 {wall_f:.1f}cm → 목표 {cfg.wall_target_cm:.0f}cm",
            telemetry={"left": left, "right": right, "wall_cm": round(wall_f, 1)},
        )
        await asyncio.sleep(dt)
    await _motor_send(0, 0)
    _state.update(phase="wall", message="벽 접근 시간 초과")
    return False


# ────────────────────────────── 오케스트레이터 ──────────────────────────────

async def _park_loop(cfg: ParkConfig) -> None:
    """진입각 정렬 → 테이프 탐색 → 라인 추종(+최종 벽/정지선 정지) 순차 실행."""
    try:
        if cfg.manage_nav2:
            _pm2("stop", "nav2")  # cmd_vel 충돌 방지

        # 1) 진입각 정렬
        if cfg.rotate_ref == "marker" and cfg.marker_id is not None:
            await _rotate_to_marker(cfg)
        elif cfg.approach_yaw_deg is not None:
            await _rotate_to_odom(cfg)

        # 2) 테이프 탐색(이미 테이프 위면 생략)
        if cfg.do_search:
            try:
                ir = await asyncio.wait_for(_read_ir(), timeout=2.0)
            except (TimeoutError, Exception):
                ir = None
            if not any(_ir_on_tape(ir, cfg.line.ir_white_max).values()):
                await _search_line(cfg)

        # 3) 라인 추종 + 최종 정지 (line_dock 재사용). nav2 는 park 가 관리하므로 위임 루프는 끔.
        _state.update(phase="line_follow", message="라인 추종 미세조정 시작")
        linecfg = cfg.line.model_copy(update={"manage_nav2": False})
        await _line_loop(linecfg)

        # line_loop 종료 결과를 흡수(완료/상실/타임아웃 등).
        _state.update(
            phase=_line_state.get("phase", "done"),
            message=_line_state.get("message", "주차 완료"),
            telemetry=_line_state.get("telemetry", {}),
        )
    except asyncio.CancelledError:
        _state.update(phase="stopped", message="사용자 중지")
        raise
    except Exception as exc:  # noqa: BLE001
        _state.update(phase="error", message=f"오류: {exc}")
    finally:
        await _motor_send(0, 0)
        _state["running"] = False
        if cfg.manage_nav2:
            _pm2("start", "nav2")


# ────────────────────────────── 태스크/엔드포인트 ──────────────────────────────

async def _cancel_task() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


async def _launch(
    coro_factory: Callable[[], Awaitable[Any]],
    phase: str,
    message: str,
    restart_nav2: bool = False,
) -> None:
    """공용 태스크 런처 — 기존 태스크를 취소하고 새 코루틴을 백그라운드로 띄운다.
    restart_nav2=True 면 프리미티브 종료 시 nav2 를 다시 켠다(끈 쪽이 반드시 되살리도록 —
    안 되살리면 주차 후 지도 클릭/구역 주행이 먹통이 된다)."""
    global _task
    await _cancel_task()
    await _motor_send(0, 0)
    _state.update(running=True, phase=phase, message=message, telemetry={})

    async def _runner() -> None:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            _state.update(phase="stopped", message="사용자 중지")
            raise
        except Exception as exc:  # noqa: BLE001
            _state.update(phase="error", message=f"오류: {exc}")
        finally:
            await _motor_send(0, 0)
            _state["running"] = False
            if restart_nav2:
                _pm2("start", "nav2")

    _task = asyncio.create_task(_runner())


@router.post("/park/start")
async def park_start(cfg: ParkConfig):
    """주차 시퀀스(정렬→탐색→라인추종) 시작. 즉시 accepted 응답, 로봇에서 비동기 완주."""
    await _cancel_task()
    await _motor_send(0, 0)
    _state.update(running=True, phase="starting", message="주차 시퀀스 시작", telemetry={})
    global _task
    _task = asyncio.create_task(_park_loop(cfg))
    return {"success": True, "message": "주차 시작", "config": cfg.model_dump()}


@router.post("/park/stop")
async def park_stop():
    """비상정지: 실행 중인 모든 루프 중단 + 모터 0 + nav2 복구."""
    await _cancel_task()
    await _motor_send(0, 0)
    _pm2("start", "nav2")
    _state.update(running=False, phase="idle", message="정지됨")
    return {"success": True, "message": "주차 정지"}


@router.post("/park/rotate")
async def park_rotate(cfg: ParkConfig):
    """(디버그) 회전 프리미티브만 단독 실행 (odom/marker)."""
    if cfg.rotate_ref == "marker" and cfg.marker_id is not None:
        await _launch(lambda: _rotate_to_marker(cfg), "rotate_marker", "마커 회전(단독)")
    elif cfg.approach_yaw_deg is not None:
        await _launch(lambda: _rotate_to_odom(cfg), "rotate", "각도 회전(단독)")
    else:
        return {"success": False, "message": "approach_yaw_deg 또는 marker_id 가 필요합니다."}
    return {"success": True, "message": "회전 시작"}


@router.post("/park/wall_approach")
async def park_wall_approach(cfg: ParkConfig):
    """(디버그) 벽 접근 프리미티브만 단독 실행."""
    if cfg.manage_nav2:
        _pm2("stop", "nav2")
    await _launch(lambda: _approach_wall(cfg), "wall", "벽 접근(단독)", restart_nav2=cfg.manage_nav2)
    return {"success": True, "message": "벽 접근 시작", "target_cm": cfg.wall_target_cm}


@router.post("/park/search_line")
async def park_search_line(cfg: ParkConfig):
    """(디버그) 테이프 탐색 프리미티브만 단독 실행."""
    if cfg.manage_nav2:
        _pm2("stop", "nav2")
    await _launch(lambda: _search_line(cfg), "search", "테이프 탐색(단독)", restart_nav2=cfg.manage_nav2)
    return {"success": True, "message": "테이프 탐색 시작"}


def _status_payload() -> dict[str, Any]:
    task_info: dict[str, Any] = {}
    if _task is not None:
        task_info = {"done": _task.done(), "cancelled": _task.cancelled()}
        if _task.done():
            try:
                exc = _task.exception()
                task_info["exception"] = str(exc) if exc else None
            except Exception as e:  # noqa: BLE001
                task_info["exception"] = f"Error: {e}"
    return {"state": _state, "task": task_info, "line": _line_state}


@router.get("/park/status")
async def park_status():
    return _status_payload()


@router.websocket("/park/ws/status")
async def park_status_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(_status_payload())
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
