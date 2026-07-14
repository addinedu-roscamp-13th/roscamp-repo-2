"""Persistent ROS map management for the admin UI."""
import asyncio
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from .auth import get_current_admin

router = APIRouter(prefix="/api/robot/map", tags=["maps"])
MAP_DIR = Path(__file__).resolve().parents[2] / "maps"
MAP_DIR.mkdir(parents=True, exist_ok=True)
ROS_SETUP = Path("/opt/ros/jazzy/setup.bash")
PINKY_SETUP = Path("/home/pinky/pinky_pro/install/setup.bash")

class SaveMapRequest(BaseModel):
    name: str = ""

def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9가-힣_-]", "_", value.strip())
    return value[:80] or datetime.now().strftime("map_%Y%m%d_%H%M%S")

def files(name: str):
    clean = safe_name(name)
    return MAP_DIR / f"{clean}.yaml", MAP_DIR / f"{clean}.pgm"

@router.get("/list")
async def list_maps(_=Depends(get_current_admin)):
    items = []
    for yaml_file in MAP_DIR.glob("*.yaml"):
        pgm_file = yaml_file.with_suffix(".pgm")
        if not pgm_file.exists():
            continue
        stat = yaml_file.stat()
        size = yaml_file.stat().st_size + pgm_file.stat().st_size
        items.append({"name": yaml_file.stem, "mtime": stat.st_mtime,
                      "size_kb": round(size / 1024, 1)})
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return {"maps": items}

@router.post("/save")
async def save_map(req: SaveMapRequest, _=Depends(get_current_admin)):
    name = safe_name(req.name)
    prefix = MAP_DIR / name
    command = (f"source {ROS_SETUP} && source {PINKY_SETUP} && "
               f"ros2 run nav2_map_server map_saver_cli -f {prefix} "
               "--ros-args -p save_map_timeout:=10.0 "
               "-p map_subscribe_transient_local:=false")
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash", "-lc", command,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "error": "지도 저장 시간 초과"}
    yaml_file, pgm_file = files(name)
    if proc.returncode != 0 or not yaml_file.exists() or not pgm_file.exists():
        error = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        return {"ok": False, "error": error or "map_saver_cli 실행 실패"}
    return {"ok": True, "name": name}

@router.delete("/{name}")
async def delete_map(name: str, _=Depends(get_current_admin)):
    yaml_file, pgm_file = files(name)
    if not yaml_file.exists() and not pgm_file.exists():
        raise HTTPException(404, "지도를 찾을 수 없습니다")
    yaml_file.unlink(missing_ok=True)
    pgm_file.unlink(missing_ok=True)
    return {"ok": True}

@router.get("/{name}/download")
async def download_map(name: str, _=Depends(get_current_admin)):
    yaml_file, pgm_file = files(name)
    if not yaml_file.exists() or not pgm_file.exists():
        raise HTTPException(404, "지도를 찾을 수 없습니다")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(yaml_file, yaml_file.name)
        archive.write(pgm_file, pgm_file.name)
    headers = {"Content-Disposition": f"attachment; filename={safe_name(name)}.zip"}
    return Response(data.getvalue(), media_type="application/zip", headers=headers)
