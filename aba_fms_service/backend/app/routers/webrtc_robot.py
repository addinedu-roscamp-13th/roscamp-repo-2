"""Robot-side WebRTC camera streaming (aiortc).

브라우저가 로봇 :9001 로 직접 offer 를 보내면 로봇이 answer 를 돌려주고
카메라 프레임을 H.264(SW 인코딩)로 P2P 전송한다. 뷰어 전용 — 감지/제어와 무관.

aiortc 는 로봇에만 설치된다. 중앙서버 등 미설치 환경에서 import 로 앱이 죽지 않도록
모듈 로드시 import 를 감싸고, 미설치면 /offer 가 503 을 반환한다.
"""
import asyncio
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.hardware.camera_stream import camera

try:
    import av
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import VideoStreamTrack

    _AIORTC_ERROR: str | None = None

    class _CameraTrack(VideoStreamTrack):
        """CameraManager 의 최신 원시 BGR 프레임을 WebRTC 비디오 트랙으로 노출."""

        _BLACK = np.zeros((240, 320, 3), dtype=np.uint8)

        async def recv(self) -> "av.VideoFrame":
            pts, time_base = await self.next_timestamp()  # 프레임레이트 페이싱 포함
            raw = camera.get_raw()
            if raw is None:
                raw = self._BLACK
            frame = av.VideoFrame.from_ndarray(raw, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base
            return frame

except Exception as exc:  # aiortc 미설치 등
    _AIORTC_ERROR = str(exc)
    _CameraTrack = None  # type: ignore[assignment]


router = APIRouter(prefix="/api/robot/webrtc", tags=["webrtc-robot"])

_pcs: set[Any] = set()


class Offer(BaseModel):
    sdp: str
    type: str


@router.post("/offer")
async def offer(params: Offer) -> dict[str, str]:
    if _CameraTrack is None:
        raise HTTPException(503, f"WebRTC(aiortc) 미설치: {_AIORTC_ERROR}")

    if not camera.is_running():
        camera.start()
        await asyncio.sleep(0.3)

    pc = RTCPeerConnection()
    _pcs.add(pc)

    @pc.on("connectionstatechange")
    async def _on_state() -> None:
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            _pcs.discard(pc)

    pc.addTrack(_CameraTrack())
    await pc.setRemoteDescription(RTCSessionDescription(sdp=params.sdp, type=params.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


@router.get("/status")
async def status() -> dict[str, Any]:
    return {
        "available": _CameraTrack is not None,
        "error": _AIORTC_ERROR,
        "peers": len(_pcs),
        "camera_running": camera.is_running(),
    }


async def shutdown() -> None:
    for pc in list(_pcs):
        await pc.close()
    _pcs.clear()
