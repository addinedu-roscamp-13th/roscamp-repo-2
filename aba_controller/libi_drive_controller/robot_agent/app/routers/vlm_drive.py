import asyncio
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from app.deps import get_current_admin
except ImportError:
    def get_current_admin():
        pass

from app.hardware.camera_stream import camera
from app.llm import generate_json

try:
    from app.routers.drive import _motor_send, _vel_to_speeds
except ImportError:
    from app.routers.driving import _motor_send, _vel_to_speeds

router = APIRouter(prefix="/api/robot/vlm", tags=["vlm-drive"])


class VlmDecisionRequest(BaseModel):
    model: str = Field(default="glm-ocr", description="Ollama vision model to use (e.g., glm-ocr, llama3.2-vision, qwen2-vl)")
    prompt: str | None = Field(default=None, description="Custom prompt. If not provided, a default driving prompt is used.")
    run_motor: bool = Field(default=False, description="Whether to actually send the velocity command to the motors (dry-run by default)")


DEFAULT_DRIVING_PROMPT = """
너는 자율주행 로봇의 두뇌이자 상황 판단 제어기다.
첨부된 이미지는 로봇의 전방 카메라에서 실시간으로 촬영한 사진이다.
1. 전방에 보행자(사람), 벽, 가구, 박스 또는 기타 장애물이 있는지 식별하라.
2. 장애물과의 거리를 예측하고 안전하게 주행하기 위한 이동 방향을 결정하라.
3. 응답은 반드시 아래 지정된 JSON 형식으로만 응답해야 한다. 추가적인 자연어 설명 없이 오직 JSON만 반환하라.

JSON Format:
{
  "has_obstacle": true 또는 false,
  "obstacle_desc": "식별한 장애물 설명",
  "status": "drive" (정상 주행) 또는 "stop" (정지) 또는 "avoid" (우회),
  "linear": -1.0에서 1.0 사이의 전진/후진 속도비율 (0.0은 정지, 보통 전진 시 0.2~0.4),
  "angular": -1.0에서 1.0 사이의 회전비율 (음수는 좌회전, 양수는 우회전, 0.0은 직진),
  "reason": "판단 근거 설명"
}
"""


@router.post("/decision")
async def get_vlm_decision(
    body: VlmDecisionRequest,
    current_admin=Depends(get_current_admin)
) -> dict[str, Any]:
    # 1. 카메라 프레임 확보
    if not camera.is_running():
        camera.start()
        # 카메라 시작 대기
        await asyncio.sleep(0.5)

    frame_id, jpeg = camera.get_frame()
    if not jpeg:
        raise HTTPException(status_code=500, detail="카메라 프레임을 가져오지 못했습니다.")

    # VLM 전송용 이미지 리사이징 (토큰 사용량 절약 및 Groq 429 Rate Limit 회피)
    try:
        import cv2
        import numpy as np
        nparr = np.frombuffer(jpeg, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            resized = cv2.resize(img, (320, 240), interpolation=cv2.INTER_AREA)
            _, jpeg_resized = cv2.imencode('.jpg', resized, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            jpeg = jpeg_resized.tobytes()
    except Exception:
        pass

    # 2. 프롬프트 결정
    prompt = body.prompt.strip() if body.prompt else DEFAULT_DRIVING_PROMPT

    # 3. Ollama VLM 호출
    try:
        # 동기적인 HTTP 호출을 ThreadPoolExecutor에서 실행
        decision = await asyncio.to_thread(
            generate_json,
            prompt=prompt,
            model=body.model,
            image_bytes=jpeg
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ollama VLM ({body.model}) 호출 중 오류가 발생했습니다: {str(e)}"
        )

    # 4. 결과 분석 및 모터 제어 (안전 규칙: run_motor=True 일 때만 구동)
    motor_info = {"sent": False, "left": 0, "right": 0}
    if body.run_motor:
        status = decision.get("status", "stop")
        linear = float(decision.get("linear", 0.0))
        angular = float(decision.get("angular", 0.0))

        if status == "stop":
            left, right = 0, 0
        else:
            left, right = _vel_to_speeds(linear, angular)

        try:
            await _motor_send(left, right)
            motor_info = {"sent": True, "left": left, "right": right}
        except Exception as e:
            motor_info = {"sent": False, "error": f"모터 구동 실패: {str(e)}", "left": 0, "right": 0}

    return {
        "success": True,
        "model": body.model,
        "decision": decision,
        "motor_command": motor_info,
        "frame_id": frame_id
    }
