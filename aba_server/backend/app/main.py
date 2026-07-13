"""ABA Service (aba_server) — 회원 모바일 웹 백엔드 (walking-skeleton).

엣지:
  1a  member → aba_server        HTTP  POST /api/control/goto  (이송 접수)
   5  aba_server → aba_fms       ROS2  /fms/task_request       (goto 내부에서 호출)
  2a  aba_server → member        WS    /api/control/ws/state
   3  aba_server → ABA DB        TCP   GET /api/db/ping (SELECT 1)
"""
import os
import re
import time
import uuid

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .ros_client import RosClient

app = FastAPI(title="aba_server (ABA Service)")
_ros: RosClient | None = None


class GotoReq(BaseModel):
    name: str = "unknown"


@app.on_event("startup")
def _startup() -> None:
    global _ros
    _ros = RosClient()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "aba_server"}


@app.post("/api/control/goto")
def goto(body: GotoReq):
    """이송 접수 → FMS 에 ROS2 AssignTask(/fms/task_request) → task_id 반환."""
    res = _ros.call_task_request() if _ros else None
    if res is None:
        return JSONResponse(status_code=503,
                            content={"accepted": False, "reason": "fms ROS2 service unavailable"})
    return {"accepted": bool(res.success), "task_id": uuid.uuid4().hex[:8],
            "target": body.name, "fms_msg": res.message}


@app.get("/api/db/ping")
def db_ping() -> dict:
    return _select_one()


@app.websocket("/api/control/ws/state")
async def ws_state(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json({"robot_id": "pinky1", "state": "idle", "battery": 88, "task_id": None})
    await ws.close()


def _select_one() -> dict:
    """MariaDB SELECT 1 — 서버↔DB(TCP) 연동 확인. 미가용 시 ok=false + 사유."""
    url = os.environ.get("DATABASE_URL", "")
    m = re.match(r"mysql\+pymysql://([^:]+):([^@]*)@([^:/]+):(\d+)/(\w+)", url)
    if not m:
        return {"ok": False, "engine": "mariadb", "detail": "DATABASE_URL 미설정/형식오류"}
    user, pw, host, port, db = m.groups()
    try:
        import pymysql
        conn = pymysql.connect(host=host, port=int(port), user=user, password=pw,
                               database=db, connect_timeout=3)
        with conn.cursor() as c:
            c.execute("SELECT 1")
            row = c.fetchone()
        conn.close()
        return {"ok": True, "engine": "mariadb", "detail": f"SELECT 1 -> {row[0]}",
                "prefix": "cb_*", "ts": time.time()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "engine": "mariadb", "detail": f"{type(e).__name__}: {e}"}
