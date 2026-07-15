"""
Nav2/SLAM 관제 라우터 (robot_agent).

AI 서버(mission_control.py)가 ``/api/robot/nav/*`` 로 프록시한다.
프록시는 인증 토큰을 전달하지 않으므로(이미 AI 서버에서 관리자 인증을 마침)
이 라우터의 엔드포인트는 별도 인증을 두지 않는다. (옛 Flask nav2_web_server.py 와 동일)
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import nav_bridge

router = APIRouter(prefix="/api/robot/nav", tags=["nav"])


class GoalReq(BaseModel):
    x: float
    y: float
    yaw: float = 0.0


class SaveMapReq(BaseModel):
    name: str = ""


class LocationSetReq(BaseModel):
    name: str
    x: float | None = None
    y: float | None = None
    yaw: float | None = None


class NameReq(BaseModel):
    name: str


class MissionReq(BaseModel):
    names: list[str] | None = None
    loop: bool = False


class ScheduleReq(MissionReq):
    minutes: int = 0


def _node():
    node = nav_bridge.get_node()
    if node is None:
        raise HTTPException(status_code=500, detail="ROS not ready")
    return node


@router.get("/state")
def state():
    node = nav_bridge.get_node()
    if node is None:
        raise HTTPException(status_code=500, detail="ROS node not started")
    return node.get_state_snapshot()


@router.post("/goal")
def goal(body: GoalReq):
    ok = _node().send_goal(body.x, body.y, body.yaw)
    return {"success": ok}


@router.post("/slam/reset")
def slam_reset():
    ok = _node().slam_reset()
    return {"success": ok}


@router.post("/slam/save_map")
def slam_save_map(body: SaveMapReq):
    node = _node()
    name = body.name.strip()
    if not name:
        name = time.strftime("pinky_map_%Y%m%d_%H%M%S")
    ok = node.slam_save_map(name)
    return {"success": ok, "name": name}


@router.get("/locations")
def locations():
    with nav_bridge.locations_lock():
        return nav_bridge.load_locations()


@router.post("/locations/set")
def locations_set(body: LocationSetReq):
    node = _node()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name이 필요합니다")

    if body.x is not None and body.y is not None:
        x = float(body.x)
        y = float(body.y)
        yaw = float(body.yaw or 0.0)
    else:
        pose = node.get_current_pose()
        if pose is None:
            raise HTTPException(
                status_code=409,
                detail="현재 위치(TF)를 아직 알 수 없습니다. 위치추정/주행을 먼저 확인하세요.",
            )
        x, y, yaw = pose

    with nav_bridge.locations_lock():
        locs = nav_bridge.load_locations()
        locs[name] = {"x": round(x, 4), "y": round(y, 4), "yaw": round(yaw, 4)}
        nav_bridge.save_locations(locs)

    return {"success": True, "name": name, "x": x, "y": y, "yaw": yaw}


@router.post("/locations/delete")
def locations_delete(body: NameReq):
    name = body.name.strip()
    with nav_bridge.locations_lock():
        locs = nav_bridge.load_locations()
        if name in locs:
            del locs[name]
            nav_bridge.save_locations(locs)
            return {"success": True, "name": name}
    raise HTTPException(status_code=404, detail=f"'{name}' 위치가 없습니다")


@router.post("/goto")
def goto(body: NameReq):
    node = _node()
    name = body.name.strip()
    with nav_bridge.locations_lock():
        locs = nav_bridge.load_locations()
    if name not in locs:
        raise HTTPException(status_code=404, detail=f"'{name}' 위치가 등록되지 않았습니다")
    p = locs[name]
    ok = node.send_goal(float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
    return {"success": ok, "name": name}


@router.post("/mission/start")
def mission_start(body: MissionReq):
    node = _node()
    names = body.names
    if not names:
        with nav_bridge.locations_lock():
            names = sorted(nav_bridge.load_locations().keys())
    ok = node.start_mission(names, body.loop)
    return {"success": ok, "names": names, "loop": body.loop}


@router.post("/mission/stop")
def mission_stop():
    node = _node()
    node.stop_mission()
    node._set_mission("stopped", None)
    return {"success": True}


@router.post("/home")
def home():
    ok = _node().go_home()
    return {"success": ok}


@router.post("/schedule/start")
def schedule_start(body: ScheduleReq):
    node = _node()
    names = body.names
    if not names:
        with nav_bridge.locations_lock():
            names = sorted(nav_bridge.load_locations().keys())
    ok = node.start_schedule(body.minutes, names, body.loop)
    return {"success": ok, "minutes": body.minutes, "names": names}


@router.post("/schedule/stop")
def schedule_stop():
    node = _node()
    node.stop_schedule()
    return {"success": True}
