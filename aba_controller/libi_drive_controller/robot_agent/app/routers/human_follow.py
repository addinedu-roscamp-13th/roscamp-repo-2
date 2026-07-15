from __future__ import annotations

import asyncio
import json
import time
import urllib.request
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.hardware.camera_stream import camera
from app.routers.driving import _motor_send

router = APIRouter()


class HumanFollowConfig(BaseModel):
    ai_server_url: str = "http://192.168.0.9:9001"
    confidence: float = Field(0.35, ge=0.1, le=0.95)
    target_area: float = Field(0.18, ge=0.03, le=0.6)
    forward_speed: int = Field(30, ge=12, le=60)
    turn_speed: int = Field(30, ge=12, le=60)
    deadband: float = Field(0.08, ge=0.01, le=0.3)
    invert_steering: bool = True
    loop_hz: float = Field(5.0, ge=1.0, le=12.0)
    lost_timeout_s: float = Field(0.8, ge=0.2, le=5.0)
    labels: list[str] = Field(default_factory=lambda: ["human", "person"])


_task: asyncio.Task | None = None
_token: str | None = None
_token_ts = 0.0
_state: dict[str, Any] = {"running": False, "phase": "idle", "message": "", "telemetry": {}, "last_error": None}


def _login(ai_server_url: str) -> str:
    global _token, _token_ts
    now = time.monotonic()
    if _token and now - _token_ts < 1800:
        return _token
    req = urllib.request.Request(
        ai_server_url.rstrip("/") + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin1234"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _token = data["access_token"]
    _token_ts = now
    return _token


def _analyze_jpeg(ai_server_url: str, jpeg: bytes) -> dict[str, Any] | None:
    boundary = "----humanfollow" + uuid.uuid4().hex
    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="file"; filename="frame.jpg"\r\n')
    parts.append(b"Content-Type: image/jpeg\r\n\r\n")
    parts.append(jpeg)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    token = _login(ai_server_url)
    req = urllib.request.Request(
        ai_server_url.rstrip("/") + "/api/arm/pinky-detect/analyze",
        data=b"".join(parts),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _best_target(detections: list[dict[str, Any]], labels: set[str]) -> dict[str, Any] | None:
    candidates = [d for d in detections if str(d.get("label", "")).lower() in labels]
    return max(candidates, key=lambda d: float(d.get("confidence") or 0.0), default=None)


def _command_for_detection(det: dict[str, Any], cfg: HumanFollowConfig) -> tuple[int, int, dict[str, Any]]:
    x1, y1, x2, y2 = [float(v) for v in det.get("box", [0, 0, 0, 0])]
    cx = (x1 + x2) / 2.0
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    ex = (cx - 320.0) / 320.0
    area = (w * h) / (640.0 * 480.0)
    centered = abs(ex) < cfg.deadband
    too_close = area >= cfg.target_area
    steer_dir = -1.0 if cfg.invert_steering else 1.0
    if not centered:
        turn = cfg.turn_speed if steer_dir * ex > 0 else -cfg.turn_speed
        return turn, -turn, {"phase": "align", "ex": ex, "area": area}
    if not too_close:
        return cfg.forward_speed, cfg.forward_speed, {"phase": "forward", "ex": ex, "area": area}
    return 0, 0, {"phase": "hold", "ex": ex, "area": area}


async def _follow_loop(cfg: HumanFollowConfig) -> None:
    labels = {label.lower() for label in cfg.labels}
    period = 1.0 / cfg.loop_hz
    last_seen = 0.0
    try:
        if not camera.is_running():
            camera.start()
            await asyncio.sleep(0.5)
        _state.update(running=True, phase="starting", message="사람 추종 루프 시작", last_error=None, telemetry={})
        while True:
            jpeg = camera.get_jpeg()
            if not jpeg:
                await _motor_send(0, 0)
                _state.update(phase="no_frame", message="카메라 프레임 대기", telemetry={})
                await asyncio.sleep(period)
                continue
            try:
                result = await asyncio.to_thread(_analyze_jpeg, cfg.ai_server_url, jpeg)
                detections = (result or {}).get("detections") or []
                target = _best_target(detections, labels)
                if target is None:
                    if time.monotonic() - last_seen > cfg.lost_timeout_s:
                        await _motor_send(0, 0)
                    _state.update(phase="search", message="사람 탐색 중", telemetry={"detections": len(detections)}, last_error=None)
                    await asyncio.sleep(period)
                    continue
                last_seen = time.monotonic()
                left, right, tele = _command_for_detection(target, cfg)
                await _motor_send(left, right)
                phase = str(tele.pop("phase"))
                _state.update(
                    phase=phase,
                    message="중앙 정렬" if phase == "align" else "접근" if phase == "forward" else "거리 유지",
                    telemetry={"left": left, "right": right, "label": target.get("label"), "confidence": target.get("confidence"), **{k: round(float(v), 3) for k, v in tele.items()}},
                    last_error=None,
                )
            except Exception as exc:
                await _motor_send(0, 0)
                _state.update(phase="error", message="추종 오류", last_error=str(exc), telemetry={})
                await asyncio.sleep(max(0.5, period))
                continue
            await asyncio.sleep(period)
    except asyncio.CancelledError:
        await _motor_send(0, 0)
        _state.update(running=False, phase="idle", message="정지됨")
        raise


@router.post("/human-follow/start")
async def start(cfg: HumanFollowConfig):
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    await _motor_send(0, 0)
    _state.update(running=True, phase="starting", message="시작 중", telemetry={}, last_error=None)
    _task = asyncio.create_task(_follow_loop(cfg))
    return {"success": True, "message": "사람 추종 시작", "config": cfg.model_dump()}


@router.post("/human-follow/stop")
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
    return {"success": True, "message": "사람 추종 정지"}


@router.get("/human-follow/status")
async def status():
    task = None if _task is None else {"done": _task.done(), "cancelled": _task.cancelled()}
    return {"state": _state, "task": task}


@router.websocket("/human-follow/ws/status")
async def status_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json({"state": _state})
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
