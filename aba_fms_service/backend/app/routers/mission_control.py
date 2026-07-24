from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import fleet_telemetry
from app.database import get_admin_db
from app.hardware.pinky_greeting_monitor import pinky_greeting_monitor
from app.deps import get_current_admin
from app.fleet_coordinator import coordinator
from app.models import Admin, LocationAction, Robot, RobotLocation
from app.security import decode_token

router = APIRouter(prefix="/api/control", tags=["mission-control"])

# robot_agent(FastAPI)로 이전된 nav 엔드포인트. 라우터 경로는 옛 Flask nav2_web_server 와
# 동일한 /api/* 형식이고 포트만 9001 로 바뀌었다. 따라서 경로 재작성 없이 그대로 프록시한다.
# [2026-07-08] 명령은 ROS2 fleet_cmd 토픽 우선(fleet_telemetry.send_command)으로 보내고,
# 링크 불가(브릿지/로봇 미연결)일 때만 위 HTTP 프록시로 폴백한다.
NAV2_DEFAULT_PORT = 9001
TIMEOUT_SEC = 3.0
SLAM_TIMEOUT_SEC = 10.0  # slam reset/save 는 서비스 대기가 있어 넉넉히
WAYPOINT_GOTO_TIMEOUT_SEC = 180.0  # 간선 다중 홉 이동은 오래 걸릴 수 있어 넉넉히

# 마커 액션(rc_marker_actions)과 동일한 액션 타입 집합을 재사용한다.
VALID_ACTIONS = {"none", "dock", "rotate", "move", "lcd_emotion", "lcd_text", "lcd_image"}


class GoalRequest(BaseModel):
    x: float
    y: float
    yaw: float = 0.0


class SaveMapRequest(BaseModel):
    name: str = ""


class LocationSetRequest(BaseModel):
    name: str = Field(..., min_length=1)
    x: float | None = None
    y: float | None = None
    yaw: float | None = None


class LocationNameRequest(BaseModel):
    name: str = Field(..., min_length=1)


class MissionRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    loop: bool = False


class RelativeMoveRequest(BaseModel):
    direction: str = "forward"
    distance_m: float = Field(..., gt=0.0, le=1.0)
    speed_mps: float = Field(0.08, gt=0.02, le=0.25)
    # 진행 방향 라이다 여유거리가 이 값(라이다 중심 기준, m) 밑으로 떨어지면 즉시 정지.
    # 맵이 2.14×1.32m 로 매우 좁아 작게 잡는다. 0.12m ≈ 전방 범퍼-벽 여유 ~4cm
    # (라이다 오프셋 -0.017 + 로봇 반경 0.06). 더 붙이려면 낮추면 됨(하한 0.05).
    min_clearance_m: float = Field(0.12, ge=0.05, le=1.0)


class ScheduleRequest(MissionRequest):
    minutes: int = Field(..., ge=1)


class LocationActionIn(BaseModel):
    action_type: str = "none"
    params: dict | None = None
    enabled: bool = True


class CoordinatorConfigIn(BaseModel):
    enabled: bool | None = None
    min_distance: float | None = Field(None, gt=0)
    clear_distance: float | None = Field(None, gt=0)
    # robot_id -> 우선순위(1이 가장 높음). 근접 시 우선순위 낮은 로봇이 양보(정지)한다.
    priorities: dict[int, int] | None = None
    # 막는 로봇을 경로 밖으로 자동 비켜세우기(evasion). 자동 주행이라 기본 OFF.
    evasion_enabled: bool | None = None


async def _target_url(robot_id: int, nav_port: int, db: AsyncSession) -> str:
    robot = (
        await db.execute(
            select(Robot).where(
                Robot.id == robot_id,
                Robot.robot_type == "pinky",
                Robot.is_active == True,
            )
        )
    ).scalar_one_or_none()
    if robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="활성 주행로봇을 찾을 수 없습니다.")
    # robot_agent(FastAPI)는 DB에 등록된 로봇 포트(보통 9001)로 동작한다.
    # 프론트가 옛 Flask 포트(8080)를 nav_port 로 넘겨도 무시하고 등록 포트를 우선 사용한다.
    port = robot.port or nav_port
    return f"http://{robot.ip_address}:{port}"


