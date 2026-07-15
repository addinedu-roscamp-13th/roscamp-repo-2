"""Background Pinky detection monitor for fleet greeting LCD."""
import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.database import AdminSessionLocal as SessionLocal
from app.models import PinkyGreetingSettings, Robot
from app.security import create_access_token
from app.hardware.pinky_yolo import pinky_yolo


@dataclass
class RobotLcdState:
    busy_until: float = 0.0
    last_error: str | None = None
    last_detection_at: float | None = None


def fetch_robot_frame(ip: str) -> bytes | None:
    token = create_access_token("admin")
    url = f"http://{ip}:9001/api/robot/camera/snapshot?token={token}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return response.read()
    except Exception:
        return None


class PinkyGreetingMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._states: dict[int, RobotLcdState] = {}
        self._running = False
        # ip_address -> monotonic 만료시각. 인식(WS)·주행 중인 로봇만 이 시각이 미래로 갱신된다.
        # 활성 로봇이 하나도 없으면 카메라 스냅샷 폴링을 아예 하지 않는다(유휴 부하 제거).
        self._active: dict[str, float] = {}

    def mark_active(self, ip: str | None, ttl: float = 60.0) -> None:
        """해당 로봇을 ttl초 동안 '활성'으로 표시(인식 WS·주행 명령 진입점에서 호출)."""
        if ip:
            self._active[ip] = time.monotonic() + ttl

    def _is_active(self, key: str | None) -> bool:
        return bool(key) and self._active.get(key, 0.0) > time.monotonic()

    def _robot_active(self, robot: Robot) -> bool:
        # 인식 WS(ip 키) 또는 주행 명령(id 키) 중 하나라도 활성이면 대상.
        return self._is_active(robot.ip_address) or self._is_active(f"id:{robot.id}")

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "running": self._running and self._task is not None and not self._task.done(),
            "robots": {
                str(robot_id): {
                    "remaining": max(0, int(state.busy_until - now)),
                    "last_error": state.last_error,
                    "last_detection_at": state.last_detection_at,
                }
                for robot_id, state in self._states.items()
            },
        }

    async def _get_settings(self, db) -> PinkyGreetingSettings:
        settings = (await db.execute(
            select(PinkyGreetingSettings).where(PinkyGreetingSettings.id == 1)
        )).scalar_one_or_none()
        if settings is None:
            settings = PinkyGreetingSettings(id=1)
            db.add(settings)
            await db.commit()
            await db.refresh(settings)
        return settings

    async def _loop(self) -> None:
        while True:
            try:
                async with SessionLocal() as db:
                    settings = await self._get_settings(db)
                    if not settings.enabled:
                        await asyncio.sleep(1.0)
                        continue

                    robots = (await db.execute(
                        select(Robot).where(Robot.robot_type == "pinky", Robot.is_active == True)
                    )).scalars().all()
                    config = self._settings_payload(settings)
                    duration = max(1, int(settings.duration_seconds or 10))

                # 인식(WS)·주행 중인 로봇만 대상으로 함. 없으면 카메라를 긁지 않고 대기.
                active_robots = [r for r in robots if self._robot_active(r)]
                if not active_robots:
                    await asyncio.sleep(1.0)
                    continue

                for robot in active_robots:
                    await self._check_robot(robot, config, duration)
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                self._running = False
                raise
            except Exception as exc:
                print(f"[pinky-greeting] loop error: {exc}", flush=True)
                await asyncio.sleep(2.0)

    def _settings_payload(self, settings: PinkyGreetingSettings) -> dict[str, Any]:
        return {
            "text": settings.text,
            "font_name": settings.font_name,
            "font_size": settings.font_size,
            "color": settings.color,
            "bg_color": settings.bg_color,
            "align": settings.align,
            "scroll": False,
            "scroll_speed": 3,
        }

    async def _check_robot(self, robot: Robot, config: dict[str, Any], duration: int) -> None:
        state = self._states.setdefault(robot.id, RobotLcdState())
        now = time.monotonic()
        if state.busy_until > now:
            return

        jpeg = await asyncio.to_thread(fetch_robot_frame, robot.ip_address)
        if not jpeg:
            state.last_error = "camera frame unavailable"
            return

        result = await asyncio.to_thread(pinky_yolo.detect_jpeg, jpeg)
        detections = (result or {}).get("detections") or []
        if not any(item.get("label") == "pinky_63" for item in detections):
            state.last_error = None
            return

        state.last_detection_at = time.time()
        ok, error = await self._show_lcd(robot, config, duration)
        if ok:
            state.busy_until = time.monotonic() + duration
            state.last_error = None
        else:
            state.last_error = error

    async def _show_lcd(self, robot: Robot, config: dict[str, Any], duration: int) -> tuple[bool, str | None]:
        text_ok, text_error = await asyncio.to_thread(self._post_robot, robot, "/api/robot/lcd/text", config)
        if not text_ok:
            return False, text_error
        asyncio.create_task(self._stop_lcd_later(robot, duration))
        return True, None

    async def _stop_lcd_later(self, robot: Robot, duration: int) -> None:
        await asyncio.sleep(duration)
        await asyncio.to_thread(self._post_robot, robot, "/api/robot/lcd/stop", None)

    def _post_robot(self, robot: Robot, path: str, payload: dict[str, Any] | None) -> tuple[bool, str | None]:
        url = f"http://{robot.ip_address}:{robot.port}{path}"
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        token = create_access_token(1)
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as response:
                if response.status >= 400:
                    return False, f"HTTP {response.status}"
            return True, None
        except Exception as exc:
            return False, str(exc)


pinky_greeting_monitor = PinkyGreetingMonitor()
