import asyncio
import json
import os
import re
import signal
import tempfile
import time
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.database import get_robot_db
from app.models import LcdImage

from .auth import get_current_admin

router = APIRouter(prefix="/api/robot", tags=["robot"])

_HW = Path(__file__).parent.parent / "hardware"
_LCD_SCRIPT = _HW / "lcd_ctrl.py"
_LED_SCRIPT = _HW / "led_ctrl.py"
_SENSOR_SCRIPT = _HW / "sensor_ctrl.py"
_BUZZER_SCRIPT = _HW / "buzzer_ctrl.py"
_MOTOR_SCRIPT = _HW / "motor_ctrl.py"

_IMAGES_DIR = _HW / "uploads" / "images"
_FONTS_DIR = _HW / "uploads" / "fonts"

_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
_FONTS_DIR.mkdir(parents=True, exist_ok=True)

_buzzer_proc: asyncio.subprocess.Process | None = None
_buzzer_melody: str | None = None


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


async def _sync_lcd_image_db(db: AsyncSession) -> None:
    exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    existing = set((await db.execute(select(LcdImage.filename))).scalars().all())
    changed = False
    for file in sorted(_IMAGES_DIR.iterdir()):
        if not file.is_file() or file.suffix.lower() not in exts or file.name in existing:
            continue
        db.add(
            LcdImage(
                filename=file.name,
                original_name=file.name,
                content_type=None,
                size_bytes=file.stat().st_size,
            )
        )
        changed = True
    if changed:
        await db.commit()


def _image_payload(image: LcdImage) -> dict:
    return {
        "id": image.id,
        "filename": image.filename,
        "original_name": image.original_name,
        "content_type": image.content_type,
        "size_bytes": image.size_bytes,
        "created_at": image.created_at.isoformat() if image.created_at else None,
    }


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


# ── LCD: 표정 ─────────────────────────────────────────────────────────────────

VALID_EMOTIONS = {"angry", "basic", "bored", "hello", "interest", "fun", "happy", "sad"}


class EmotionReq(BaseModel):
    emotion: str


@router.post("/lcd/emotion")
async def set_emotion(req: EmotionReq, _=Depends(get_current_admin)):
    if req.emotion not in VALID_EMOTIONS:
        raise HTTPException(400, f"유효하지 않은 표정: {req.emotion}")
    return await _run(f"sudo -n python3 {_LCD_SCRIPT} emotion {req.emotion}", timeout=10)


# ── LCD: 정지 ─────────────────────────────────────────────────────────────────

@router.post("/lcd/stop")
async def lcd_stop(_=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_LCD_SCRIPT} stop", timeout=8)


# ── LCD: 이미지 업로드 & 표시 ─────────────────────────────────────────────────

@router.post("/lcd/image")
async def lcd_image(
    file: UploadFile = File(...),
    _=Depends(get_current_admin),
    db: AsyncSession = Depends(get_robot_db),
):
    ct = file.content_type or ""
    if not ct.startswith("image/"):
        raise HTTPException(400, "이미지 파일만 업로드 가능합니다")
    dest = _unique_upload_path(file.filename or "image.png")
    content = await file.read()
    dest.write_bytes(content)

    image = LcdImage(
        filename=dest.name,
        original_name=file.filename or dest.name,
        content_type=ct,
        size_bytes=len(content),
    )
    db.add(image)
    await db.commit()
    await db.refresh(image)

    res = await _run(f"sudo -n python3 {_LCD_SCRIPT} image {dest}", timeout=15)
    return {**res, "image": _image_payload(image)}


class ImageSelectReq(BaseModel):
    filename: str


@router.post("/lcd/image/select")
async def lcd_image_select(
    req: ImageSelectReq,
    _=Depends(get_current_admin),
    db: AsyncSession = Depends(get_robot_db),
):
    safe = _safe_name(req.filename)
    image = (await db.execute(select(LcdImage).where(LcdImage.filename == safe))).scalar_one_or_none()
    dest = _IMAGES_DIR / safe
    if image is None and dest.exists():
        image = LcdImage(
            filename=dest.name,
            original_name=dest.name,
            content_type=None,
            size_bytes=dest.stat().st_size,
        )
        db.add(image)
        await db.commit()
    if not dest.exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return await _run(f"sudo -n python3 {_LCD_SCRIPT} image {dest}", timeout=15)


@router.get("/lcd/images")
async def list_images(
    _=Depends(get_current_admin),
    db: AsyncSession = Depends(get_robot_db),
):
    await _sync_lcd_image_db(db)
    images = (
        await db.execute(select(LcdImage).order_by(LcdImage.created_at.desc(), LcdImage.id.desc()))
    ).scalars().all()
    return {"images": [_image_payload(image) for image in images]}


