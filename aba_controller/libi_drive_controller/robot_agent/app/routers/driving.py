import asyncio
import datetime
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import List, Literal, cast

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.bridge import bridge
from app.drivers.driving_driver import DrivingDriver
from app.schemas.driving import MoveRequest, RotateRequest

router = APIRouter()


def _driver() -> DrivingDriver:
    return cast(DrivingDriver, bridge.driver)


# LCD 텍스트/부저는 subprocess 유지, IMU는 pinky_imu_bno055 추가 전까지 subprocess 유지
_HW = Path(__file__).parent.parent / "hardware"
_LCD_SCRIPT = _HW / "lcd_ctrl.py"      # 이미지 표시용만 유지
_SENSOR_SCRIPT = _HW / "sensor_ctrl.py"  # IMU 전용

_IMAGES_DIR = _HW / "uploads" / "images"
_FONTS_DIR = _HW / "uploads" / "fonts"

_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
_FONTS_DIR.mkdir(parents=True, exist_ok=True)


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


def _safe_name(filename: str) -> str:
    name = os.path.basename(filename or "upload")
    name = re.sub(r"[^\w\-.]", "_", name)
    return name or "upload"


def _unique_upload_path(filename: str) -> Path:
    safe = _safe_name(filename)
    stem = Path(safe).stem or "image"
    suffix = Path(safe).suffix or ".png"
    candidate = _IMAGES_DIR / safe
    if not candidate.exists():
        return candidate
    token = int(time.time() * 1000)
    return _IMAGES_DIR / f"{stem}_{token}{suffix}"


# ── 주행 / 회전 API ───────────────────────────────────────────────────

@router.post("/move")
def move(req: MoveRequest):
    """직선 이동"""
    return _driver().move(req.direction, req.distance, req.speed)


@router.post("/rotate")
def rotate(req: RotateRequest):
    """제자리 회전"""
    return _driver().rotate(req.angle, req.speed)


# ── WebSocket 조이스틱 ─────────────────────────────────────────────────

@router.websocket("/ws/drive")
async def drive_ws(websocket: WebSocket):
    """실시간 조이스틱 제어 — /cmd_vel 토픽 발행"""
    from app.core import ros_bridge
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            linear = float(data.get("linear", 0.0))
            angular = float(data.get("angular", 0.0))
            ros_bridge.publish_cmd_vel(linear, angular)
            await websocket.send_json({"type": "status", "ok": True, "linear": linear, "angular": angular})
    except (WebSocketDisconnect, Exception):
        ros_bridge.publish_cmd_vel(0.0, 0.0)


# ── 프로세스 관리 ────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOG_DIR = PROJECT_ROOT / "logs"
ROS_SETUP = "/opt/ros/jazzy/setup.bash"

MAP_DIR = PROJECT_ROOT / "maps"
MAP_FILE = MAP_DIR / "map"

ProcessName = Literal["teleop", "obstacle_avoid", "slam", "nav2"]

