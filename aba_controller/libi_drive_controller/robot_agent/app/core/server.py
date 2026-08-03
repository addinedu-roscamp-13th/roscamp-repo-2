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
    # 배포 원본: aba_controller/.../robot_agent/app/core/fleet_link.py (deploy_fleet_link.py)
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
    from app.routers import camera, common, pinky_detect
    try:
        from app.routers import human_follow
    except ImportError as e:
        # 2026-08-04 실측: human_follow.py 가 driving.py 의 존재하지 않는
        # _motor_send 를 임포트해 앱 전체가 기동 실패한다(다른 기능과 무관한
        # 사전 존재 버그). human_follow 는 애초에 호출 불가능했으므로 건너뛰어도
        # 잃는 기능이 없다 — robot_agent 의 나머지 라우터(camera 등)를 살린다.
        import logging
        logging.getLogger(__name__).warning(f"human_follow router 임포트 실패, 건너뜀: {e}")
        human_follow = None

    try:
        from app.routers import vlm_drive
        app.include_router(vlm_drive.router)
    except ImportError as e:
        # 2026-08-04 실측: vlm_drive.py 가 존재하지 않는 driving._motor_send/_vel_to_speeds
        # 를 임포트해 앱 전체(arm/driving 공통)가 기동 실패한다 — human_follow 와 같은
        # 사전 존재 버그. vlm_drive 도 애초에 호출 불가능했으므로 건너뛰어도 잃는 기능이
        # 없다.
        import logging
        logging.getLogger(__name__).warning(f"vlm_drive router 임포트 실패, 건너뜀: {e}")

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
        # nav_router(Nav2 대시보드)는 로봇 온보드에서 제거됨(FastAPI/ROS2 역할 분리, main).
        # driving.py 에 더 이상 nav_router 가 없어 마운트하지 않는다.
        import logging as _logging
        # 2026-08-04 실측: 세 도킹 라우터 전부 존재하지 않는 driving._motor_send 등을
        # 임포트해 기동 실패한다 — 다른 세션이 진행 중인 리팩터(driving_driver.py 로
        # 모터 제어 이동)가 아직 이 파일들에 안 미친 상태로 보인다. 남의 진행 중인
        # 리팩터를 추측해서 고치지 않고, night-patrol 검증에 불필요한 이 라우터들만
        # 건너뛴다 — camera/common/driving(move·rotate)은 영향 없다.
        for _name in ("aruco_dock", "line_dock", "park_dock"):
            try:
                _mod = __import__(f"app.routers.{_name}", fromlist=[_name])
                api.include_router(_mod.router, prefix="/robot", tags=[_name.replace("_", "-")])
                api.include_router(_mod.router, prefix="/admin/robot", tags=[f"{_name}-legacy".replace("_", "-")], include_in_schema=False)
            except ImportError as e:
                _logging.getLogger(__name__).warning(f"{_name} router 임포트 실패, 건너뜀: {e}")
        if human_follow is not None:
            api.include_router(human_follow.router, prefix="/robot", tags=["human-follow"])
            api.include_router(human_follow.router, prefix="/admin/robot", tags=["human-follow-legacy"], include_in_schema=False)

    app.include_router(api)

    return app
