"""
자율 탐색 (Frontier Exploration) 엔진.

1. /map에서 OccupancyGrid 읽기
2. 프론티어 셀 찾기 (free 셀에 인접한 unknown=-1 셀)
3. 0.5m 그리드로 클러스터링 → 중심점 목록
4. 로봇과 가장 가까운 클러스터 중심 → Nav2 목표
5. 0.4m 이내 도달 or 45초 타임아웃 → 다음 목표
6. 프론티어 없음 → 탐색 완료
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any

from . import ros_bridge

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "status": "idle",       # idle | running | done | error | stopped
    "frontiers": [],        # [{x, y, size}] world coords
    "current_goal": None,   # {x, y} | None
    "explored_count": 0,
    "message": "",
}

_stop_event = threading.Event()


def get_state() -> dict:
    with _lock:
        return dict(_state)


def start() -> bool:
    """탐색 시작. 이미 실행 중이면 False."""
    with _lock:
        if _state["running"]:
            return False
        _state.update({
            "running": True,
            "status": "running",
            "frontiers": [],
            "current_goal": None,
            "explored_count": 0,
            "message": "탐색 시작 중...",
        })
    _stop_event.clear()
    t = threading.Thread(target=_explore_loop, daemon=True, name="explorer")
    t.start()
    return True


def stop() -> None:
    _stop_event.set()
    ros_bridge.publish_cmd_vel(0.0, 0.0)
    with _lock:
        _state.update({
            "running": False,
            "status": "stopped",
            "message": "사용자가 탐색을 중지했습니다.",
            "current_goal": None,
        })


def _find_frontiers(map_data: dict) -> list[dict]:
    """OccupancyGrid에서 프론티어 클러스터 목록 반환."""
    width = map_data["width"]
    height = map_data["height"]
    resolution = map_data["resolution"]
    origin_x = map_data["origin_x"]
    origin_y = map_data["origin_y"]
    data = map_data["data"]

    FREE_MAX = 25      # 0~25 = free (nav2 convention)
    CLUSTER_M = 0.5   # cluster resolution in meters

    clusters: dict[tuple, list[tuple[float, float]]] = {}

    for r in range(1, height - 1):
        for c in range(1, width - 1):
            if data[r * width + c] != -1:
                continue
            # Is any 4-directional neighbor free?
            has_free = any(
                0 <= data[(r + dr) * width + (c + dc)] <= FREE_MAX
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
            )
            if not has_free:
                continue
            wx = origin_x + (c + 0.5) * resolution
            wy = origin_y + (r + 0.5) * resolution
            key = (int(wx / CLUSTER_M), int(wy / CLUSTER_M))
            clusters.setdefault(key, []).append((wx, wy))

    result = []
    for pts in clusters.values():
        if len(pts) < 3:   # noise filter: skip tiny clusters
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        result.append({"x": cx, "y": cy, "size": len(pts)})
    return result


def _explore_loop() -> None:
    REACH_DIST = 0.4   # m — 목표 도달 판정 거리
    GOAL_TIMEOUT = 45  # s — 이 시간 안에 못 도달하면 다음 목표
    NEAR_SKIP = 0.35   # m — 이미 가까운 프론티어는 건너뜀
    LOOP_SLEEP = 0.5

    try:
        while not _stop_event.is_set():
            map_data = ros_bridge.get_topic("map")
            odom = ros_bridge.get_topic("odom")

            if map_data is None or odom is None:
                with _lock:
                    _state["message"] = "맵/오도메트리 대기 중..."
                time.sleep(1.0)
                continue

            frontiers = _find_frontiers(map_data)
            with _lock:
                _state["frontiers"] = frontiers

            if not frontiers:
                with _lock:
                    _state.update({
                        "running": False,
                        "status": "done",
                        "current_goal": None,
                        "message": f"탐색 완료! 총 {_state['explored_count']}개 목표 방문",
                    })
                return

            rx, ry = odom["x"], odom["y"]

            def dist(f: dict) -> float:
                return math.sqrt((f["x"] - rx) ** 2 + (f["y"] - ry) ** 2)

            # Skip frontiers that are already very close (already explored)
            reachable = [f for f in frontiers if dist(f) >= NEAR_SKIP]
            if not reachable:
                reachable = frontiers  # fallback: try all

            best = min(reachable, key=dist)
            goal = {"x": best["x"], "y": best["y"]}

            with _lock:
                _state["current_goal"] = goal
                _state["message"] = f"목표로 이동 중: ({goal['x']:.2f}, {goal['y']:.2f})"

            ros_bridge.publish_nav_goal(goal["x"], goal["y"], 0.0)

            # Wait for arrival or timeout
            t0 = time.time()
            reached = False
            while not _stop_event.is_set() and time.time() - t0 < GOAL_TIMEOUT:
                odom = ros_bridge.get_topic("odom")
                if odom:
                    d = math.sqrt((odom["x"] - goal["x"]) ** 2 + (odom["y"] - goal["y"]) ** 2)
                    if d < REACH_DIST:
                        reached = True
                        break
                time.sleep(LOOP_SLEEP)

            if _stop_event.is_set():
                break

            with _lock:
                _state["explored_count"] += 1
                suffix = f" (누적: {_state['explored_count']}개)"
                _state["message"] = ("도달!" if reached else f"타임아웃 ({GOAL_TIMEOUT}s)") + suffix

            time.sleep(1.0)  # 짧은 정지 후 다음 목표

    except Exception as e:
        with _lock:
            _state.update({
                "running": False,
                "status": "error",
                "current_goal": None,
                "message": f"오류: {e}",
            })