def _request_json(base: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
            raw = res.read()
            if not raw:
                return {"success": True}
            return json.loads(raw.decode("utf-8"))
    except socket.timeout as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Nav2 관제 서버 응답 시간이 초과되었습니다.") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") or str(exc)
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Nav2 관제 서버에 연결할 수 없습니다: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Nav2 관제 서버가 JSON이 아닌 응답을 반환했습니다.") from exc


def _location_payload(loc: RobotLocation) -> dict[str, float]:
    return {"x": float(loc.x), "y": float(loc.y), "yaw": float(loc.yaw)}


async def _db_locations(robot_id: int, db: AsyncSession) -> dict[str, dict[str, float]]:
    # 'HOME' 은 로봇별 홈 복귀 좌표를 담는 예약 이름이다. 실제 좌표는 A~H 중 한
    # 구역과 겹치므로, 순찰 구역 목록/맵 마커에는 넣지 않고 홈 복귀에서만 직접 조회한다.
    rows = (
        await db.execute(
            select(RobotLocation)
            .where(RobotLocation.robot_id == robot_id, RobotLocation.name != "HOME")
            .order_by(RobotLocation.name)
        )
    ).scalars().all()
    return {loc.name: _location_payload(loc) for loc in rows}


async def _upsert_db_location(robot_id: int, name: str, x: float, y: float, yaw: float, db: AsyncSession) -> RobotLocation:
    loc = (
        await db.execute(
            select(RobotLocation).where(RobotLocation.robot_id == robot_id, RobotLocation.name == name)
        )
    ).scalar_one_or_none()
    if loc is None:
        loc = RobotLocation(robot_id=robot_id, name=name)
        db.add(loc)
    loc.x = round(float(x), 4)
    loc.y = round(float(y), 4)
    loc.yaw = round(float(yaw), 4)
    await db.commit()
    await db.refresh(loc)
    return loc


async def _get_db_location(robot_id: int, db: AsyncSession, name: str) -> RobotLocation:
    loc = (
        await db.execute(
            select(RobotLocation).where(RobotLocation.robot_id == robot_id, RobotLocation.name == name)
        )
    ).scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail=repr(name) + " 위치가 없습니다")
    return loc


async def _sync_location_to_robot(robot_id: int, nav_port: int, db: AsyncSession, name: str) -> None:
    loc = await _get_db_location(robot_id, db, name)
    await _proxy(
        robot_id,
        nav_port,
        db,
        "/api/locations/set",
        "POST",
        {"name": loc.name, **_location_payload(loc)},
    )


async def _sync_locations_to_robot(robot_id: int, nav_port: int, db: AsyncSession, names: list[str]) -> None:
    for name in names:
        await _sync_location_to_robot(robot_id, nav_port, db, name)


async def _proxy(
    robot_id: int,
    nav_port: int,
    db: AsyncSession,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    base = await _target_url(robot_id, nav_port, db)
    # robot_agent 의 nav 경로는 옛 Flask 와 동일한 /api/* 이므로 그대로 전달한다.
    return await asyncio.to_thread(_request_json, base, path, method, payload)


async def _state_for_robot(robot_id: int, nav_port: int, db: AsyncSession) -> Any:
    base = await _target_url(robot_id, nav_port, db)
    ip = base.split("//", 1)[-1].rsplit(":", 1)[0]
    cached = fleet_telemetry.get_state(ip)
    if cached is not None:
        return cached
    return await asyncio.to_thread(_request_json, base, "/api/state")


async def _admin_from_ws_token(token: str, db: AsyncSession) -> Admin | None:
    try:
        admin_id = int(decode_token(token))
    except (ValueError, TypeError):
        return None
    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()
    return admin if admin is not None and admin.is_active else None


@router.websocket("/ws/state")
async def state_ws(
    ws: WebSocket,
    robot_id: int = Query(...),
    nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535),
    token: str = Query(...),
    db: AsyncSession = Depends(get_admin_db),
):
    admin = await _admin_from_ws_token(token, db)
    if admin is None:
        await ws.close(code=4001)
        return
    await ws.accept()
    try:
        while True:
            try:
                state = await _state_for_robot(robot_id, nav_port, db)
                await ws.send_json({"ok": True, "state": state})
            except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
                return
            except Exception as e:
                try:
                    await ws.send_json({"ok": False, "error": str(e)})
                except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
                    return
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


