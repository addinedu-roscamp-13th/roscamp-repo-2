"""
주행 제어 라우터.

WS   /api/robot/ws/drive    — 실시간 조이스틱 제어
WS   /api/robot/ws/explore  — occupancy grid 맵 스트림
POST /api/robot/explore/start
POST /api/robot/explore/stop
GET  /api/robot/explore/status
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import time
import subprocess
import re
import datetime
import io
import zipfile
from pathlib import Path
from typing import Literal
from pydantic import BaseModel

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, Response, HTTPException

from app.deps import get_current_admin
from app.security import decode_token
from app import ros_bridge, explorer


router = APIRouter(prefix="/api/robot", tags=["drive"])

_HW = Path(__file__).parent.parent / "hardware"
_MOTOR_SCRIPT = _HW / "motor_ctrl.py"
_SENSOR_SCRIPT = _HW / "sensor_ctrl.py"

MAX_SPEED = 75  # 최대 모터 속도 %

_explore_task: asyncio.Task | None = None
_explore_status: str = "idle"
_explore_log: list[str] = []

# ── Occupancy-grid / 맵 상태 ─────────────────────────────────────────────────
_map_clients: set[WebSocket] = set()
_map_grid: dict[tuple[int, int], int] = {}   # 격자 셀(ix, iy) → 장애물 감지 횟수
_path_history: list[tuple[float, float]] = []
_robot_pose: dict[str, float] = {"x": 0.0, "y": 0.0, "heading": 0.0}

# Dead-reckoning 파라미터 (경험치 기반 — 실제 로봇에서 보정 필요)
_CELL_CM   = 10.0    # cm per grid cell
_FWD_CELL_S = 1.4    # 전진 시 cells/s (FWD=42% 기준)
_TURN_DEG_S = 75.0   # 회전 시 deg/s  (TURN=46% 기준)

# 주행 모터 데몬(상주 프로세스) — 명령마다 sudo python3 를 띄우지 않고 재사용한다.
_motor_proc: asyncio.subprocess.Process | None = None
_motor_lock = asyncio.Lock()

# 센서 상주 데몬 + 최신값 캐시 — /sensor 읽기를 매번 sudo python3 로 띄우지 않는다(3.2초 → ~0.1초).
_sensor_proc: asyncio.subprocess.Process | None = None
_sensor_reader_task: asyncio.Task | None = None
_sensor_cache: dict = {"ts": 0.0}
_sensor_lock = asyncio.Lock()


async def _run(cmd: str, timeout: int = 10) -> dict:
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        ok = proc.returncode == 0
        return {
            "success": ok,
            "output": stdout.decode().strip(),
            "error": stderr.decode().strip() if not ok else "",
        }
    except asyncio.TimeoutError:
        if proc and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            except ProcessLookupError:
                pass
        return {"success": False, "output": "", "error": "응답 시간 초과"}
    except Exception as exc:
        return {"success": False, "output": "", "error": str(exc)}


async def _motor_send(left: int, right: int) -> None:
    """주행 모터 데몬에 속도를 전달한다.

    상주 데몬 1개를 재사용하므로 명령(120ms 주기)마다 프로세스를 띄우지 않는다.
    데몬이 없는데 정지 명령이면 띄울 필요가 없다."""
    global _motor_proc
    async with _motor_lock:
        proc = _motor_proc
        if proc is None or proc.returncode is not None:
            if left == 0 and right == 0:
                return
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "python3", str(_MOTOR_SCRIPT), "daemon",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            _motor_proc = proc
        try:
            assert proc.stdin is not None
            proc.stdin.write(f"{left} {right}\n".encode())
            await proc.stdin.drain()
        except Exception:
            _motor_proc = None  # 데몬이 죽었으면 다음 호출에서 재시작


async def _motor_close() -> None:
    """주행 모터 데몬을 정지·종료해 GPIO 를 반환한다."""
    global _motor_proc
    async with _motor_lock:
        proc = _motor_proc
        _motor_proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            assert proc.stdin is not None
            proc.stdin.write(b"stop\nquit\n")
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            except ProcessLookupError:
                pass


def _vel_to_speeds(linear: float, angular: float) -> tuple[int, int]:
    """정규화된 linear/angular (-1..1) → 모터 속도 (-100..100)."""
    lin = max(-1.0, min(1.0, float(linear)))
    ang = max(-1.0, min(1.0, float(angular)))
    left = int((lin - ang) * MAX_SPEED)
    right = int((lin + ang) * MAX_SPEED)
    return max(-100, min(100, left)), max(-100, min(100, right))


# ── WebSocket: 조이스틱 제어 ──────────────────────────────────────────────────

@router.websocket("/ws/drive")
async def drive_ws(websocket: WebSocket, token: str = Query(...)):
    """
    Client → Server: {"linear": float, "angular": float}  (-1..1 정규화)
    Server → Client: {"type": "status", "ok": bool, "left": int, "right": int}
    """
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            linear = float(data.get("linear", 0.0))
            angular = float(data.get("angular", 0.0))
            left, right = _vel_to_speeds(linear, angular)
            await _motor_send(left, right)
            await websocket.send_json({
                "type": "status",
                "ok": True,
                "left": left,
                "right": right,
            })
    except (WebSocketDisconnect, Exception):
        await _motor_close()


# ── 자율탐색 ─────────────────────────────────────────────────────────────────

async def _ensure_sensor_daemon() -> None:
    """센서 상주 데몬을 띄우고 stdout 을 읽어 캐시에 채우는 백그라운드 태스크를 보장."""
    global _sensor_proc, _sensor_reader_task
    async with _sensor_lock:
        if _sensor_proc is not None and _sensor_proc.returncode is None:
            return
        _sensor_proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "python3", str(_SENSOR_SCRIPT), "daemon",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        _sensor_reader_task = asyncio.create_task(_sensor_reader(_sensor_proc))


async def _sensor_reader(proc: asyncio.subprocess.Process) -> None:
    global _sensor_cache
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                rec = json.loads(line.decode())
            except Exception:
                continue
            rec["ts"] = time.time()
            _sensor_cache = rec
    except Exception:
        pass


def _cache_fresh() -> bool:
    return (time.time() - _sensor_cache.get("ts", 0.0)) < 2.0


async def _read_dist() -> float | None:
    """캐시(상주 데몬)에서 즉시 거리(cm)를 반환. 미준비 시 1회 직접 읽기로 폴백."""
    await _ensure_sensor_daemon()
    if _cache_fresh() and "distance_cm" in _sensor_cache:
        d = _sensor_cache.get("distance_cm")
        return float(d) if d else None
    res = await _run(f"sudo -n python3 {_SENSOR_SCRIPT} ultrasonic", timeout=6)
    if res["success"] and res["output"]:
        try:
            return float(json.loads(res["output"]).get("distance_cm") or 0) or None
        except Exception:
            pass
    return None


async def _read_ir() -> dict | None:
    await _ensure_sensor_daemon()
    if _cache_fresh() and "left" in _sensor_cache:
        return {
            "left": _sensor_cache.get("left"),
            "center": _sensor_cache.get("center"),
            "right": _sensor_cache.get("right"),
        }
    res = await _run(f"sudo -n python3 {_SENSOR_SCRIPT} ir", timeout=6)
    if res["success"] and res["output"]:
        try:
            return json.loads(res["output"])
        except Exception:
            pass
    return None


async def cached_ultrasonic_result() -> dict:
    """robot.py /sensor/ultrasonic 용 — 캐시에서 즉시 RobotResult 형태로 반환."""
    await _ensure_sensor_daemon()
    if _cache_fresh() and "distance_cm" in _sensor_cache:
        payload = {
            "sensor": "ultrasonic",
            "distance_m": _sensor_cache.get("distance_m"),
            "distance_cm": _sensor_cache.get("distance_cm"),
        }
        return {"success": True, "output": json.dumps(payload), "error": ""}
    return await _run(f"sudo -n python3 {_SENSOR_SCRIPT} ultrasonic", timeout=8)


async def cached_ir_result() -> dict:
    """robot.py /sensor/ir 용 — 캐시에서 즉시 RobotResult 형태로 반환."""
    await _ensure_sensor_daemon()
    if _cache_fresh() and "left" in _sensor_cache:
        left = _sensor_cache.get("left") or 0
        center = _sensor_cache.get("center") or 0
        right = _sensor_cache.get("right") or 0
        payload = {
            "sensor": "ir",
            "left": left,
            "center": center,
            "right": right,
            "obstacle": bool(left or center or right),
        }
        return {"success": True, "output": json.dumps(payload), "error": ""}
    return await _run(f"sudo -n python3 {_SENSOR_SCRIPT} ir", timeout=8)


def _log(msg: str) -> None:
    global _explore_log
    _explore_log.append(msg)
    if len(_explore_log) > 60:
        _explore_log = _explore_log[-60:]


def _reset_map() -> None:
    global _map_grid, _path_history, _robot_pose
    _map_grid.clear()
    _path_history.clear()
    _robot_pose = {"x": 0.0, "y": 0.0, "heading": 0.0}


async def _broadcast_map(dist: float | None, state: str) -> None:
    global _map_clients
    if not _map_clients:
        return
    payload = json.dumps({
        "type": "map",
        "robot": _robot_pose,
        "obstacles": list(_map_grid.keys()),
        "path": _path_history[-400:],
        "state": state,
        "dist": dist,
    })
    dead: set[WebSocket] = set()
    for ws in list(_map_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _map_clients.difference_update(dead)


async def _exploration_loop() -> None:
    global _explore_status, _explore_log, _robot_pose, _path_history

    _explore_status = "running"
    _explore_log = ["자율탐색 시작"]

    FWD = 42; TURN = 46; BACK = 38
    CLEAR = 35.0; RESUME = 50.0; TOO_CLOSE = 18.0
    turn_dir = 1
    state = "forward"
    prev_state = "forward"
    prev_turn_dir = 1
    loop = asyncio.get_event_loop()
    last_t = loop.time()

    await _ensure_sensor_daemon()
    try:
        while True:
            now = loop.time()
            dt = now - last_t
            last_t = now

            # ── Dead reckoning: 직전 루프의 명령 기준으로 포즈 업데이트 ──
            hrad = math.radians(_robot_pose["heading"])
            if prev_state == "forward":
                cells = _FWD_CELL_S * dt
                _robot_pose["x"] += math.sin(hrad) * cells
                _robot_pose["y"] -= math.cos(hrad) * cells
                pt = (_robot_pose["x"], _robot_pose["y"])
                _path_history.append(pt)
                if len(_path_history) > 2000:
                    del _path_history[:1000]
            elif prev_state == "turn":
                _robot_pose["heading"] = (
                    _robot_pose["heading"] + prev_turn_dir * _TURN_DEG_S * dt
                ) % 360

            dist = await _read_dist()

            # ── 장애물 셀 기록 ──
            if dist is not None:
                obs_hrad = math.radians(_robot_pose["heading"])
                obs_x = _robot_pose["x"] + math.sin(obs_hrad) * dist / _CELL_CM
                obs_y = _robot_pose["y"] - math.cos(obs_hrad) * dist / _CELL_CM
                cell: tuple[int, int] = (round(obs_x), round(obs_y))
                _map_grid[cell] = min(_map_grid.get(cell, 0) + 1, 15)

            if dist is None:
                await _motor_send(0, 0)
                prev_state = "stop"
                await _broadcast_map(dist, state)
                await asyncio.sleep(0.15)
                continue

            prev_turn_dir = turn_dir

            if state == "forward":
                if dist < CLEAR:
                    ir = await _read_ir()
                    left_v = (ir or {}).get("left") or 0
                    right_v = (ir or {}).get("right") or 0
                    turn_dir = -1 if left_v > right_v else 1
                    state = "turn"
                    prev_state = "turn"
                    _log(f"장애물 {dist:.0f}cm — 회전 시작")
                else:
                    await _motor_send(FWD, FWD)
                    prev_state = "forward"

            if state == "turn":
                if dist > RESUME:
                    state = "forward"
                    await _motor_send(FWD, FWD)
                    prev_state = "forward"
                    _log(f"전진 (전방 {dist:.0f}cm)")
                elif dist < TOO_CLOSE:
                    await _motor_send(-BACK, -BACK)
                    prev_state = "back"
                else:
                    await _motor_send(turn_dir * TURN, -turn_dir * TURN)
                    prev_state = "turn"
                    prev_turn_dir = turn_dir

            await _broadcast_map(dist, state)
            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        _log("탐색 중지")
        await _motor_send(0, 0)
        await _motor_close()
        _explore_status = "stopped"
        raise


@router.websocket("/ws/explore")
async def explore_map_ws(websocket: WebSocket, token: str = Query(...)):
    """occupancy-grid 맵 데이터 스트림 — 탐색 루프가 100ms마다 브로드캐스트."""
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _map_clients.add(websocket)
    # 현재 맵 상태를 즉시 전송
    try:
        await websocket.send_text(json.dumps({
            "type": "map",
            "robot": _robot_pose,
            "obstacles": list(_map_grid.keys()),
            "path": _path_history[-400:],
            "state": _explore_status,
            "dist": None,
        }))
        while True:
            await websocket.receive_text()   # keep-alive ping 수신
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _map_clients.discard(websocket)


@router.post("/explore/start")
async def explore_start(_=Depends(get_current_admin)):
    from app import ros_bridge, explorer
    if ros_bridge.is_active():
        ok = explorer.start()
        return {"ok": ok, **_get_explore_state()}
    else:
        global _explore_task, _explore_status
        if _explore_task and not _explore_task.done():
            return {"ok": False, "error": "이미 탐색 중입니다", **_get_explore_state()}
        _reset_map()
        _explore_task = asyncio.create_task(_exploration_loop())
        return {"ok": True, **_get_explore_state()}


@router.post("/explore/stop")
async def explore_stop(_=Depends(get_current_admin)):
    from app import ros_bridge, explorer
    if ros_bridge.is_active():
        explorer.stop()
        return {"ok": True, **_get_explore_state()}
    else:
        global _explore_task, _explore_status
        if _explore_task and not _explore_task.done():
            _explore_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(_explore_task), timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        _explore_status = "idle"
        return {"ok": True, **_get_explore_state()}


def _get_explore_state() -> dict:
    from app import ros_bridge, explorer
    if ros_bridge.is_active():
        st = explorer.get_state()
        return {
            "running": st["running"],
            "status": st["status"],
            "log": [st["message"]] if st["message"] else [],
            "message": st["message"],
        }
    else:
        running = _explore_task is not None and not _explore_task.done()
        return {
            "running": running,
            "status": _explore_status if running else "idle",
            "log": _explore_log[-25:],
            "message": _explore_log[-1] if _explore_log else "",
        }


@router.get("/explore/status")
async def get_explore_status(_=Depends(get_current_admin)):
    return _get_explore_state()


# ── 프로세스 관리 ─────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # /home/Aiprj/bot_ai_server
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOG_DIR = PROJECT_ROOT / "logs"
ROS_SETUP = "/opt/ros/jazzy/setup.bash"

MAP_DIR = PROJECT_ROOT / "maps"
MAP_FILE = MAP_DIR / "map"  # → map.yaml + map.pgm

ProcessName = Literal["teleop", "obstacle_avoid", "slam", "nav2"]

COMMANDS: dict[str, list[str]] = {
    "teleop": [str(SCRIPTS_DIR / "run_turtlebot3_teleop.sh")],
    "obstacle_avoid": [str(SCRIPTS_DIR / "run_obstacle_avoid.sh")],
    "slam": [],   # _build_command("slam")에서 생성
    "nav2": [],   # _build_command("nav2")에서 생성
}

def _detect_use_sim_time() -> str:
    r = _run_ros("ros2 topic list", timeout=6)
    topics = r.get("stdout") or ""
    return "true" if "/clock" in topics.split() else "false"

def _build_command(name: str) -> list[str]:
    if name == "slam":
        ust = _detect_use_sim_time()
        return ["bash", "-c",
                f"source {ROS_SETUP} && "
                "export TURTLEBOT3_MODEL=${TURTLEBOT3_MODEL:-burger} && "
                "ros2 launch slam_toolbox online_async_launch.py "
                f"slam_params_file:={PROJECT_ROOT}/config/slam_params.yaml "
                f"use_sim_time:={ust}"]
    if name == "nav2":
        ust = _detect_use_sim_time()
        # [2026-07-26] nav2 파라미터는 pinky_navigation 패키지 것 **하나만** 쓴다.
        # (robot_agent/app/routers/driving.py 의 같은 자리와 동일한 이유 — 그쪽 주석 참고)
        #
        # 예전 {PROJECT_ROOT}/config/nav2_params.yaml 은 MPPI/xy_tol 0.25/radius 0.22 로,
        # pi.sh·pm2 가 띄우는 실제 설정(RPP/0.03/0.06)과 달랐다. xy_goal_tolerance 0.25 는
        # fleet_node 의 arrive_radius 0.05 보다 커서 "nav2 는 도착, fleet_node 는 미도착"
        # 교착을 만든다.
        return ["bash", "-c", "\n".join([
            f"source {ROS_SETUP} || exit 1",
            "export TURTLEBOT3_MODEL=${TURTLEBOT3_MODEL:-burger}",
            'NAV2_SHARE="$(ros2 pkg prefix --share pinky_navigation 2>/dev/null || true)"',
            'NAV2_PARAMS="$NAV2_SHARE/params/nav2_params.yaml"',
            'if [ -z "$NAV2_SHARE" ] || [ ! -f "$NAV2_PARAMS" ]; then',
            '  echo "[nav2] pinky_navigation 파라미터를 찾지 못했습니다: $NAV2_PARAMS" >&2',
            '  echo "[nav2] ros_ws 에서 colcon build 후 install/setup.bash 를 source 했는지 확인" >&2',
            "  exit 1",
            "fi",
            "exec ros2 launch nav2_bringup navigation_launch.py "
            f'params_file:="$NAV2_PARAMS" use_sim_time:={ust}',
        ])]
    return COMMANDS[name]

_processes: dict[str, subprocess.Popen] = {}

def _log_path(name: str) -> Path:
    return LOG_DIR / f"{name}.log"

def _proc_status(name: str) -> dict:
    proc = _processes.get(name)
    log = str(_log_path(name))
    if proc is None:
        return {"name": name, "running": False, "pid": None, "log_file": log}
    rc = proc.poll()
    running = rc is None
    return {"name": name, "running": running, "pid": proc.pid, "returncode": rc, "log_file": log}

def _run_ros(cmd: str, timeout: int = 10) -> dict:
    try:
        r = subprocess.run(
            ["bash", "-c", f"source {ROS_SETUP} && {cmd}"],
            cwd=str(PROJECT_ROOT), text=True, capture_output=True, timeout=timeout, check=False,
        )
        return {"command": cmd, "returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "returncode": -1, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {"command": cmd, "returncode": -1, "stdout": "", "stderr": str(e)}

def _kill_proc(name: str) -> None:
    proc = _processes.get(name)
    if proc is None:
        return
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=8)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
            except Exception:
                pass
    _processes.pop(name, None)

@router.get("/status", summary="ROS 상태 + 프로세스 목록")
async def robot_status(_=Depends(get_current_admin)):
    use_sim_time = _detect_use_sim_time()
    from app import ros_bridge
    return {
        "ros_active": ros_bridge.is_active(),
        "mode": "sim" if use_sim_time == "true" else "real",
        "use_sim_time": use_sim_time == "true",
        "ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "172"),
        "processes": {name: _proc_status(name) for name in COMMANDS},
    }

@router.post("/process/{name}/start", summary="프로세스 시작")
async def start_process(name: ProcessName, _=Depends(get_current_admin)):
    _kill_proc(name)
    if name == "slam":
        r = _run_ros("ros2 pkg prefix slam_toolbox", timeout=5)
        if r["returncode"] != 0:
            return {"error": "slam_toolbox가 설치되어 있지 않습니다."}
    if name == "nav2":
        r = _run_ros("ros2 pkg prefix nav2_bringup", timeout=5)
        if r["returncode"] != 0:
            return {"error": "nav2_bringup이 설치되어 있지 않습니다."}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lf = _log_path(name).open("ab")
    import subprocess
    proc = subprocess.Popen(
        _build_command(name), cwd=str(PROJECT_ROOT),
        stdout=lf, stderr=subprocess.STDOUT, start_new_session=True,
    )
    _processes[name] = proc
    return _proc_status(name)

@router.post("/process/{name}/stop", summary="프로세스 중지")
async def stop_process(name: ProcessName, _=Depends(get_current_admin)):
    _kill_proc(name)
    return _proc_status(name)

@router.get("/process/{name}/log", summary="프로세스 로그")
async def process_log(name: ProcessName, lines: int = 100, _=Depends(get_current_admin)):
    path = _log_path(name)
    if not path.exists():
        return {"log": ""}
    content = path.read_text(errors="replace").splitlines()
    return {"log": "\n".join(content[-lines:])}

class MapSaveIn(BaseModel):
    name: str = ""

def _sanitize_map_name(name: str) -> str:
    import re
    clean = re.sub(r"[^a-zA-Z0-9_\-가-힣]", "_", name.strip())[:64]
    import datetime
    return clean or datetime.datetime.now().strftime("map_%Y%m%d_%H%M%S")

@router.post("/map/save", summary="현재 SLAM 지도 저장")
async def save_map(body: MapSaveIn = MapSaveIn(), _=Depends(get_current_admin)):
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    import datetime
    name = _sanitize_map_name(body.name) if body.name else datetime.datetime.now().strftime("map_%Y%m%d_%H%M%S")
    out_path = MAP_DIR / name
    ust = _detect_use_sim_time()
    r = _run_ros(
        f"ros2 run nav2_map_server map_saver_cli -f {out_path} "
        f"--ros-args -p use_sim_time:={ust} -p save_map_timeout:=10.0",
        timeout=45,
    )
    yaml_exists = (MAP_DIR / f"{name}.yaml").exists()
    if r["returncode"] == 0 or yaml_exists:
        return {"ok": True, "name": name, "yaml_exists": yaml_exists}
    full = r["stderr"] or r["stdout"]
    error_lines = [l for l in full.splitlines() if "[ERROR]" in l or "Failed" in l]
    msg = "\n".join(error_lines) if error_lines else full[-300:]
    return {"ok": False, "error": msg}

@router.post("/map/reset", summary="현재 SLAM 맵 초기화")
async def reset_map(_=Depends(get_current_admin)):
    from app import ros_bridge
    ros_bridge.clear_map()
    was_running = _proc_status("slam")["running"]
    if not was_running:
        return {"ok": True, "restarted": False, "note": "맵 캐시를 비웠습니다. SLAM은 실행 중이 아닙니다."}

    _kill_proc("slam")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lf = _log_path("slam").open("ab")
    import subprocess
    proc = subprocess.Popen(
        _build_command("slam"), cwd=str(PROJECT_ROOT),
        stdout=lf, stderr=subprocess.STDOUT, start_new_session=True,
    )
    _processes["slam"] = proc
    return {"ok": True, "restarted": True, **_proc_status("slam")}

@router.get("/map/list", summary="저장된 지도 목록")
async def map_list(_=Depends(get_current_admin)):
    if not MAP_DIR.exists():
        return {"maps": []}
    maps = []
    for yaml_path in sorted(MAP_DIR.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True):
        pgm_path = yaml_path.with_suffix(".pgm")
        stat = yaml_path.stat()
        maps.append({
            "name": yaml_path.stem,
            "yaml": str(yaml_path),
            "pgm": str(pgm_path) if pgm_path.exists() else None,
            "mtime": stat.st_mtime,
            "size_kb": round(stat.st_size / 1024, 1),
        })
    return {"maps": maps}

@router.get("/map/{name}/download", summary="저장된 지도 다운로드")
async def download_map(name: str, _=Depends(get_current_admin)):
    name = _sanitize_map_name(name)
    yaml_path = MAP_DIR / f"{name}.yaml"
    pgm_path = MAP_DIR / f"{name}.pgm"
    from fastapi import HTTPException
    if not yaml_path.exists() and not pgm_path.exists():
        raise HTTPException(status_code=404, detail=f"지도를 찾을 수 없습니다: {name}")

    import io, zipfile
    from fastapi import Response
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in (yaml_path, pgm_path):
            if p.exists():
                zf.write(p, arcname=p.name)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )

@router.delete("/map/{name}", summary="저장된 지도 삭제")
async def delete_map(name: str, _=Depends(get_current_admin)):
    name = _sanitize_map_name(name)
    deleted = []
    for ext in (".yaml", ".pgm"):
        p = MAP_DIR / f"{name}{ext}"
        if p.exists():
            p.unlink()
            deleted.append(str(p))
    return {"ok": True, "deleted": deleted}

# ── WebSocket: 실제 SLAM 맵 스트림 (2D/3D 트윈용) ──────────────
# 실제 slam_toolbox OccupancyGrid(/map) + odom + 프론티어 탐색 상태를 스트림한다.
# 조기 정의된 센서기반 /ws/explore 와 경로가 겹치지 않도록 /ws/slam_map 으로 노출한다.
@router.websocket("/ws/slam_map")
async def ws_slam_map(ws: WebSocket, token: str = Query(...)):
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4001)
        return
    await ws.accept()
    last_map_hash: int | None = None
    from app import ros_bridge, explorer

    async def _stream():
        nonlocal last_map_hash
        while True:
            try:
                map_data = ros_bridge.get_topic("map")
                if map_data:
                    h = hash(tuple(map_data["data"][:100]))
                    if h != last_map_hash:
                        last_map_hash = h
                        await ws.send_json({"type": "map", **map_data})

                odom = ros_bridge.get_topic("odom")
                if odom:
                    await ws.send_json({"type": "odom", **odom})

                await ws.send_json({"type": "explore", **explorer.get_state()})
            except Exception:
                return
            await asyncio.sleep(0.2)

    async def _recv():
        try:
            while True:
                frame = await ws.receive()
                if frame.get("type") == "websocket.disconnect":
                    return
                raw = frame.get("text") or (frame.get("bytes", b"").decode() if frame.get("bytes") else None)
                if raw:
                    msg = json.loads(raw)
                    cmd = msg.get("cmd")
                    if cmd == "start":
                        explorer.start()
                    elif cmd == "stop":
                        explorer.stop()
        except Exception:
            return

    stream_task = asyncio.create_task(_stream())
    recv_task = asyncio.create_task(_recv())
    try:
        await asyncio.wait({stream_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # 맵 뷰어 연결이 끊겨도 탐색은 계속되어야 한다(explorer.stop() 호출 금지).
        stream_task.cancel()
        recv_task.cancel()


class TargetModeReq(BaseModel):
    target: str

@router.get("/drive/target", summary="현재 제어 타겟 조회")
async def get_drive_target(_=Depends(get_current_admin)):
    return {"target": "real"}

@router.post("/drive/target", summary="제어 타겟 설정")
async def set_drive_target(req: TargetModeReq, _=Depends(get_current_admin)):
    if req.target != "real":
        raise HTTPException(status_code=400, detail="실제 로봇 모드만 지원합니다")
    return {"ok": True, "target": "real"}


