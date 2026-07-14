import asyncio
import time

import cv2
import numpy as np

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

try:
    from app.deps import get_current_admin
except Exception:
    def get_current_admin():
        return None
from app.hardware.camera_stream import camera
try:
    from app.security import decode_token
except Exception:
    def decode_token(token: str):
        return {"sub": "robot"}

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame
except Exception:
    RTCPeerConnection = None
    RTCSessionDescription = None
    VideoStreamTrack = object
    VideoFrame = None

router = APIRouter(prefix="/api/robot", tags=["camera"])
_pcs: set = set()


class CameraVideoTrack(VideoStreamTrack):
    """Low-latency WebRTC track backed by the latest camera JPEG frame."""

    def __init__(self, fps: int = 12, width: int = 320, quality: int = 55) -> None:
        super().__init__()
        self.delay = 1.0 / max(1, min(20, fps))
        self.width = width
        self.quality = quality
        self._last_frame_id = -1
        self._last_ts = 0.0

    async def recv(self):
        if VideoFrame is None:
            raise RuntimeError("aiortc/av is not available")
        pts, time_base = await self.next_timestamp()
        while True:
            frame_id, jpeg = camera.get_frame()
            now = time.time()
            if jpeg and (frame_id != self._last_frame_id or now - self._last_ts >= self.delay):
                self._last_frame_id = frame_id
                self._last_ts = now
                data = _transcode_jpeg(jpeg, self.width, self.quality) or jpeg
                arr = np.frombuffer(data, dtype=np.uint8)
                frame_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame_bgr is not None:
                    frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
                    frame.pts = pts
                    frame.time_base = time_base
                    return frame
            await asyncio.sleep(self.delay / 2)


def _validate_stream_token(token: str) -> bool:
    """MJPEG 스트림 엔드포인트 전용 토큰 검증 (쿼리 파라미터)."""
    try:
        decode_token(token)
        return True
    except ValueError:
        return False


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
                headers = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(out)}\r\n".encode()
                    + b"Cache-Control: no-cache\r\n\r\n"
                )
                yield headers + out + b"\r\n"
            await asyncio.sleep(delay)
    except (asyncio.CancelledError, GeneratorExit):
        pass


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/camera/stream")
async def camera_stream(request: Request, token: str = Query(""), w: int = Query(0, ge=0, le=1280), q: int = Query(80, ge=20, le=95), fps: int = Query(15, ge=1, le=30)):
    """MJPEG 스트리밍 — <img> 태그는 헤더를 보낼 수 없어 쿼리 파라미터로 인증."""
    if token and not _validate_stream_token(token):
        raise HTTPException(401, "인증 실패")
    if not camera.is_running():
        camera.start()
    # 첫 프레임이 준비될 때까지 최대 15초 대기
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


@router.get("/camera/snapshot")
async def camera_snapshot(token: str = Query(...)):
    """최신 JPEG 한 장 반환. 프록시/MJPEG 렌더링이 불안정한 화면 갱신용."""
    if not _validate_stream_token(token):
        raise HTTPException(401, "인증 실패")
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


@router.post("/camera/start")
async def camera_start(_=Depends(get_current_admin)):
    if camera.is_running():
        return {"success": True, "message": "이미 실행 중"}
    camera.start()
    await asyncio.sleep(0.4)
    if camera.error:
        return {"success": False, "message": camera.error}
    return {"success": True, "message": "카메라 시작"}


@router.post("/camera/stop")
async def camera_stop(_=Depends(get_current_admin)):
    camera.stop()
    return {"success": True, "message": "카메라 중지"}


@router.get("/camera/status")
async def camera_status(_=Depends(get_current_admin)):
    return {
        "running": camera.is_running(),
        "error": camera.error,
        **camera.get_debug_status(),
    }


@router.get("/camera/analysis")
async def camera_analysis(_=Depends(get_current_admin)):
    if not camera.is_running():
        camera.start()
    for _ in range(60):
        analysis = camera.get_analysis()
        if analysis is not None:
            return analysis
        if camera.error:
            raise HTTPException(503, camera.error)
        await asyncio.sleep(0.05)
    raise HTTPException(503, "카메라 분석 데이터가 아직 준비되지 않았습니다")


@router.post("/camera/webrtc/offer")
async def camera_webrtc_offer(
    request: Request,
    w: int = Query(320, ge=0, le=1280),
    q: int = Query(50, ge=20, le=95),
    fps: int = Query(12, ge=1, le=30),
):
    """WebRTC 저지연 스트림 — 브라우저 offer(SDP)를 받아 answer(SDP)를 돌려준다.

    프레임은 CameraVideoTrack 이 camera.get_frame() 의 최신 JPEG 를 실시간
    트랜스코딩(w/q/fps)해서 공급한다. aiortc 미설치 환경은 503 으로 폴백.
    """
    if RTCPeerConnection is None:
        raise HTTPException(503, "aiortc 가 설치되지 않아 WebRTC 를 사용할 수 없습니다")
    body = await request.json()
    sdp, sdp_type = body.get("sdp"), body.get("type")
    if not sdp or not sdp_type:
        raise HTTPException(400, "offer 의 sdp/type 이 누락되었습니다")
    if not camera.is_running():
        camera.start()

    pc = RTCPeerConnection()
    _pcs.add(pc)

    @pc.on("connectionstatechange")
    async def _on_connection_state() -> None:
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            _pcs.discard(pc)

    pc.addTrack(CameraVideoTrack(fps=fps, width=w, quality=q))
    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
