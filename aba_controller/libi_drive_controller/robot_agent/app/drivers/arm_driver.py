import logging
import threading
from typing import Optional

from app.core.ros_node import RosNode
from app.drivers.base import BaseDriver
from app.schemas.arm import JointState

logger = logging.getLogger(__name__)


class ArmDriver(BaseDriver):
    """JetCobot 관절·그리퍼 제어 (pymycobot).

    pymycobot 는 arm PC 에서만 필요하므로 start() 안에서 lazy import 한다.
    실제 시리얼 연결 파라미터(포트/보레이트)는 /dev/ttyJETCOBOT, 1,000,000 을 사용한다.
    """

    def start(self, ros_node: Optional[RosNode] = None) -> None:
        super().start(ros_node)
        self._mc = None
        self._connected = False
        self._sdk_lock = threading.Lock()

        # lazy import pymycobot
        try:
            from pymycobot.mycobot280 import MyCobot280
            logger.info("Successfully imported MyCobot280 SDK")
            try:
                # /dev/ttyJETCOBOT 포트 연결 시도
                mc = MyCobot280("/dev/ttyJETCOBOT", 1000000)
                mc.thread_lock = True
                self._mc = mc
                self._connected = True
                logger.info("Connected to MyCobot280 on /dev/ttyJETCOBOT")
            except Exception as e:
                logger.error(f"Failed to connect to /dev/ttyJETCOBOT: {e}. Trying ttyUSB0 fallback.")
                try:
                    # ttyUSB0 폴백
                    mc = MyCobot280("/dev/ttyUSB0", 1000000)
                    mc.thread_lock = True
                    self._mc = mc
                    self._connected = True
                    logger.info("Connected to MyCobot280 on /dev/ttyUSB0 fallback")
                except Exception as e2:
                    logger.error(f"Failed fallback connection: {e2}. Running in Demo mode.")
        except ImportError:
            logger.warning("pymycobot SDK not found. Running in Demo mode.")

    def shutdown(self) -> None:
        if self._mc is not None:
            try:
                self._mc.close()
            except Exception:
                pass
        self._mc = None
        self._connected = False

    def get_status(self) -> dict:
        if self._mc is not None and self._connected:
            with self._sdk_lock:
                try:
                    joints = self._mc.get_angles() or [0.0] * 6
                    gripper = float(self._mc.get_gripper_value() or 0)
                    return {
                        "connected": True,
                        "joints": [float(x) for x in joints],
                        "gripper": gripper
                    }
                except Exception:
                    self._connected = False
        return {
            "connected": False,
            "joints": [0.0] * 6,
            "gripper": 0.0
        }

    def send_angles(self, angles: list[float], speed: int) -> dict:
        if self._mc is not None and self._connected:
            with self._sdk_lock:
                try:
                    self._mc.send_angles(angles, speed)
                    return {"success": True}
                except Exception as e:
                    logger.error(f"send_angles error: {e}")
        return {"success": False, "note": "demo mode or connection lost"}

    def set_gripper_value(self, value: int, speed: int) -> dict:
        if self._mc is not None and self._connected:
            with self._sdk_lock:
                try:
                    self._mc.set_gripper_value(value, speed)
                    return {"success": True}
                except Exception as e:
                    logger.error(f"set_gripper_value error: {e}")
        return {"success": False, "note": "demo mode or connection lost"}

    def home(self) -> dict:
        if self._mc is not None and self._connected:
            with self._sdk_lock:
                try:
                    self._mc.send_angles([0.0] * 6, 40)
                    # 약간의 대기 후 그리퍼 열기
                    # (에이전트가 단독 스레드로 제어하므로 blocking sleep 가능)
                    import time
                    time.sleep(1.0)
                    self._mc.set_gripper_value(100, 40)
                    return {"success": True}
                except Exception as e:
                    logger.error(f"home error: {e}")
        return {"success": False, "note": "demo mode or connection lost"}

    def stop(self) -> dict:
        if self._mc is not None and self._connected:
            with self._sdk_lock:
                try:
                    self._mc.stop()
                    return {"success": True}
                except Exception as e:
                    logger.error(f"stop error: {e}")
        return {"success": False, "note": "demo mode or connection lost"}

    def jog_stop(self) -> dict:
        return self.stop()

    def jog(self, joint: int, delta: float) -> dict:
        if self._mc is not None and self._connected:
            with self._sdk_lock:
                try:
                    # delta가 양수면 1 (정방향), 음수면 0 (역방향)
                    direction = 1 if delta > 0 else 0
                    self._mc.jog_angle(joint, direction, int(abs(delta) * 10))
                    return {"success": True}
                except Exception as e:
                    logger.error(f"jog error: {e}")
        return {"success": False, "joint": joint, "delta": delta}

    def set_gripper(self, position: float) -> dict:
        # Pydantic schema position (0.0~1.0) -> SDK gripper value (0~100)
        val = int(position * 100)
        return self.set_gripper_value(val, 20)