COMMANDS: dict[str, list[str]] = {
    "teleop": [str(SCRIPTS_DIR / "run_turtlebot3_teleop.sh")],
    "obstacle_avoid": [str(SCRIPTS_DIR / "run_obstacle_avoid.sh")],
    "slam": [],
    "nav2": [],
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
        #
        # 예전엔 {PROJECT_ROOT}/config/nav2_params.yaml 을 가리켰다. 그 파일은 pi.sh·pm2 가
        # 띄우는 실제 설정과 완전히 다른 물건이었다:
        #     API 쪽   MPPI  20 Hz  xy_tol 0.25  yaw_tol 0.25  radius 0.22  inflation 0.70
        #     실제     RPP   10 Hz  xy_tol 0.03  yaw_tol 0.15  radius 0.06  inflation 0.08
        # 즉 **어느 경로로 nav2 를 켰느냐에 따라 로봇이 다르게 움직였다.**
        #
        # 특히 xy_goal_tolerance 0.25 는 fleet_node 의 arrive_radius 0.05 보다 크다.
        # nav2 는 25 cm 지점에서 "도착"이라며 goal 을 끝내는데 fleet_node 는 5 cm 안에
        # 들어와야 도착으로 인정하므로, 로봇은 멈춰 서고 관제는 영원히 기다린다(교착).
        # pinky_navigation/params/nav2_params.yaml 주석에 그 부등식과 실측 사례가 있다:
        #     update_min_d(0.02) < xy_goal_tolerance(0.03) < arrive_radius(0.05) < 최소 레인(0.062)
        #
        # 경로를 하드코딩하지 않고 패키지 share 에서 찾는다 — 설치 위치가 로봇마다 달라도
        # 따라가고, 패키지가 없으면 조용히 엉뚱한 파일을 쓰는 대신 큰 소리로 실패한다.
        # 각 단계를 줄로 나눈다. 한 줄 `A && B && C=$(...) && [ -f ] || {…}` 형태는
        # ① && 와 || 가 같은 우선순위라 앞 단계 실패에도 뒤 메시지가 나오고
        # ② `C=$(...)` 의 종료상태가 명령치환 결과라, 패키지를 못 찾으면 체인이 거기서 끊겨
        #    아래 안내 메시지가 아예 출력되지 않는다. 둘 다 디버깅을 방해한다.
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
async def robot_status():
    use_sim_time = _detect_use_sim_time()
    from app.core import ros_bridge
    return {
        "ros_active": ros_bridge.is_active(),
        "mode": "sim" if use_sim_time == "true" else "real",
        "use_sim_time": use_sim_time == "true",
        "ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "172"),
        "processes": {name: _proc_status(name) for name in COMMANDS},
    }


@router.post("/process/{name}/start", summary="프로세스 시작")
async def start_process(name: ProcessName):
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
    proc = subprocess.Popen(
        _build_command(name), cwd=str(PROJECT_ROOT),
        stdout=lf, stderr=subprocess.STDOUT, start_new_session=True,
    )
    _processes[name] = proc
    return _proc_status(name)


@router.post("/process/{name}/stop", summary="프로세스 중지")
async def stop_process(name: ProcessName):
    _kill_proc(name)
    return _proc_status(name)


@router.get("/process/{name}/log", summary="프로세스 로그")
async def process_log(name: ProcessName, lines: int = 100):
    path = _log_path(name)
    if not path.exists():
        return {"log": ""}
    content = path.read_text(errors="replace").splitlines()
    return {"log": "\n".join(content[-lines:])}


class TargetModeReq(BaseModel):
    target: str


@router.get("/drive/target", summary="현재 제어 타겟 조회")
async def get_drive_target():
    return {"target": "real"}


@router.post("/drive/target", summary="제어 타겟 설정")
async def set_drive_target(req: TargetModeReq):
    if req.target != "real":
        raise HTTPException(status_code=400, detail="실제 로봇 모드만 지원합니다")
    return {"ok": True, "target": "real"}


# ── LCD: 표정 (ROS2 /set_emotion 서비스) ─────────────────────────────

VALID_EMOTIONS = {"angry", "basic", "bored", "hello", "interest", "fun", "happy", "sad"}


class EmotionReq(BaseModel):
    emotion: str


@router.post("/lcd/emotion")
async def set_emotion(req: EmotionReq):
    if req.emotion not in VALID_EMOTIONS:
        raise HTTPException(400, f"유효하지 않은 표정: {req.emotion}")
    from app.core import ros_bridge
    ok = ros_bridge.call_set_emotion(req.emotion)
    return {"success": ok, "output": req.emotion if ok else "", "error": "" if ok else "ROS 브리지 미연결"}


@router.post("/lcd/stop")
async def lcd_stop():
    from app.core import ros_bridge
    ok = ros_bridge.call_lcd_stop()
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


# ── LCD: 텍스트/이미지/폰트 (FastAPI 유지) ───────────────────────────

@router.post("/lcd/image")
async def lcd_image(file: UploadFile = File(...)):
    ct = file.content_type or ""
    if not ct.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다")
    dest = _unique_upload_path(file.filename or "image.png")
    content = await file.read()
    dest.write_bytes(content)
    res = await _run(f"sudo -n python3 {_LCD_SCRIPT} image {dest}", timeout=15)
    return {
        **res,
        "image": {
            "id": hash(dest.name),
            "filename": dest.name,
            "original_name": file.filename or dest.name,
            "content_type": ct,
            "size_bytes": len(content),
            "created_at": datetime.datetime.now().isoformat()
        }
    }


class ImageSelectReq(BaseModel):
    filename: str


@router.post("/lcd/image/select")
async def lcd_image_select(req: ImageSelectReq):
    safe = _safe_name(req.filename)
    dest = _IMAGES_DIR / safe
    if not dest.exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return await _run(f"sudo -n python3 {_LCD_SCRIPT} image {dest}", timeout=15)


@router.get("/lcd/images")
async def list_images():
    exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    images = []
    if _IMAGES_DIR.exists():
        for f in sorted(_IMAGES_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in exts:
                images.append({
                    "id": hash(f.name),
                    "filename": f.name,
                    "original_name": f.name,
                    "content_type": f"image/{f.suffix.lower().strip('.')}",
                    "size_bytes": f.stat().st_size,
                    "created_at": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                })
    return {"images": images}


@router.delete("/lcd/images/{name}")
async def delete_image(name: str):
    safe = _safe_name(name)
    (_IMAGES_DIR / safe).unlink(missing_ok=True)
    return {"success": True}


class LcdTextReq(BaseModel):
    text: str
    font_name: str = "default"
    font_size: int = Field(24, ge=8, le=96)
    color: str = "#ffffff"
    bg_color: str = "#000000"
    align: str = "center"
    scroll: bool = False
    scroll_speed: int = Field(3, ge=1, le=20)


@router.post("/lcd/text")
async def lcd_text(req: LcdTextReq):
    font_path = ""
    if req.font_name != "default":
        fp = _FONTS_DIR / _safe_name(req.font_name)
        if fp.exists():
            font_path = str(fp)
    from app.core import ros_bridge
    ok = ros_bridge.call_lcd_text(
        text=req.text,
        font_path=font_path,
        font_size=req.font_size,
        color=req.color,
        bg_color=req.bg_color,
        align=req.align,
        scroll=req.scroll,
        scroll_speed=req.scroll_speed,
    )
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


@router.post("/lcd/font")
async def lcd_font(file: UploadFile = File(...)):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in {"ttf", "otf"}:
        raise HTTPException(400, "TTF 또는 OTF 파일만 업로드 가능합니다")
    safe = _safe_name(file.filename or f"font.{ext}")
    dest = _FONTS_DIR / safe
    content = await file.read()
    dest.write_bytes(content)
    return {"success": True, "filename": safe}


@router.get("/lcd/fonts")
async def list_fonts():
    exts = {".ttf", ".otf"}
    files = sorted(f.name for f in _FONTS_DIR.iterdir() if f.suffix.lower() in exts)
    return {"fonts": files}


@router.delete("/lcd/fonts/{name}")
async def delete_font(name: str):
    (_FONTS_DIR / _safe_name(name)).unlink(missing_ok=True)
    return {"success": True}


# ── LED 제어 (ROS2 /set_led 서비스) ──────────────────────────────────

class LedFillReq(BaseModel):
    r: int = Field(0, ge=0, le=255)
    g: int = Field(0, ge=0, le=255)
    b: int = Field(0, ge=0, le=255)


@router.post("/led/fill")
async def led_fill(req: LedFillReq):
    from app.core import ros_bridge
    ok = ros_bridge.call_set_led("fill", req.r, req.g, req.b)
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


class LedPixelReq(BaseModel):
    pixels: List[int] = Field(..., description="픽셀 인덱스 목록 (0-7)")
    r: int = Field(0, ge=0, le=255)
    g: int = Field(0, ge=0, le=255)
    b: int = Field(0, ge=0, le=255)


@router.post("/led/pixel")
async def led_pixel(req: LedPixelReq):
    from app.core import ros_bridge
    ok = ros_bridge.call_set_led("set_pixel", req.r, req.g, req.b, list(req.pixels))
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


@router.post("/led/clear")
async def led_clear():
    from app.core import ros_bridge
    ok = ros_bridge.call_set_led("clear")
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


class BrightnessReq(BaseModel):
    brightness: int = Field(..., ge=0, le=255)


@router.post("/led/brightness")
async def led_brightness(req: BrightnessReq):
    from app.core import ros_bridge
    ok = ros_bridge.call_set_brightness(req.brightness)
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


# ── 부저 제어 (ROS2 /play_buzzer, /play_melody, /stop_buzzer) ────────

VALID_PRESETS = {"bell", "beep", "alarm", "success", "error"}
VALID_MELODIES = {"fur_elise", "school_bell"}


class BuzzerReq(BaseModel):
    preset: str = "bell"
    count: int | None = None
    freq: int | None = None
    duration: float | None = None


@router.post("/buzzer")
async def play_buzzer(req: BuzzerReq):
    if req.preset not in VALID_PRESETS:
        raise HTTPException(400, f"유효하지 않은 프리셋: {req.preset}")
    from app.core import ros_bridge
    ok = ros_bridge.call_play_buzzer(
        req.preset,
        req.count    if req.count    is not None else 0,
        req.freq     if req.freq     is not None else 0,
        req.duration if req.duration is not None else 0.0,
    )
    return {"success": ok, "output": "", "error": "" if ok else "ROS 브리지 미연결"}


@router.get("/buzzer/status")
async def buzzer_status():
    return {"running": None, "melody": None, "note": "ROS2 노드에서 관리 (pinky_buzzer)"}


class BuzzerMelodyReq(BaseModel):
    melody: str


@router.post("/buzzer/melody/play")
async def play_buzzer_melody(req: BuzzerMelodyReq):
    if req.melody not in VALID_MELODIES:
        raise HTTPException(400, f"유효하지 않은 멜로디: {req.melody}")
    from app.core import ros_bridge
    ok = ros_bridge.call_play_melody(req.melody)
    return {"success": ok, "output": f"melody started: {req.melody}" if ok else "", "error": "" if ok else "ROS 브리지 미연결"}


@router.post("/buzzer/melody/stop")
async def stop_buzzer_melody():
    from app.core import ros_bridge
    ok = ros_bridge.call_stop_buzzer()
    return {"success": ok, "output": "melody stopped" if ok else "", "error": "" if ok else "ROS 브리지 미연결"}


# ── 센서 (ROS2 토픽 — pinky_sensor_adc) ──────────────────────────────

async def _sensor_status_payload() -> dict:
    from app.core import ros_bridge
    us = ros_bridge.get_topic("us_range")
    ir = ros_bridge.get_topic("ir_range")
    return {
        "dist_cm": us.get("distance_cm") if isinstance(us, dict) else None,
        "ir": {
            "left": ir.get("left"),
            "center": ir.get("center"),
            "right": ir.get("right"),
            "obstacle": bool(ir.get("obstacle")),
        } if isinstance(ir, dict) else None,
    }


@router.get("/sensor/ultrasonic")
async def sensor_ultrasonic():
    from app.core import ros_bridge
    us = ros_bridge.get_topic("us_range")
    if us is None:
        raise HTTPException(503, "센서 데이터 미수신 (pinky_sensor_adc 실행 확인)")
    return {"success": True, "output": json.dumps(us), "error": ""}


@router.get("/sensor/battery")
async def sensor_battery():
    from app.core import ros_bridge
    batt = ros_bridge.get_topic("battery")
    return {"success": True, "output": json.dumps({"sensor": "battery", **batt}), "error": ""}


@router.get("/sensor/ir")
async def sensor_ir():
    from app.core import ros_bridge
    ir = ros_bridge.get_topic("ir_range")
    if ir is None:
        raise HTTPException(503, "센서 데이터 미수신 (pinky_sensor_adc 실행 확인)")
    return {"success": True, "output": json.dumps(ir), "error": ""}


@router.websocket("/sensor/ws/status")
async def sensor_status_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(await _sensor_status_payload())
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return


@router.get("/sensor/imu")
async def sensor_imu():
    # pinky_imu_bno055 ROS2 노드 추가 시 토픽으로 전환 예정
    return await _run(f"sudo -n python3 {_SENSOR_SCRIPT} imu", timeout=8)


# ── 모터 직접 제어 (→ /cmd_vel 토픽) ────────────────────────────────

class MotorMoveReq(BaseModel):
    left: int = Field(..., ge=-100, le=100)
    right: int = Field(..., ge=-100, le=100)
    duration: float = Field(0.5, ge=0.05, le=3.0)


@router.post("/motor/move")
async def motor_move(req: MotorMoveReq):
    from app.core import ros_bridge
    linear = (req.left + req.right) / 2.0 / 100.0 * 0.22
    angular = (req.right - req.left) / 2.0 / 100.0 * 2.84
    ros_bridge.publish_cmd_vel(linear, angular)
    await asyncio.sleep(req.duration)
    ros_bridge.publish_cmd_vel(0.0, 0.0)
    return {"success": True, "output": "", "error": ""}


@router.post("/motor/stop")
async def motor_stop():
    from app.core import ros_bridge
    ros_bridge.publish_cmd_vel(0.0, 0.0)
    return {"success": True, "output": "", "error": ""}

