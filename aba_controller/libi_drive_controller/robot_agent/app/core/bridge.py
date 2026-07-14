import threading
from typing import Optional

from app.drivers.base import BaseDriver


class Bridge:
    """API(요청 스레드) ↔ 드라이버/ROS(spin 스레드) 사이의 공유 지점.

    FastAPI 핸들러와 rclpy executor 는 서로 다른 스레드에서 돌기 때문에,
    활성 드라이버 접근을 이 한 곳으로 모아 락으로 보호한다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._driver: Optional[BaseDriver] = None

    def set_driver(self, driver: BaseDriver) -> None:
        with self._lock:
            self._driver = driver

    @property
    def driver(self) -> BaseDriver:
        with self._lock:
            if self._driver is None:
                raise RuntimeError("Driver is not initialized yet")
            return self._driver


#: 프로세스 전역 단일 브리지 (로봇 PC 1대 = 에이전트 1개)
bridge = Bridge()
