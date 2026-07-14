"""Pinky Pro object-detection API."""
import asyncio
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_admin
from app.hardware.camera_stream import camera
from app.hardware.pinky_greeting_monitor import pinky_greeting_monitor
from app.hardware.pinky_yolo import pinky_yolo
from app.models import PinkyGreetingSettings
from app.security import create_access_token

router = APIRouter(prefix="/api/arm/pinky-detect", tags=["pinky-yolo"])


class ConfidenceRequest(BaseModel):
    confidence: float = Field(ge=0.1, le=0.95)


class GreetingSettingsRequest(BaseModel):
    enabled: bool | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=60)
    text: str | None = None
    font_name: str | None = None
    font_size: int | None = Field(default=None, ge=8, le=96)
    color: str | None = None
    bg_color: str | None = None
    align: str | None = None


def _settings_dict(settings: PinkyGreetingSettings) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "duration_seconds": settings.duration_seconds,
        "text": settings.text,
        "font_name": settings.font_name,
        "font_size": settings.font_size,
        "color": settings.color,
        "bg_color": settings.bg_color,
        "align": settings.align,
        "monitor": pinky_greeting_monitor.status(),
    }


async def _get_or_create_settings(db: AsyncSession) -> PinkyGreetingSettings:
    settings = (await db.execute(
        select(PinkyGreetingSettings).where(PinkyGreetingSettings.id == 1)
    )).scalar_one_or_none()
    if settings is None:
        settings = PinkyGreetingSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.get("/status")
async def status() -> dict:
    return pinky_yolo.status()


@router.get("/greeting")
async def get_greeting_settings(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
) -> dict:
    settings = await _get_or_create_settings(db)
    return _settings_dict(settings)


@router.put("/greeting")
async def update_greeting_settings(
    request: GreetingSettingsRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
) -> dict:
    settings = await _get_or_create_settings(db)
    data = request.model_dump(exclude_unset=True)
    for field, value in data.items():
        if value is None:
            continue
        if field == "align" and value not in {"left", "center", "right"}:
            raise HTTPException(400, "align은 left, center, right 중 하나여야 합니다")
        setattr(settings, field, value)
    await db.commit()
    await db.refresh(settings)
    return _settings_dict(settings)


@router.post("/confidence")
async def set_confidence(request: ConfidenceRequest) -> dict:
    pinky_yolo.set_confidence(request.confidence)
    return {"success": True, "confidence": pinky_yolo.confidence}


@router.post("/analyze")
async def analyze_frame(
    file: UploadFile = File(...),
    _=Depends(get_current_admin),
) -> dict:
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다")
    payload = await file.read()
    result = pinky_yolo.detect_jpeg(payload)
    if result is None:
        raise HTTPException(400, "이미지 프레임을 해석하지 못했습니다")
    return result


def fetch_remote_frame(ip: str) -> bytes | None:
    token = create_access_token("admin")
    urls = [
        f"http://{ip}:9001/api/robot/camera/snapshot?token={token}",
        f"http://{ip}:9001/api/robot/camera/snapshot?token={token}",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as response:
                return response.read()
        except Exception:
            continue
    return None


@router.websocket("/ws")
async def detection_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    robot_ip = websocket.query_params.get("robot_ip")
    model_name = websocket.query_params.get("model", "pinky_best")
    if not robot_ip:
        if not camera.is_running():
            camera.start()
    try:
        while True:
            if robot_ip:
                # 인식 WS가 열려 있는 동안만 인사 모니터가 이 로봇을 폴링하도록 활성 표시.
                pinky_greeting_monitor.mark_active(robot_ip, ttl=8.0)
                jpeg = await asyncio.to_thread(fetch_remote_frame, robot_ip)
                if jpeg:
                    payload = await asyncio.to_thread(pinky_yolo.detect_jpeg, jpeg, model_name)
                else:
                    payload = {"type": "error", "message": f"로봇({robot_ip}) 카메라에서 프레임을 가져오지 못했습니다."}
            else:
                payload = await asyncio.to_thread(pinky_yolo.detect_latest, model_name)

            if payload is not None:
                await websocket.send_json(payload)
                if payload.get("type") == "error":
                    await asyncio.sleep(1)
            await asyncio.sleep(0.03)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
