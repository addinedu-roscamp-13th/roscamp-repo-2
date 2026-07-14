import asyncio
import logging

logger = logging.getLogger(__name__)


async def camera_push_loop(server_url: str) -> None:
    """로봇팔 카메라 프레임을 중앙 서버로 지속 전송한다 (30fps, 재연결 포함)."""
    from app.hardware.camera_stream import camera as camera_hw

    ws_url = (
        server_url.rstrip("/")
        .replace("http://", "ws://")
        .replace("https://", "wss://")
    )
    ws_url = f"{ws_url}/api/arm/ws/robot-camera"

    while True:
        try:
            import websockets

            logger.info("[camera-push] 서버 연결 중: %s", ws_url)
            async with websockets.connect(ws_url) as ws:
                logger.info("[camera-push] 연결됨 — 프레임 전송 시작")
                if not camera_hw.is_running():
                    camera_hw.start()
                while True:
                    jpeg = camera_hw.get_jpeg()
                    if jpeg:
                        await ws.send(jpeg)
                    await asyncio.sleep(1 / 30)
        except Exception as e:
            logger.warning("[camera-push] 연결 끊김: %s — 2초 후 재시도", e)
            await asyncio.sleep(2)
