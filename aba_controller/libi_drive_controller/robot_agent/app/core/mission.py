"""미션(순찰/경유)·스케줄 순찰 엔진.

nav2_web_server.py(Flask)의 mission/schedule thread 엔진을 이전한 모듈.

- ros_bridge.nav_to() (NavigateToPose 완료대기)를 사용해 등록된 명명 위치를 순회한다.
- 미션 워커/스케줄 워커는 각각 별도 daemon thread 로 돌고 threading.Event 로 중단한다.
- 상태는 _lock 으로 보호하며 get_status() 로 스냅샷을 읽는다.
"""
from __future__ import annotations

import threading

from app.core import locations, ros_bridge

_lock = threading.Lock()

# 미션 상태
_status = "idle"          # idle/running/done/failed/stopped
_current: str | None = None  # 현재 향하는 구역
_names: list[str] = []       # 미션 구역 순서
_loop = False

_mission_thread: threading.Thread | None = None
_mission_stop = threading.Event()

# 스케줄
_schedule_thread: threading.Thread | None = None
_schedule_stop = threading.Event()
_schedule_minutes = 0


def get_status() -> dict:
    with _lock:
        return {
            "status": _status,
            "current": _current,
            "names": list(_names),
            "loop": _loop,
            "schedule_minutes": _schedule_minutes,
        }


def _set(status: str | None = None, current: str | None = ...) -> None:
    global _status, _current
    with _lock:
        if status is not None:
            _status = status
        if current is not ...:
            _current = current


# ── 미션(순찰/경유) ─────────────────────────────────────────
#: 첫 지점은 nav2 기동을 기다려 준다.
#  pi.sh 로 콜드부팅하면 nav2 창이 `/scan` 을 기다렸다가 라이프사이클을 올리느라 30초가
#  넘게 걸린다. 그 전에 미션이 시작되면 첫 goal 이 "액션서버 없음"으로 즉시 실패했다.
FIRST_GOAL_WAIT_SEC = 60.0
#: 한 지점을 몇 번까지 다시 시도할지. 0 이면 예전처럼 한 번 실패에 미션 종료.
POINT_RETRIES = 2
#: 재시도 간격(초).
RETRY_GAP_SEC = 2.0


def _mission_worker(names: list[str], loop: bool) -> None:
    _set("running", None)
    first = True
    skipped = 0          # 재시도까지 실패해서 건너뛴 지점 수
    while not _mission_stop.is_set():
        for nm in names:
            if _mission_stop.is_set():
                break
            p = locations.get(nm)
            if p is None:
                continue
            _set("running", nm)

            # 실패해도 바로 미션을 죽이지 않는다.
            # 예전엔 `if not ok: _set("failed"); return` 이라 **한 번 실패에 워커 스레드가
            # 그 자리에서 끝났다.** 부팅 직후 nav2 가 아직 없거나 한 지점에서 경로를 못 찾으면
            # 순찰 전체가 A 에서 멈춘 채 고정됐다(관제엔 계속 failed/A). 경로 드라이버
            # (path_request_driver)는 이미 쿨다운 재시도를 하는데 여기만 방어가 없었다.
            ok = False
            for attempt in range(POINT_RETRIES + 1):
                if _mission_stop.is_set():
                    break
                ok = ros_bridge.nav_to(
                    float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)),
                    stop_event=_mission_stop,
                    wait_action_sec=FIRST_GOAL_WAIT_SEC if first else None,
                )
                first = False
                if ok or _mission_stop.is_set():
                    break
                if attempt < POINT_RETRIES:
                    _mission_stop.wait(RETRY_GAP_SEC)

            if _mission_stop.is_set():
                break
            if not ok:
                # 이 지점은 포기하고 **다음 지점으로 넘어간다.** 순찰은 계속돼야 한다.
                # 대신 건너뛴 사실을 기억했다가 끝에서 failed 로 보고한다 — 조용히 done 이
                # 되면 "다 돌았다"고 오해한다.
                skipped += 1

        if not loop:
            break
    if _mission_stop.is_set():
        _set("stopped", None)
    else:
        _set("done" if skipped == 0 else "failed", None)


def start_mission(names: list[str], loop: bool) -> bool:
    global _mission_thread, _names, _loop
    stop_mission()
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names:
        return False
    with _lock:
        _names = names
        _loop = bool(loop)
    _mission_stop.clear()
    _mission_thread = threading.Thread(
        target=_mission_worker, args=(names, bool(loop)), daemon=True)
    _mission_thread.start()
    return True


def stop_mission() -> None:
    global _mission_thread
    _mission_stop.set()
    ros_bridge.cancel_nav()
    t = _mission_thread
    if t is not None and t.is_alive():
        t.join(timeout=2.0)
    _mission_thread = None


def go_home() -> bool:
    """'HOME' 구역이 등록돼 있으면 그곳으로, 없으면 맵 원점(0,0)으로."""
    stop_mission()
    p = locations.get("HOME")
    if p is not None:
        return ros_bridge.send_nav_goal(
            float(p["x"]), float(p["y"]), float(p.get("yaw", 0.0)))
    return ros_bridge.send_nav_goal(0.0, 0.0, 0.0)


# ── 스케줄 순찰 ─────────────────────────────────────────────
def _schedule_worker(minutes: int, names: list[str], loop: bool) -> None:
    interval = max(1, int(minutes)) * 60
    while not _schedule_stop.is_set():
        if _schedule_stop.wait(timeout=interval):
            break
        start_mission(names, loop)


def start_schedule(minutes: int, names: list[str], loop: bool) -> bool:
    global _schedule_thread, _schedule_minutes
    stop_schedule()
    names = [str(n).strip() for n in names if str(n).strip()]
    if not names or int(minutes) < 1:
        return False
    with _lock:
        _schedule_minutes = int(minutes)
    _schedule_stop.clear()
    _schedule_thread = threading.Thread(
        target=_schedule_worker, args=(int(minutes), names, bool(loop)), daemon=True)
    _schedule_thread.start()
    return True


def stop_schedule() -> None:
    global _schedule_thread, _schedule_minutes
    _schedule_stop.set()
    t = _schedule_thread
    if t is not None and t.is_alive():
        t.join(timeout=2.0)
    _schedule_thread = None
    with _lock:
        _schedule_minutes = 0
