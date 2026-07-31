from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import RobotType, settings
from app.core.bridge import bridge
from app.core.ros_node import RosNode
from app.drivers import create_driver


@asynccontextmanager
async def lifespan(app: FastAPI):
    """기동/종료 훅: 드라이버 생성, (필요 시) rclpy 노드 spin, 정리."""
    ros_node = None
    if settings.robot_type is RobotType.driving:
        ros_node = RosNode(settings.ros_node_name)
        ros_node.start()

    driver = create_driver(settings.robot_type)
    driver.start(ros_node=ros_node)
    bridge.set_driver(driver)

    # [2026-07-08] fleet link — 중앙서버와 ROS2 토픽 통신 (HTTP 폴링 대체).
    # 배포/원본: bot_ai_server/backend/app/fleet_link_robot.py (deploy_fleet_link.py)
    if settings.robot_type is RobotType.driving:
        from app.core import fleet_link
        fleet_link.start()

    try:
        yield
    finally:
        driver.shutdown()
        if ros_node is not None:
            ros_node.shutdown()


def create_app() -> FastAPI:
    """ROBOT_TYPE 에 맞는 라우터만 장착한 FastAPI 앱을 만든다."""
    app = FastAPI(
        title=f"Robot Agent ({settings.robot_type.value})",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from fastapi import APIRouter
    from app.routers import camera, common, pinky_detect, human_follow

    from app.routers import vlm_drive
    app.include_router(vlm_drive.router)

    api = APIRouter(prefix="/api")
    api.include_router(common.router, tags=["common"])
    # 2026-07-07 admin 프리픽스 제거: 정식 경로는 /api/robot/* (중앙서버 규칙과 통일).
    # 기존 /api/admin/robot/* 는 구버전 호환 별칭으로 유지한다.
    api.include_router(camera.router, prefix="/robot/camera", tags=["camera"])
    api.include_router(camera.router, prefix="/admin/robot/camera", tags=["camera-legacy"], include_in_schema=False)
    api.include_router(pinky_detect.router, tags=["pinky-detect"])

    if settings.robot_type is RobotType.arm:
        from app.routers import arm
        # /api/arm 경로 지원 (api 라우터의 /api prefix + /arm)
        api.include_router(arm.router, prefix="/arm", tags=["arm"])
        # /arm 경로 지원 (app에 직접 등록)
        app.include_router(arm.router, prefix="/arm", tags=["arm"])
    elif settings.robot_type is RobotType.driving:
        from app.routers import driving
        # /driving 경로 지원 (app에 직접 등록)
        app.include_router(driving.router, prefix="/driving", tags=["driving"])
        # 정식 /api/robot + 구버전 호환 /api/admin/robot
        api.include_router(driving.router, prefix="/robot", tags=["driving"])
        api.include_router(driving.router, prefix="/admin/robot", tags=["driving-legacy"], include_in_schema=False)
        # Nav2 대시보드: Flask(nav2_web_server.py)와 동일한 /api/* 경로로 노출
        #   → /api/state, /api/goal, /api/locations, /api/mission/start ...
        api.include_router(driving.nav_router, tags=["nav"])
        from app.routers import aruco_dock
        api.include_router(aruco_dock.router, prefix="/robot", tags=["aruco-dock"])
        api.include_router(aruco_dock.router, prefix="/admin/robot", tags=["aruco-dock-legacy"], include_in_schema=False)
        from app.routers import line_dock
        api.include_router(line_dock.router, prefix="/robot", tags=["line-dock"])
        api.include_router(line_dock.router, prefix="/admin/robot", tags=["line-dock-legacy"], include_in_schema=False)
        from app.routers import park_dock
        api.include_router(park_dock.router, prefix="/robot", tags=["park-dock"])
        api.include_router(park_dock.router, prefix="/admin/robot", tags=["park-dock-legacy"], include_in_schema=False)
        from app.routers import parkp
        api.include_router(parkp.router, prefix="/robot", tags=["parkp"])
        api.include_router(parkp.router, prefix="/admin/robot", tags=["parkp-legacy"], include_in_schema=False)
        api.include_router(human_follow.router, prefix="/robot", tags=["human-follow"])
        api.include_router(human_follow.router, prefix="/admin/robot", tags=["human-follow-legacy"], include_in_schema=False)

    app.include_router(api)

    return app