@router.delete("/lcd/images/{name}")
async def delete_image(
    name: str,
    _=Depends(get_current_admin),
    db: AsyncSession = Depends(get_robot_db),
):
    safe = _safe_name(name)
    image = (await db.execute(select(LcdImage).where(LcdImage.filename == safe))).scalar_one_or_none()
    if image is not None:
        await db.delete(image)
        await db.commit()
    (_IMAGES_DIR / safe).unlink(missing_ok=True)
    return {"success": True}


# ── LCD: 텍스트 표시 ──────────────────────────────────────────────────────────

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
async def lcd_text(req: LcdTextReq, _=Depends(get_current_admin)):
    font_path = ""
    if req.font_name != "default":
        fp = _FONTS_DIR / _safe_name(req.font_name)
        if fp.exists():
            font_path = str(fp)

    cfg = {
        "text": req.text,
        "font_path": font_path,
        "font_size": req.font_size,
        "color": req.color,
        "bg_color": req.bg_color,
        "align": req.align,
        "scroll": req.scroll,
        "scroll_speed": req.scroll_speed,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="lcd_text_",
        delete=False, encoding="utf-8"
    ) as f:
        json.dump(cfg, f, ensure_ascii=False)
        tmp_path = f.name
    try:
        return await _run(f"sudo -n python3 {_LCD_SCRIPT} text {tmp_path}", timeout=15)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── LCD: 폰트 관리 ────────────────────────────────────────────────────────────

@router.post("/lcd/font")
async def lcd_font(
    file: UploadFile = File(...),
    _=Depends(get_current_admin),
):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in {"ttf", "otf"}:
        raise HTTPException(400, "TTF 또는 OTF 파일만 업로드 가능합니다")
    safe = _safe_name(file.filename or f"font.{ext}")
    dest = _FONTS_DIR / safe
    content = await file.read()
    dest.write_bytes(content)
    return {"success": True, "filename": safe}


@router.get("/lcd/fonts")
async def list_fonts(_=Depends(get_current_admin)):
    exts = {".ttf", ".otf"}
    files = sorted(f.name for f in _FONTS_DIR.iterdir() if f.suffix.lower() in exts)
    return {"fonts": files}


@router.delete("/lcd/fonts/{name}")
async def delete_font(name: str, _=Depends(get_current_admin)):
    (_FONTS_DIR / _safe_name(name)).unlink(missing_ok=True)
    return {"success": True}


# ── LED ───────────────────────────────────────────────────────────────────────

class LedFillReq(BaseModel):
    r: int = Field(0, ge=0, le=255)
    g: int = Field(0, ge=0, le=255)
    b: int = Field(0, ge=0, le=255)


@router.post("/led/fill")
async def led_fill(req: LedFillReq, _=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_LED_SCRIPT} fill {req.r} {req.g} {req.b}")


class LedPixelReq(BaseModel):
    pixels: List[int] = Field(..., description="픽셀 인덱스 목록 (0-7)")
    r: int = Field(0, ge=0, le=255)
    g: int = Field(0, ge=0, le=255)
    b: int = Field(0, ge=0, le=255)


@router.post("/led/pixel")
async def led_pixel(req: LedPixelReq, _=Depends(get_current_admin)):
    indices = ",".join(str(i) for i in req.pixels)
    return await _run(f"sudo -n python3 {_LED_SCRIPT} pixel {indices} {req.r} {req.g} {req.b}")


@router.post("/led/clear")
async def led_clear(_=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_LED_SCRIPT} clear")


class BrightnessReq(BaseModel):
    brightness: int = Field(..., ge=0, le=255)


@router.post("/led/brightness")
async def led_brightness(req: BrightnessReq, _=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_LED_SCRIPT} brightness {req.brightness}")


# ── 부저 (Buzzer) ─────────────────────────────────────────────────────────────

SOUND_PRESETS = {
    "bell":    (1, 1500, 0.2),
    "beep":    (1, 1000, 0.15),
    "alarm":   (3, 2000, 0.2),
    "success": (2, 1800, 0.15),
    "error":   (3,  800, 0.3),
}


class BuzzerReq(BaseModel):
    preset: str = "bell"
    count: int | None = None
    freq: int | None = None
    duration: float | None = None


