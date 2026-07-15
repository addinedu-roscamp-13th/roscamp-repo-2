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


# ── Nav2 대시보드(nav2_web_server 이전) 요청 스키마 ──────────────────

class GoalRequest(BaseModel):
    x: float = Field(..., description="목표 X 좌표 (map 프레임, m)")
    y: float = Field(..., description="목표 Y 좌표 (map 프레임, m)")
    yaw: float = Field(0.0, description="목표 방향각 (rad)")


class LocationSetRequest(BaseModel):
    name: str = Field(..., description="구역 이름 (예: A, HOME)")
    x: float | None = Field(None, description="좌표 지정 시 X (없으면 현재 TF 위치로 저장)")
    y: float | None = Field(None, description="좌표 지정 시 Y")
    yaw: float = Field(0.0, description="방향각 (rad)")


class NameRequest(BaseModel):
    name: str = Field(..., description="구역 이름")


class MissionStartRequest(BaseModel):
    names: list[str] | None = Field(None, description="순회 구역 순서 (없으면 등록된 전체)")
    loop: bool = Field(False, description="반복 순찰 여부")


class ScheduleStartRequest(BaseModel):
    minutes: int = Field(..., ge=1, description="순찰 주기 (분)")
    names: list[str] | None = Field(None, description="순회 구역 (없으면 등록된 전체)")
    loop: bool = Field(False, description="각 순찰의 반복 여부")


class SaveMapRequest(BaseModel):
    name: str = Field("", description="저장 파일명 (비우면 날짜 기반 자동 생성)")
