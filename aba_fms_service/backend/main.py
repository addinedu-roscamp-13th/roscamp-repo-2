"""ABA FMS Service (aba_fms_service) — 사서 관제 백엔드 (walking-skeleton).

엣지:
  1b  librarian → aba_fms       HTTP  GET /api/control/state
  2b  aba_fms → librarian       WS    /api/control/ws/state
   4  aba_fms → ABA DB          TCP   GET /api/db/ping (SELECT 1)
   5  aba_server → aba_fms      ROS2  /fms/task_request (ros_server)
   7  ai_service → aba_fms      TCP   perception 결과 수신(:9010) → GET /api/perception/last
"""
import json
import os
import re
import socket
import threading
import time

from fastapi import FastAPI, WebSocket

from ros_server import RosServer

app = FastAPI(title="aba_fms_service (ABA FMS Service)")
_last_perception: dict = {"received": False}
FMS_TCP_PORT = int(os.environ.get("FMS_TCP_PORT", "9010"))


@app.on_event("startup")
def _startup() -> None:
    RosServer()                       # 엣지 5 서버측
    _start_perception_tcp(FMS_TCP_PORT)  # 엣지 7 수신측


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "aba_fms_service"}


@app.get("/api/control/state")
def control_state() -> dict:
    return {"robots": [{"id": "pinky1", "available": True, "pose": [0, 0, 0],
                        "battery": 88, "mode": "idle", "task": None}], "tasks": []}


@app.get("/api/db/ping")
def db_ping() -> dict:
    return _select_one()


@app.get("/api/perception/last")
def perception_last() -> dict:
    return _last_perception


@app.websocket("/api/control/ws/state")
async def ws_state(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json({"robot_id": "pinky1", "state": "idle", "battery": 88})
    await ws.close()


def _start_perception_tcp(port: int) -> None:
    """ai_service 로부터 perception 결과를 TCP 로 수신(엣지 7)."""
    def serve() -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen()
        print(f"[aba_fms] perception TCP listen :{port}", flush=True)
        while True:
            conn, addr = s.accept()
            data = conn.recv(65536)
            conn.close()
            try:
                payload = json.loads(data.decode())
            except Exception:  # noqa: BLE001
                payload = {"raw_bytes": len(data)}
            _last_perception.clear()
            _last_perception.update({"received": True, "from": addr[0],
                                     "payload": payload, "ts": time.time()})
            print(f"[aba_fms] perception recv from {addr[0]}: {payload.get('predictions')}", flush=True)

    threading.Thread(target=serve, daemon=True).start()


def _select_one() -> dict:
    url = os.environ.get("DATABASE_URL", "")
    m = re.match(r"mysql\+(?:pymysql|aiomysql)://([^:]+):([^@]*)@([^:/]+):(\d+)/(\w+)", url)
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
                "prefix": "rc_*", "ts": time.time()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "engine": "mariadb", "detail": f"{type(e).__name__}: {e}"}
