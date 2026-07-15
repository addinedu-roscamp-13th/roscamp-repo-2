"""aba_server 의 ROS2 서비스 클라이언트 — /fms/task_request (AssignTask).

rclpy 노드를 별도 스레드에서 spin 하고, HTTP 핸들러(스레드풀)에서
call_async + Event 로 응답을 기다린다. (보일러플레이트 패턴: FastAPI + rclpy 공존)
연동성 스모크라 커스텀 srv 대신 std_srvs/Trigger 사용.
"""
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor
from std_srvs.srv import Trigger


class RosClient:
    def __init__(self, node_name: str = "aba_server_ros"):
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node(node_name)
        self.cli = self.node.create_client(Trigger, "/fms/task_request")
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self.node)
        self._lock = threading.Lock()
        threading.Thread(target=self._exec.spin, daemon=True).start()

    def call_task_request(self, timeout: float = 5.0):
        """FMS 에 태스크 접수 요청. 성공 시 Trigger.Response, 실패/미가용 시 None."""
        with self._lock:
            if not self.cli.wait_for_service(timeout_sec=timeout):
                return None
            fut = self.cli.call_async(Trigger.Request())
            ev = threading.Event()
            fut.add_done_callback(lambda _f: ev.set())
            if not ev.wait(timeout):
                return None
            return fut.result()
