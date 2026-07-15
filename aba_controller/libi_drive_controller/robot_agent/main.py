"""로봇 온보드 에이전트 공통 진입점.

ROBOT_TYPE(.env) 을 읽어 드라이버·라우터를 선택하고, driving 타입이면
rclpy 노드를 함께 기동한다. 5대의 로봇 PC 가 동일 코드로 :9001 에 뜬다.
"""
import uvicorn

from app.config import settings
from app.core.server import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
