import threading
from typing import Optional


class RosNode:
    """rclpy 노드를 별도 스레드에서 spin 시키는 래퍼.

    Labi Bot의 주행 로봇(Pinky)용 ros_bridge 모듈을 래핑하여 시작/종료를 관리한다.
    """

    def __init__(self, node_name: str = "robot_agent") -> None:
        self.node_name = node_name

    @property
    def node(self):
        from app.core import ros_bridge
        return ros_bridge._node

    def start(self) -> None:
        from app.core import ros_bridge
        ros_bridge.start()

    def shutdown(self) -> None:
        import rclpy
        if rclpy.ok():
            try:
                rclpy.shutdown()
            except Exception:
                pass
