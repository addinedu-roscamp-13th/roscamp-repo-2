import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import AdminSessionLocal, init_db
from app.models import Admin
from app.hardware.camera_stream import camera as camera_hw
from app.hardware.pinky_greeting_monitor import pinky_greeting_monitor
from app.routers import admin_follow, arm, aruco_dock, auth, camera, chat, dashboard, dev, drive, fleet, fleet_order, fsm, human_follow_robot, maps, marker_actions, mission_control, nav, pinky_yolo, robot, robot_learning, robots, ros, users, voice, webrtc_robot
from app.security import hash_password

# ── 로깅 ────────────────────────────────────────────────────────────────────
# ⚠️ **이걸 안 하면 `log.info` 가 전부 사라진다.**
# 파이썬 루트 로거의 기본 레벨은 WARNING 이라, 핸들러도 레벨도 없이 `getLogger(...)` 만
# 쓰면 info 는 조용히 버려진다. 2026-07-21 디버깅 내내 "로그에 아무것도 없다"며 헤맸는데,
# 실제로는 찍히고 버려지고 있었다 — `[dispatch]`·`[arm]`·`[lifecycle]` 이 그랬고,
# 눈에 보이던 `[reconcile]`·`[release]` 는 우연히 warning 이라 통과한 것이었다.
#
# 레벨은 LIBI_LOG_LEVEL 로 바꾼다 (기본 INFO). 시끄러우면 WARNING.
logging.basicConfig(
    level=os.environ.get("LIBI_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    force=True,          # uvicorn 이 먼저 잡아둔 핸들러를 밀어낸다 (없으면 무시된다)
)

app = FastAPI(title="LiBi Admin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(arm.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(dev.router)
app.include_router(robot.router)
app.include_router(robot_learning.router)
app.include_router(robots.router)
app.include_router(drive.router)
app.include_router(fsm.router)
app.include_router(fleet.router)
app.include_router(fleet_order.router)
app.include_router(admin_follow.router)
app.include_router(ros.router)
app.include_router(maps.router)
app.include_router(mission_control.router)
app.include_router(nav.router)
app.include_router(camera.router)
app.include_router(chat.router)
app.include_router(pinky_yolo.router)
app.include_router(human_follow_robot.router)
app.include_router(webrtc_robot.router)
app.include_router(aruco_dock.router)
app.include_router(marker_actions.router)
app.include_router(voice.router)

@app.get("/api/health")
async def health():
    return {"status": "ok"}


async def _seed():
    from sqlalchemy import func, select
    from app.models import Robot

    async with AdminSessionLocal() as db:
        existing = (
            await db.execute(select(Admin).where(Admin.username == "admin"))
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Admin(
                    username="admin",
                    password_hash=hash_password("admin1234"),
                    full_name="관리자",
                    role="superadmin",
                    is_active=True,
                )
            )
            await db.commit()

        robot_count = (
            await db.execute(select(func.count()).select_from(Robot))
        ).scalar_one()
        if robot_count == 0:
            db.add_all([
                Robot(
                    name="CentralServer",
                    robot_type="server",
                    ip_address="192.168.1.4",
                    port=9001,
                    description="중앙 AI 서버",
                    is_active=True,
                ),
                Robot(
                    name="JetCobot-1",
                    robot_type="arm",
                    ip_address="192.168.0.70",
                    port=9001,
                    ai_server_url="http://192.168.1.4:9001",
                    description="로봇팔 JetCobot",
                    is_active=True,
                ),
                Robot(
                    name="Pinky-1",
                    robot_type="pinky",
                    ip_address="192.168.0.71",
                    port=9001,
                    description="PinkyPro 주행 로봇",
                    is_active=True,
                ),
            ])
            await db.commit()
        else:
            # 기존 레코드도 업데이트
            arms = (await db.execute(
                select(Robot).where(Robot.robot_type == "arm")
            )).scalars().all()
            updated = False
            for arm in arms:
                if arm.ai_server_url != "http://192.168.1.4:9001":
                    arm.ai_server_url = "http://192.168.1.4:9001"
                    updated = True
            if updated:
                await db.commit()




async def _start_camera_push_if_needed():
    """robots 테이블에 arm 로봇과 server 레코드가 모두 있으면 카메라 PUSH 태스크를 시작한다."""
    from sqlalchemy import select
    from app.models import Robot
    async with AdminSessionLocal() as db:
        arm = (await db.execute(
            select(Robot).where(Robot.robot_type == "arm", Robot.is_active == True)
        )).scalar_one_or_none()

        server = (await db.execute(
            select(Robot).where(Robot.robot_type == "server", Robot.is_active == True)
        )).scalar_one_or_none()

    if arm and server:
        import asyncio as _asyncio
        from app.hardware.camera_push import camera_push_loop
        server_url = f"http://{server.ip_address}:{server.port}"
        _asyncio.create_task(camera_push_loop(server_url))
        print(f"[startup] 카메라 PUSH 시작 → {server_url}", flush=True)


@app.on_event("startup")
async def startup():
    import asyncio as _asyncio

    await init_db()
    await _seed()
    await _start_camera_push_if_needed()
    camera_hw.start()
    from app import ros_bridge
    ros_bridge.start()

    pinky_greeting_monitor.start()

    # 플릿 텔레메트리(도메인 87 구독 캐시) — /api/control/state 를 HTTP 프록시 없이 응답
    from app import fleet_telemetry
    fleet_telemetry.start()

    # libi_modes FSM 링크(상태/BT 스냅샷 구독 + 전이 요청 채널) — /api/fsm/* 가 읽는 캐시
    from app import fsm_link
    fsm_link.start()

    # libi_fleet 배차·교통 링크(fleet_ws/fleet_node 서비스 호출 + 피드 구독) — /api/fleet/* 가 읽는 캐시
    from app import fleet_link
    fleet_link.start()

    # orchestrator ↔ fleet_node 실배선(핸드오프 6절 ①). 켜면 주문의 주행 다리가 실제로
    # fleet_node 로 나가고, 완료는 task_states 훅으로 돌아온다. 로봇/sim 이 없을 땐 꺼두면
    # 기존 _stub_dispatch 로 남아 패널에서 force-advance 로만 시퀀스를 본다.
    import os as _os_disp
    if _os_disp.environ.get("LIBI_REAL_DISPATCH") == "1":
        from app import fleet_dispatch_bridge
        fleet_dispatch_bridge.install()
        print("[startup] orchestrator 실배선 활성화 (LIBI_REAL_DISPATCH=1)", flush=True)
    else:
        print("[startup] orchestrator 는 stub dispatch (LIBI_REAL_DISPATCH=1 로 실배선)", flush=True)

    # 주행로봇 근접 안전 코디네이터(Phase1). fleet_node 의 교통 플러그인(기본 CbsTraffic =
    # CBS + 가중 Space-Time A*, 계획이 밀리면 ReservationDeadlock 반응형으로 자동 강등)이
    # 같은 문제를 더 정교하게 풀어 이걸 대체할 예정이다(결정 완료). 다만 플러그인이 실제로
    # 뜨고 검증되기 전엔 서버쪽 분리 공백을 막으려 coordinator 를 그대로 둔다 — 기본 ON.
    # 플러그인 검증(#18) 후 은퇴: env FMS_DISABLE_COORDINATOR=1 로 끄거나 이 블록 제거.
    import os as _os
    if _os.environ.get("FMS_DISABLE_COORDINATOR") == "1":
        print("[startup] 근접 안전 코디네이터 비활성(FMS_DISABLE_COORDINATOR=1) "
              "— 교통은 fleet_node 교통 플러그인(기본 CbsTraffic)이 담당한다고 가정", flush=True)
    else:
        from app.fleet_coordinator import coordinator
        _asyncio.create_task(coordinator.run())
        print("[startup] 근접 안전 코디네이터 루프 시작 (설정 DB 로드; 기본 enabled=on)", flush=True)



if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=9001, reload=True)
