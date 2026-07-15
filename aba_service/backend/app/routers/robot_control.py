import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RobotControlLog

router = APIRouter(prefix="/api/robot", tags=["robot-control"])

class RobotCommandReq(BaseModel):
    user_message: str
    robot_type: str  # "mobile" | "arm"
    action: str      # e.g., "move", "stop", "angles", etc.
    parameters: Optional[Dict[str, Any]] = None

ROBOT_SERVER_URL = "http://127.0.0.1:9001"

def _call_robot_api(path: str, method: str = "POST", data: Optional[dict] = None) -> tuple[bool, str]:
    url = f"{ROBOT_SERVER_URL}{path}"
    try:
        payload = json.dumps(data or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload if method == "POST" else None,
            headers={"Content-Type": "application/json"} if method == "POST" else {},
            method=method
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            res_body = response.read().decode("utf-8")
            return True, res_body
    except urllib.error.HTTPError as e:
        try:
            err_msg = e.read().decode("utf-8")
        except Exception:
            err_msg = e.reason
        return False, f"HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, str(e)

@router.post("/execute")
def execute_command(req: RobotCommandReq, db: Session = Depends(get_db)):
    # 1. Log to DB as pending
    log_entry = RobotControlLog(
        user_message=req.user_message,
        robot_type=req.robot_type,
        action=req.action,
        parameters=json.dumps(req.parameters or {}),
        status="pending"
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    # 2. Map command to robot server endpoint
    success = False
    response_text = ""
    path = ""
    body = {}

    if req.robot_type == "mobile":
        if req.action == "move":
            path = "/api/admin/robot/motor/move"
            body = {
                "left": int(req.parameters.get("left", 0)),
                "right": int(req.parameters.get("right", 0)),
                "duration": float(req.parameters.get("duration", 0.5))
            }
        elif req.action == "stop":
            path = "/api/admin/robot/motor/stop"
        elif req.action == "emotion":
            path = "/api/admin/robot/lcd/emotion"
            body = {"emotion": req.parameters.get("emotion", "basic")}
        elif req.action == "text":
            path = "/api/admin/robot/lcd/text"
            body = {
                "text": req.parameters.get("text", ""),
                "font_name": req.parameters.get("font_name", "default"),
                "font_size": int(req.parameters.get("font_size", 24)),
                "color": req.parameters.get("color", "#ffffff"),
                "bg_color": req.parameters.get("bg_color", "#000000"),
                "align": req.parameters.get("align", "center"),
                "scroll": bool(req.parameters.get("scroll", False)),
                "scroll_speed": int(req.parameters.get("scroll_speed", 3))
            }
        elif req.action == "buzzer":
            path = "/api/admin/robot/buzzer"
            body = {
                "preset": req.parameters.get("preset", "bell"),
                "count": int(req.parameters.get("count", 1)),
                "freq": int(req.parameters.get("freq", 1000)),
                "duration": float(req.parameters.get("duration", 0.2))
            }
    elif req.robot_type == "arm":
        if req.action == "home":
            path = "/api/arm/home"
        elif req.action == "stop":
            path = "/api/arm/stop"
        elif req.action == "jog-stop":
            path = "/api/arm/jog-stop"
        elif req.action == "angles":
            path = "/api/arm/angles"
            body = {
                "angles": req.parameters.get("angles", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                "speed": int(req.parameters.get("speed", 50))
            }
        elif req.action == "gripper":
            path = "/api/arm/gripper"
            body = {
                "angle": int(req.parameters.get("angle", 0))
            }
        elif req.action == "camera-view":
            path = "/api/arm/camera-view"
            body = {"preset": int(req.parameters.get("preset", 1))}
        elif req.action == "face-view":
            path = "/api/arm/face-view"
        elif req.action == "color-pick":
            path = "/api/arm/color-pick"
            body = {"color": req.parameters.get("color", "red")}
        elif req.action == "face-track":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/face-track/{'start' if start else 'stop'}"
        elif req.action == "gesture":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/gesture/{'start' if start else 'stop'}"
        elif req.action == "barcode-qr":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/barcode-qr/{'start' if start else 'stop'}"
        elif req.action == "classify":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/classify/{'start' if start else 'stop'}"
        elif req.action == "ocr":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/ocr/{'start' if start else 'stop'}"

    if path:
        success, response_text = _call_robot_api(path, method="POST", data=body)
    else:
        success = False
        response_text = f"Unknown action: {req.action} for robot type: {req.robot_type}"

    # 3. Update DB entry status
    log_entry.status = "success" if success else "failed"
    if not success:
        log_entry.error_message = response_text[:500]
    db.commit()

    return {
        "id": log_entry.id,
        "success": success,
        "response": response_text
    }

@router.get("/history")
def get_history(limit: int = 50, db: Session = Depends(get_db)):
    stmt = select(RobotControlLog).order_by(RobotControlLog.created_at.desc()).limit(limit)
    rows = db.scalars(stmt).all()
    result = []
    for r in rows:
        try:
            params = json.loads(r.parameters) if r.parameters else {}
        except Exception:
            params = {}
        result.append({
            "id": r.id,
            "user_message": r.user_message,
            "robot_type": r.robot_type,
            "action": r.action,
            "parameters": params,
            "status": r.status,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    return result
