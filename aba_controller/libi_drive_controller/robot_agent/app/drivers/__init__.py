from app.config import RobotType
from app.drivers.base import BaseDriver


def create_driver(robot_type: RobotType) -> BaseDriver:
    """ROBOT_TYPE 에 맞는 드라이버를 생성한다.

    하드웨어 의존성(pymycobot, rclpy)은 선택된 타입에서만 import 되도록
    각 드라이버 모듈 안에서 lazy import 한다. 그래야 arm PC 에 ROS2 가,
    driving PC 에 pymycobot 이 없어도 부팅이 가능하다.
    """
    if robot_type is RobotType.arm:
        from app.drivers.arm_driver import ArmDriver

        return ArmDriver()
    if robot_type is RobotType.driving:
        from app.drivers.driving_driver import DrivingDriver

        return DrivingDriver()
    raise ValueError(f"Unknown ROBOT_TYPE: {robot_type}")
