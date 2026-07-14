import asyncio
import math
import subprocess
import time
from pathlib import Path
from typing import Optional

from app.core.ros_node import RosNode
from app.drivers.base import BaseDriver
from app.schemas.driving import DriveState


class DrivingDriver(BaseDriver):
    """Pinky 주행 제어 (ROS2 navigation).

    RosNode 또는 ros_bridge 가 띄운 rclpy 노드를 통해 cmd_vel publish / odom subscribe 한다.
    ROS 환경이 아닐 경우 app/hardware/motor_ctrl.py 를 사용하여 직접 모터를 제어한다.
    """

    def start(self, ros_node: Optional[RosNode] = None) -> None:
        super().start(ros_node)
        self._last_state = DriveState(x=0.0, y=0.0, heading=0.0, speed=0.0)
        self._motor_script = Path(__file__).parent.parent / "hardware" / "motor_ctrl.py"

    def get_status(self) -> dict:
        from app.core import ros_bridge
        if ros_bridge.is_active():
            odom = ros_bridge.get_topic("odom")
            if odom:
                return {
                    "x": odom["x"],
                    "y": odom["y"],
                    "heading": math.degrees(odom["yaw"]) % 360,
                    "speed": odom["vx"]
                }
        return self._last_state.model_dump()

    def _vel_to_speeds(self, linear: float, angular: float) -> tuple[int, int]:
        MAX_SPEED = 75  # 최대 모터 속도 %
        lin = max(-1.0, min(1.0, linear / 0.25 if linear != 0 else 0.0))
        ang = max(-1.0, min(1.0, angular / 1.0 if angular != 0 else 0.0))
        left = int((lin - ang) * MAX_SPEED)
        right = int((lin + ang) * MAX_SPEED)
        return max(-100, min(100, left)), max(-100, min(100, right))

    def _run_motor_cmd(self, left: int, right: int, duration: float) -> dict:
        cmd = f"sudo -n python3 {self._motor_script} move {left} {right} {duration}"
        try:
            r = subprocess.run(
                ["bash", "-c", cmd],
                text=True, capture_output=True, timeout=int(duration + 5), check=False
            )
            return {"success": r.returncode == 0, "output": r.stdout, "error": r.stderr}
        except Exception as e:
            return {"success": False, "output": "", "error": str(e)}

    def move(self, direction: str, distance: float, speed: float) -> dict:
        from app.core import ros_bridge
        
        linear_speed = speed * 0.25  # m/s (최대 물리 속도를 약 0.25 m/s 로 가정)
        duration = distance / linear_speed if linear_speed > 0 else 0
        
        linear_vel = 0.0
        angular_vel = 0.0
        if direction == "forward":
            linear_vel = linear_speed
        elif direction == "backward":
            linear_vel = -linear_speed
        elif direction == "left":
            angular_vel = speed * 1.0
        elif direction == "right":
            angular_vel = -speed * 1.0

        if ros_bridge.is_active():
            t0 = time.time()
            while time.time() - t0 < duration:
                ros_bridge.publish_cmd_vel(linear_vel, angular_vel)
                time.sleep(0.1)
            ros_bridge.publish_cmd_vel(0.0, 0.0)
        else:
            left, right = self._vel_to_speeds(linear_vel, angular_vel)
            self._run_motor_cmd(left, right, duration)
            
        return {"direction": direction, "distance": distance, "speed": speed, "status": "done"}

    def rotate(self, angle: float, speed: float) -> dict:
        from app.core import ros_bridge
        
        angular_speed = speed * 1.0  # rad/s (~57 deg/s)
        rad = math.radians(abs(angle))
        duration = rad / angular_speed if angular_speed > 0 else 0
        
        angular_vel = angular_speed if angle > 0 else -angular_speed
        
        if ros_bridge.is_active():
            t0 = time.time()
            while time.time() - t0 < duration:
                ros_bridge.publish_cmd_vel(0.0, angular_vel)
                time.sleep(0.1)
            ros_bridge.publish_cmd_vel(0.0, 0.0)
        else:
            left, right = self._vel_to_speeds(0.0, angular_vel)
            self._run_motor_cmd(left, right, duration)
            
        return {"angle": angle, "speed": speed, "status": "done"}

    def home(self) -> dict:
        from app.core import ros_bridge
        if ros_bridge.is_active():
            ros_bridge.publish_nav_goal(0.0, 0.0, 0.0)
        return {"result": "homing"}

    def stop(self) -> dict:
        from app.core import ros_bridge
        if ros_bridge.is_active():
            ros_bridge.publish_cmd_vel(0.0, 0.0)
        
        subprocess.run(
            ["sudo", "-n", "python3", str(self._motor_script), "stop"],
            capture_output=True, check=False
        )
        return {"result": "stopped"}
