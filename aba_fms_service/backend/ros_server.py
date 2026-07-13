"""aba_fms_service 의 ROS2 서비스 서버 — /fms/task_request (AssignTask).

aba_server 의 접수 요청을 수락한다. 연동성 스모크라 std_srvs/Trigger 사용
(실물에서는 커스텀 srv 로 task_type/pickup/dropoff 전달). rclpy 는 FastAPI
프로세스 안 별도 스레드에서 spin.
"""
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor
from std_srvs.srv import Trigger


class RosServer:
    def __init__(self, node_name: str = "aba_fms_ros"):
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node(node_name)
        self.srv = self.node.create_service(Trigger, "/fms/task_request", self._on_request)
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self.node)
        threading.Thread(target=self._exec.spin, daemon=True).start()

    def _on_request(self, request, response):  # noqa: ARG002
        # 배차는 FMS 내부에서 — 스텁은 무조건 수락
        response.success = True
        response.message = "accepted robot_id=pinky1 (fms skeleton)"
        return response
