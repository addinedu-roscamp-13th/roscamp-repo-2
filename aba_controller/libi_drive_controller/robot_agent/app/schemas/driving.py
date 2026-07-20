from pydantic import BaseModel, Field
from typing import Literal


class MoveRequest(BaseModel):
    direction: Literal["forward", "backward", "left", "right"] = Field(..., description="이동 방향")
    distance: float = Field(..., gt=0, description="이동 거리 (m)")
    speed: float = Field(0.3, gt=0, le=1.0, description="속도 비율 (0.0~1.0)")


class RotateRequest(BaseModel):
    angle: float = Field(..., description="회전 각도 (degree, 양수=좌회전, 음수=우회전)")
    speed: float = Field(0.3, gt=0, le=1.0, description="속도 비율 (0.0~1.0)")


class DriveState(BaseModel):
    x: float = Field(..., description="현재 X 좌표 (m)")
    y: float = Field(..., description="현재 Y 좌표 (m)")
    heading: float = Field(..., description="현재 방향각 (degree)")
    speed: float = Field(..., description="현재 속도 (m/s)")