async def _ros_command(
    robot_id: int,
    nav_port: int,
    db: AsyncSession,
    action: str,
    args: dict[str, Any] | None = None,
    timeout: float = TIMEOUT_SEC,
) -> Any | None:
    """ROS2 fleet_cmd 로 명령을 시도한다.

    - 링크 불가/타임아웃 → None (호출측이 기존 HTTP 프록시로 폴백)
    - 로봇이 에러 응답(ok=False) → HTTP 프록시였다면 받았을 상태코드로 HTTPException
    - 성공 → 로봇 nav_router 와 동일 스키마의 응답 dict
    """
    base = await _target_url(robot_id, nav_port, db)
    ip = base.split("//", 1)[-1].rsplit(":", 1)[0]
    res = await asyncio.to_thread(
        fleet_telemetry.send_command,
        ip,
        action,
        args or {},
        timeout,
        False,
    )
    if res is None:
        return None
    if not res.get("ok"):
        raise HTTPException(status_code=int(res.get("status") or 502), detail=res.get("msg") or "로봇 명령이 실패했습니다.")
    data = res.get("data")
    return data if data is not None else {"success": True}


@router.get("/state")
async def state(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    # [2026-07-07] ROS 텔레메트리 캐시 우선 — 로봇 HTTP 프록시(103KB × 1s 폴링, 파이 CPU 포화·504)를 제거.
    # 캐시가 stale(로봇 오프라인·브릿지 중단 등)이면 기존 프록시로 폴백한다.
    return await _state_for_robot(robot_id, nav_port, db)


@router.get("/telemetry")
async def telemetry(_admin: Admin = Depends(get_current_admin)):
    """진단용: ROS 텔레메트리 캐시의 로봇별 신선도(ros_age_sec 등)."""
    return fleet_telemetry.telemetry_info()


@router.post("/goal")
async def goal(body: GoalRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    res = await _ros_command(robot_id, nav_port, db, "goal", body.model_dump())
    if res is not None:
        return res
    return await _proxy(robot_id, nav_port, db, "/api/goal", "POST", body.model_dump())


@router.post("/slam/reset")
async def slam_reset(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    res = await _ros_command(robot_id, nav_port, db, "slam_reset", {}, timeout=SLAM_TIMEOUT_SEC)
    if res is not None:
        return res
    return await _proxy(robot_id, nav_port, db, "/api/slam/reset", "POST", {})


@router.post("/slam/save-map")
async def slam_save_map(body: SaveMapRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    res = await _ros_command(robot_id, nav_port, db, "slam_save_map", body.model_dump(), timeout=SLAM_TIMEOUT_SEC)
    if res is not None:
        return res
    return await _proxy(robot_id, nav_port, db, "/api/slam/save_map", "POST", body.model_dump())


@router.get("/locations")
async def locations(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    return await _db_locations(robot_id, db)


@router.post("/locations")
async def set_location(body: LocationSetRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name이 필요합니다")

    payload = body.model_dump(exclude_none=True)
    if body.x is None or body.y is None:
        # 좌표 없음 = 로봇의 현재 위치(TF) 캡처 — 응답의 x/y/yaw 로 DB 를 채운다
        res = await _ros_command(robot_id, nav_port, db, "loc_set", {"name": name})
        if res is None:
            res = await _proxy(robot_id, nav_port, db, "/api/locations/set", "POST", {"name": name})
        x = float(res["x"])
        y = float(res["y"])
        yaw = float(res.get("yaw", 0.0))
    else:
        x = float(body.x)
        y = float(body.y)
        yaw = float(body.yaw or 0.0)
        try:
            res = await _ros_command(robot_id, nav_port, db, "loc_set", payload)
            if res is None:
                await _proxy(robot_id, nav_port, db, "/api/locations/set", "POST", payload)
        except HTTPException:
            pass

    loc = await _upsert_db_location(robot_id, name, x, y, yaw, db)
    return {"success": True, "name": loc.name, **_location_payload(loc)}


@router.delete("/locations")
async def delete_location(body: LocationNameRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    name = body.name.strip()
    loc = (
        await db.execute(
            select(RobotLocation).where(RobotLocation.robot_id == robot_id, RobotLocation.name == name)
        )
    ).scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail=f"{name!r} 위치가 없습니다")
    await db.delete(loc)
    await db.commit()
    try:
        res = await _ros_command(robot_id, nav_port, db, "loc_delete", {"name": name})
        if res is None:
            await _proxy(robot_id, nav_port, db, "/api/locations/delete", "POST", {"name": name})
    except HTTPException:
        pass
    return {"success": True, "name": name}


@router.post("/goto")
async def goto(body: LocationNameRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    pinky_greeting_monitor.mark_active(f"id:{robot_id}", ttl=120.0)
    name = body.name.strip()
    loc = await _get_db_location(robot_id, db, name)
    # 위치를 명령에 내장 — 기존 HTTP 사전동기화(locations/set) 왕복을 없앤다
    res = await _ros_command(robot_id, nav_port, db, "goto", {"name": name, "location": _location_payload(loc)})
    if res is not None:
        return res
    await _sync_location_to_robot(robot_id, nav_port, db, name)
    return await _proxy(robot_id, nav_port, db, "/api/goto", "POST", {"name": name})


@router.get("/waypoints")
async def get_waypoints(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    """내비 그래프(vertices+lanes) 조회 — fleet_link.waypoint_get (ROS2 전용, HTTP 폴백 없음)."""
    res = await _ros_command(robot_id, nav_port, db, "waypoint_get", {})
    if res is None:
        raise HTTPException(status_code=503, detail="로봇과 ROS 링크가 연결되지 않았습니다")
    return res


class WaypointSaveRequest(BaseModel):
    vertices: dict[str, dict[str, float]]
    lanes: list[dict[str, Any]]


@router.put("/waypoints")
async def save_waypoints(body: WaypointSaveRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    """내비 그래프 저장 — fleet_link.waypoint_save."""
    res = await _ros_command(robot_id, nav_port, db, "waypoint_save", body.model_dump())
    if res is None:
        raise HTTPException(status_code=503, detail="로봇과 ROS 링크가 연결되지 않았습니다")
    return res


@router.post("/waypoints/{name}/goto")
async def waypoint_goto(name: str, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    """간선(lane)을 따라 지정 노드까지 순차 이동 — fleet_link.waypoint_goto."""
    pinky_greeting_monitor.mark_active(f"id:{robot_id}", ttl=120.0)
    res = await _ros_command(robot_id, nav_port, db, "waypoint_goto", {"name": name}, timeout=WAYPOINT_GOTO_TIMEOUT_SEC)
    if res is None:
        raise HTTPException(status_code=503, detail="로봇과 ROS 링크가 연결되지 않았습니다")
    return res


@router.post("/relative-move")
async def relative_move(body: RelativeMoveRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    base = await _target_url(robot_id, nav_port, db)
    ip = base.split("//", 1)[-1].rsplit(":", 1)[0]
    dir_str = "backward" if body.direction == "backward" else "forward"
    direction = -1.0 if dir_str == "backward" else 1.0
    linear = float(body.speed_mps) * direction
    duration = min(30.0, max(0.1, float(body.distance_m) / max(abs(linear), 0.01)))
    stop_gap = float(body.min_clearance_m)
    per_iter = 0.1  # 루프 주기(초)

    # relative_move 는 nav2 를 우회하는 저수준 조그다 — 스스로 장애물을 피하지 못하므로
    # 라이다 전방(후진 시 후방) 여유거리로 안전정지를 건다.
    # fail-safe: 여유거리를 확인할 수 없으면(스캔 없음/오래됨) 아예 움직이지 않는다.
    c0 = fleet_telemetry.clearance(ip, dir_str)
    if c0 is None:
        raise HTTPException(
            status_code=503,
            detail=f"{'후방' if dir_str == 'backward' else '전방'} 라이다 스캔을 받지 못해 "
                   f"안전상 이동을 취소했습니다 (스캔 브릿지/센서 확인 필요)",
        )
    if c0 < stop_gap:
        return {"success": False, "mode": "ros2_cmd_vel", "stopped_reason": "obstacle",
                "clearance_m": c0, "min_clearance_m": stop_gap, "published": 0,
                "message": f"진행 방향 {c0:.2f}m 앞에 장애물이 있어 이동하지 않았습니다 (임계 {stop_gap:.2f}m)"}

    started = time.time()
    sent = 0
    stopped_reason: str | None = None
    try:
        while time.time() - started < duration:
            c = fleet_telemetry.clearance(ip, dir_str)
            if c is None:
                stopped_reason = "scan_lost"
                break
            if c < stop_gap:
                stopped_reason = "obstacle"
                break
            if not fleet_telemetry.publish_cmd_vel(ip, linear, 0.0):
                raise HTTPException(status_code=503, detail="로봇 cmd_vel 토픽 링크가 연결되지 않았습니다")
            sent += 1
            await asyncio.sleep(per_iter)
    finally:
        for _ in range(3):
            fleet_telemetry.publish_cmd_vel(ip, 0.0, 0.0)
            await asyncio.sleep(0.03)

    traveled = round(min(float(body.distance_m), sent * per_iter * abs(linear)), 3)
    if stopped_reason == "obstacle":
        last_c = fleet_telemetry.clearance(ip, dir_str)
        return {"success": True, "mode": "ros2_cmd_vel", "stopped_reason": "obstacle",
                "clearance_m": last_c, "min_clearance_m": stop_gap,
                "distance_m": body.distance_m, "traveled_m": traveled, "published": sent,
                "message": f"장애물 감지로 {traveled:.2f}m 이동 후 정지했습니다 (앞 여유 임계 {stop_gap:.2f}m)"}
    if stopped_reason == "scan_lost":
        return {"success": True, "mode": "ros2_cmd_vel", "stopped_reason": "scan_lost",
                "distance_m": body.distance_m, "traveled_m": traveled, "published": sent,
                "message": f"라이다 신호가 끊겨 안전상 {traveled:.2f}m 이동 후 정지했습니다"}
    return {"success": True, "mode": "ros2_cmd_vel", "distance_m": body.distance_m,
            "speed_mps": abs(linear), "duration_s": round(duration, 2),
            "published": sent, "traveled_m": traveled}


@router.post("/mission/start")
async def mission_start(body: MissionRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    locs = await _db_locations(robot_id, db)
    names = body.names or sorted(locs.keys())
    missing = [n for n in names if n not in locs]
    if missing:
        raise HTTPException(status_code=404, detail=f"{missing!r} 위치가 없습니다")
    pinky_greeting_monitor.mark_active(f"id:{robot_id}", ttl=120.0)
    res = await _ros_command(
        robot_id, nav_port, db, "mission_start",
        {"names": names, "loop": body.loop, "locations": {n: locs[n] for n in names}},
    )
    if res is not None:
        return res
    await _sync_locations_to_robot(robot_id, nav_port, db, names)
    return await _proxy(robot_id, nav_port, db, "/api/mission/start", "POST", {**body.model_dump(), "names": names})


@router.post("/mission/stop")
async def mission_stop(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    res = await _ros_command(robot_id, nav_port, db, "mission_stop", {})
    if res is not None:
        return res
    return await _proxy(robot_id, nav_port, db, "/api/mission/stop", "POST", {})


@router.post("/home")
async def home(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    pinky_greeting_monitor.mark_active(f"id:{robot_id}", ttl=120.0)
    # 로봇별 HOME 구역이 DB에 등록돼 있으면 그 좌표로 goto(로봇마다 다른 홈).
    # 없으면 로봇 기본 홈 복귀(로컬 'HOME' 구역 또는 맵 원점)로 폴백한다.
    home_loc = (
        await db.execute(
            select(RobotLocation).where(RobotLocation.robot_id == robot_id, RobotLocation.name == "HOME")
        )
    ).scalar_one_or_none()
    if home_loc is not None:
        res = await _ros_command(robot_id, nav_port, db, "goto", {"name": "HOME", "location": _location_payload(home_loc)})
        if res is not None:
            return res
        await _sync_location_to_robot(robot_id, nav_port, db, "HOME")
        return await _proxy(robot_id, nav_port, db, "/api/goto", "POST", {"name": "HOME"})
    res = await _ros_command(robot_id, nav_port, db, "home", {})
    if res is not None:
        return res
    return await _proxy(robot_id, nav_port, db, "/api/home", "POST", {})


@router.post("/schedule/start")
async def schedule_start(body: ScheduleRequest, robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    locs = await _db_locations(robot_id, db)
    names = body.names or sorted(locs.keys())
    missing = [n for n in names if n not in locs]
    if missing:
        raise HTTPException(status_code=404, detail=f"{missing!r} 위치가 없습니다")
    pinky_greeting_monitor.mark_active(f"id:{robot_id}", ttl=120.0)
    res = await _ros_command(
        robot_id, nav_port, db, "schedule_start",
        {"minutes": body.minutes, "names": names, "loop": body.loop, "locations": {n: locs[n] for n in names}},
    )
    if res is not None:
        return res
    await _sync_locations_to_robot(robot_id, nav_port, db, names)
    return await _proxy(robot_id, nav_port, db, "/api/schedule/start", "POST", {**body.model_dump(), "names": names})


@router.post("/schedule/stop")
async def schedule_stop(robot_id: int = Query(...), nav_port: int = Query(NAV2_DEFAULT_PORT, ge=1, le=65535), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    res = await _ros_command(robot_id, nav_port, db, "schedule_stop", {})
    if res is not None:
        return res
    return await _proxy(robot_id, nav_port, db, "/api/schedule/stop", "POST", {})


# ── 구역 도착 액션 설정 CRUD ──────────────────────────────────────────────
# 구역(RobotLocation)에 도착한 뒤 실행할 액션을 robot_id + name 단위로 저장한다.
# 실행은 프론트(/admin/control)가 로봇을 직접 호출해 담당하고 여기서는 설정만 관리한다.

def _action_out(a: LocationAction) -> dict[str, Any]:
    return {
        "name": a.name,
        "action_type": a.action_type,
        "params": json.loads(a.params) if a.params else None,
        "enabled": a.enabled,
    }


@router.get("/location-actions")
async def list_location_actions(robot_id: int = Query(...), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    rows = (
        await db.execute(
            select(LocationAction).where(LocationAction.robot_id == robot_id).order_by(LocationAction.name)
        )
    ).scalars().all()
    return [_action_out(a) for a in rows]


@router.put("/location-actions/{name}")
async def upsert_location_action(name: str, body: LocationActionIn, robot_id: int = Query(...), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name이 필요합니다")
    if body.action_type not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 action_type: {body.action_type}")
    a = (
        await db.execute(
            select(LocationAction).where(LocationAction.robot_id == robot_id, LocationAction.name == name)
        )
    ).scalar_one_or_none()
    if a is None:
        a = LocationAction(robot_id=robot_id, name=name)
        db.add(a)
    a.action_type = body.action_type
    a.params = json.dumps(body.params, ensure_ascii=False) if body.params else None
    a.enabled = body.enabled
    await db.commit()
    await db.refresh(a)
    return _action_out(a)


@router.delete("/location-actions/{name}")
async def delete_location_action(name: str, robot_id: int = Query(...), db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    a = (
        await db.execute(
            select(LocationAction).where(LocationAction.robot_id == robot_id, LocationAction.name == name.strip())
        )
    ).scalar_one_or_none()
    if a:
        await db.delete(a)
        await db.commit()
    return {"success": True, "name": name.strip()}


# ── 근접 안전 코디네이터 (Phase 1) ─────────────────────────────────────────
# 중앙 백그라운드 서비스가 주행로봇 간 거리를 감시해 근접 시 자동 정지한다.
# 재개(주행)는 사람이 수동으로 한다.

@router.get("/fleet/coordinator")
async def get_coordinator(_admin: Admin = Depends(get_current_admin)):
    return coordinator.snapshot()

@router.websocket("/ws/fleet/coordinator")
async def coordinator_ws(
    ws: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_admin_db),
):
    admin = await _admin_from_ws_token(token, db)
    if admin is None:
        await ws.close(code=4001)
        return
    await ws.accept()
    try:
        while True:
            try:
                await ws.send_json({"ok": True, "state": coordinator.snapshot()})
            except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
                return
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


@router.put("/fleet/coordinator")
async def set_coordinator(body: CoordinatorConfigIn, _admin: Admin = Depends(get_current_admin)):
    coordinator.update_config(enabled=body.enabled, min_distance=body.min_distance, clear_distance=body.clear_distance, priorities=body.priorities, evasion_enabled=body.evasion_enabled)
    await coordinator.save()  # 변경 설정을 DB 에 영속화 — 재시작에도 유지
    return coordinator.snapshot()


@router.post("/fleet/resume")
async def resume_robot(robot_id: int = Query(...), _admin: Admin = Depends(get_current_admin)):
    return coordinator.resume(robot_id)
