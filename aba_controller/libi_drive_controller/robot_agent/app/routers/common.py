from fastapi import APIRouter

from app.core.bridge import bridge

router = APIRouter()


@router.get("/health")
def health_check():
    """헬스 체크 (드라이버 초기화 전에도 응답)."""
    return {"status": "ok"}


@router.get("/status")
def get_status():
    """현재 로봇 상태 조회 (타입별 드라이버가 형태 결정)."""
    return bridge.driver.get_status()


@router.post("/stop")
def stop():
    """즉시 정지 (공통)."""
    return bridge.driver.stop()


@router.post("/home")
def go_home():
    """홈 포지션 이동/복귀 (공통)."""
    return bridge.driver.home()
