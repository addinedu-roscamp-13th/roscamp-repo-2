from abc import ABC, abstractmethod
from typing import Optional

from app.core.ros_node import RosNode


class BaseDriver(ABC):
    """모든 로봇 드라이버의 공통 인터페이스.

    라우터/브리지는 이 인터페이스만 알면 되고, 실제 하드웨어 제어
    (pymycobot / ROS2) 는 하위 클래스가 캡슐화한다.
    """

    #: driving 타입처럼 rclpy 노드가 필요한 경우 start() 에서 주입된다.
    ros_node: Optional[RosNode] = None

    def start(self, ros_node: Optional[RosNode] = None) -> None:
        """드라이버 초기화. lifespan 시작 시 1회 호출된다."""
        self.ros_node = ros_node

    def shutdown(self) -> None:
        """드라이버 정리. lifespan 종료 시 1회 호출된다."""

    @abstractmethod
    def get_status(self) -> dict:
        """현재 로봇 상태(스키마 호환 dict)를 반환한다."""

    @abstractmethod
    def stop(self) -> dict:
        """즉시 정지."""

    @abstractmethod
    def home(self) -> dict:
        """홈 포지션 이동/복귀."""
