import asyncio

import cv2
import numpy as np

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.hardware.camera_stream import camera

router = APIRouter()


def _transcode_jpeg(jpeg: bytes, width: int, quality: int) -> bytes | None:
    if width <= 0 and quality >= 80:
        return jpeg
    arr = np.frombuffer(jpeg, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return jpeg
    if width > 0 and frame.shape[1] > width:
        scale = width / frame.shape[1]
        height = max(1, int(frame.shape[0] * scale))
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else jpeg


async def _mjpeg_generator(request: Request, width: int, quality: int, fps: int):
    last_frame_id = -1
    delay = 1.0 / max(1, fps)
    try:
        while not await request.is_disconnected():
            frame_id, jpeg = camera.get_frame()
            if jpeg and frame_id != last_frame_id:
                last_frame_id = frame_id
                out = _transcode_jpeg(jpeg, width, quality) or jpeg
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(out)}\r\n".encode()
                    + b"Cache-Control: no-cache\r\n\r\n"
                )
                yield header + out + b"\r\n"
            await asyncio.sleep(delay)
    except (asyncio.CancelledError, GeneratorExit):
        pass


@router.get("/stream")
async def camera_stream(request: Request, w: int = 0, q: int = 80, fps: int = 15):
    if not camera.is_running():
        camera.start()
    for _ in range(300):
        if camera.get_jpeg() is not None:
            break
        await asyncio.sleep(0.05)
    else:
        raise HTTPException(503, "카메라 프레임을 받지 못했습니다")
    return StreamingResponse(
        _mjpeg_generator(request, w, q, fps),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/snapshot")
async def camera_snapshot():
    if not camera.is_running():
        camera.start()
    for _ in range(60):
        jpeg = camera.get_jpeg()
        if jpeg is not None:
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                },
            )
        await asyncio.sleep(0.05)
    raise HTTPException(503, "카메라 프레임을 받지 못했습니다")


@router.post("/start")
async def camera_start():
    if camera.is_running():
        return {"success": True, "message": "이미 실행 중"}
    camera.start()
    await asyncio.sleep(0.4)
    if camera.error:
        return {"success": False, "message": camera.error}
    return {"success": True, "message": "카메라 시작"}


@router.post("/stop")
async def camera_stop():
    camera.stop()
    return {"success": True, "message": "카메라 중지"}


@router.get("/status")
async def camera_status():
    return {
        "running": camera.is_running(),
        "error": camera.error,
        **camera.get_debug_status(),
    }


@router.get("/analysis")
async def camera_analysis():
    analysis = camera.get_analysis()
    if analysis is not None:
        return analysis
    raise HTTPException(404, "분석 데이터 없음")
