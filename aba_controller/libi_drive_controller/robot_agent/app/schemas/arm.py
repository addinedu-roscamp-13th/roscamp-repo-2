from typing import Any, List
from pydantic import BaseModel, Field


class JogRequest(BaseModel):
    joint: int = Field(..., ge=0, le=5, description="관절 인덱스 (0~5)")
    delta: float = Field(..., description="이동 각도 (degree)")


class GripperRequest(BaseModel):
    position: float = Field(..., ge=0.0, le=1.0, description="0.0 완전 닫힘, 1.0 완전 열림")


class JointState(BaseModel):
    joints: List[float] = Field(..., description="6축 관절 각도 (degree)")
    gripper: float = Field(..., description="그리퍼 위치")


class ConfidenceRequest(BaseModel):
    confidence: float = Field(..., ge=0.1, le=0.95)


class PinkyStatus(BaseModel):
    available: bool
    loaded: bool
    model_path: str
    dataset_path: str
    classes: List[str]
    confidence: float
    error: str | None
    last_result: Any | None