@router.post("/buzzer")
async def play_buzzer(req: BuzzerReq, _=Depends(get_current_admin)):
    if req.preset not in SOUND_PRESETS:
        raise HTTPException(400, f"유효하지 않은 프리셋: {req.preset}")
    cnt0, freq0, dur0 = SOUND_PRESETS[req.preset]
    cnt = req.count if req.count is not None else cnt0
    freq = req.freq if req.freq is not None else freq0
    dur = req.duration if req.duration is not None else dur0
    cmd = f"sudo -n python3 {_BUZZER_SCRIPT} beep {cnt} {freq} {dur}"
    return await _run(cmd, timeout=int(cnt * dur * 2 + 5))


VALID_MELODIES = {"fur_elise", "school_bell"}


class BuzzerMelodyReq(BaseModel):
    melody: str


async def _cleanup_buzzer_proc() -> None:
    global _buzzer_proc, _buzzer_melody
    if _buzzer_proc and _buzzer_proc.returncode is not None:
        _buzzer_proc = None
        _buzzer_melody = None


@router.get("/buzzer/status")
async def buzzer_status(_=Depends(get_current_admin)):
    await _cleanup_buzzer_proc()
    return {"running": _buzzer_proc is not None, "melody": _buzzer_melody}


@router.post("/buzzer/melody/play")
async def play_buzzer_melody(req: BuzzerMelodyReq, _=Depends(get_current_admin)):
    global _buzzer_proc, _buzzer_melody
    if req.melody not in VALID_MELODIES:
        raise HTTPException(400, f"유효하지 않은 멜로디: {req.melody}")

    await stop_buzzer_melody()
    _buzzer_proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "python3", str(_BUZZER_SCRIPT), "melody", req.melody,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    _buzzer_melody = req.melody
    return {"success": True, "output": f"melody started: {req.melody}", "error": ""}


@router.post("/buzzer/melody/stop")
async def stop_buzzer_melody(_=Depends(get_current_admin)):
    global _buzzer_proc, _buzzer_melody
    if _buzzer_proc is None:
        _buzzer_melody = None
        return {"success": True, "output": "no melody running", "error": ""}

    proc = _buzzer_proc
    try:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.communicate()
        return {"success": True, "output": "melody stopped", "error": ""}
    finally:
        _buzzer_proc = None
        _buzzer_melody = None


# ── 센서 ──────────────────────────────────────────────────────────────────────

@router.get("/sensor/ultrasonic")
async def sensor_ultrasonic(_=Depends(get_current_admin)):
    # 상주 센서 데몬 캐시에서 즉시 반환(3.2초 → ~0.1초). 미준비 시 직접 읽기로 폴백.
    from .drive import cached_ultrasonic_result
    return await cached_ultrasonic_result()


@router.get("/sensor/battery")
async def sensor_battery(_=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_SENSOR_SCRIPT} battery", timeout=8)


@router.get("/sensor/ir")
async def sensor_ir(_=Depends(get_current_admin)):
    from .drive import cached_ir_result
    return await cached_ir_result()


def _parse_sensor_output(res: dict | None) -> dict | None:
    try:
        return json.loads(res.get("output") or "null") if res else None
    except Exception:
        return None


async def _sensor_status_payload() -> dict:
    from .drive import cached_ir_result, cached_ultrasonic_result
    ultra, ir = await asyncio.gather(cached_ultrasonic_result(), cached_ir_result())
    ud = _parse_sensor_output(ultra)
    idata = _parse_sensor_output(ir)
    return {
        "dist_cm": ud.get("distance_cm") if isinstance(ud, dict) else None,
        "ir": {
            "left": idata.get("left"),
            "center": idata.get("center"),
            "right": idata.get("right"),
            "obstacle": bool(idata.get("obstacle")),
        } if isinstance(idata, dict) else None,
    }


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
async def sensor_imu(_=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_SENSOR_SCRIPT} imu", timeout=8)


# ── 모터 ──────────────────────────────────────────────────────────────────────

class MotorMoveReq(BaseModel):
    left: int = Field(..., ge=-100, le=100)
    right: int = Field(..., ge=-100, le=100)
    duration: float = Field(0.5, ge=0.05, le=3.0)


@router.post("/motor/move")
async def motor_move(req: MotorMoveReq, _=Depends(get_current_admin)):
    return await _run(
        f"sudo -n python3 {_MOTOR_SCRIPT} move {req.left} {req.right} {req.duration}",
        timeout=int(req.duration + 8),
    )


@router.post("/motor/stop")
async def motor_stop(_=Depends(get_current_admin)):
    return await _run(f"sudo -n python3 {_MOTOR_SCRIPT} stop", timeout=8)
