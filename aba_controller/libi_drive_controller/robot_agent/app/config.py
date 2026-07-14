from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class RobotType(str, Enum):
    arm = "arm"
    driving = "driving"


class Settings(BaseSettings):
    """환경변수(.env)에서 로드되는 에이전트 설정."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        extra="ignore"
    )

    robot_type: RobotType = RobotType.arm
    host: str = "0.0.0.0"
    port: int = 9001
    ros_node_name: str = "robot_agent"
    camera_color_swap: str = "none"  # "none", "rgb_bgr", "yuv_uv"
    camera_flip: str = "none"        # "none", "vertical", "horizontal", "both"
    camera_device: str = "0"         # V4L2 camera device index or path (e.g. "/dev/jetcocam0" or "0")


settings = Settings()
