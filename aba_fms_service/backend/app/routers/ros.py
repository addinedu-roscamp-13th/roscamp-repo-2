"""ROS 2 process management endpoints required by the admin UI."""
import asyncio
import os
import signal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from .auth import get_current_admin

router = APIRouter(prefix="/api/robot", tags=["ros"])
ROS_SETUP = Path("/opt/ros/jazzy/setup.bash")
PINKY_SETUP = Path("/home/pinky/pinky_pro/install/setup.bash")
STATE_DIR = Path("/tmp/labi_ros")
STATE_DIR.mkdir(parents=True, exist_ok=True)
LAUNCHES = {"slam": "web_slam.launch.xml", "nav2": "web_nav2.launch.xml"}

def pid_path(name):
    return STATE_DIR / f"{name}.pid"

def get_pid(name):
    try:
        pid = int(pid_path(name).read_text())
        os.kill(pid, 0)
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\\0", b" ").decode()
        if LAUNCHES[name] not in cmd:
            raise ProcessLookupError
        return pid
    except (OSError, ValueError, ProcessLookupError):
        pid_path(name).unlink(missing_ok=True)
        return None

def state(name):
    pid = get_pid(name)
    return {"running": pid is not None, "pid": pid, "launch_file": LAUNCHES[name]}

@router.get("/status")
async def status(_=Depends(get_current_admin)):
    return {"ros_available": ROS_SETUP.exists() and PINKY_SETUP.exists(),
            "processes": {name: state(name) for name in LAUNCHES}}

@router.post("/process/{name}/start")
async def start(name: str, _=Depends(get_current_admin)):
    if name not in LAUNCHES:
        raise HTTPException(404, f"지원하지 않는 ROS 프로세스: {name}")
    if not ROS_SETUP.exists() or not PINKY_SETUP.exists():
        raise HTTPException(503, "ROS 2 환경을 찾을 수 없습니다")
    if get_pid(name):
        return {"ok": True, "already_running": True, **state(name)}
    other = "nav2" if name == "slam" else "slam"
    if get_pid(other):
        raise HTTPException(409, f"{other} 프로세스를 먼저 중지하세요")
    bringup = "ros2 launch pinky_bringup bringup_robot.launch.xml"
    navigation = f"ros2 launch pinky_navigation {LAUNCHES[name]}"
    command = f"source {ROS_SETUP} && source {PINKY_SETUP} && {{ {bringup} & {navigation} & wait; }}"
    log = (STATE_DIR / f"{name}.log").open("ab", buffering=0)
    try:
        proc = await asyncio.create_subprocess_exec("/bin/bash", "-lc", command,
            stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()
    pid_path(name).write_text(str(proc.pid))
    await asyncio.sleep(0.5)
    if proc.returncode is not None:
        pid_path(name).unlink(missing_ok=True)
        detail = (STATE_DIR / f"{name}.log").read_text(errors="replace")[-2000:]
        return {"ok": False, "running": False, "error": detail or "ROS 시작 실패"}
    return {"ok": True, **state(name)}

@router.post("/process/{name}/stop")
async def stop(name: str, _=Depends(get_current_admin)):
    if name not in LAUNCHES:
        raise HTTPException(404, f"지원하지 않는 ROS 프로세스: {name}")
    pid = get_pid(name)
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
            await asyncio.sleep(1)
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pid_path(name).unlink(missing_ok=True)
    return {"ok": True, "running": False}
