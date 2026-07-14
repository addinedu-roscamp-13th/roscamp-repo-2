from fastapi import APIRouter

router = APIRouter(prefix="/arm/pinky-detect")


@router.get("/status")
def status() -> dict:
    return {
        "available": False,
        "loaded": False,
        "error": "추론은 중앙 서버에서 처리합니다.",
    }
