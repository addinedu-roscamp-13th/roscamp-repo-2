"""내비 그래프(waypoint.yaml: vertices + lanes) 저장소.

pinky_navigation 패키지의 params/waypoint.yaml 은 colcon build 때마다
install/ 이 덮어써지므로(rebuild 손실), locations.py 와 동일하게 홈
디렉토리(~/.pinky/waypoint.yaml)에서 읽고 쓴다. 최초 1회, 홈에 파일이
없으면 패키지의 기본 waypoint.yaml로 시드한다.

저장 형식: {"vertices": {name: {x,y,yaw}}, "lanes": [{from,to,bidirectional}]}
"""
from __future__ import annotations

import heapq
import math
import os
import threading

import yaml

WP_FILE = os.environ.get(
    "PINKY_WAYPOINTS",
    os.path.expanduser("~/.pinky/waypoint.yaml"),
)

_lock = threading.Lock()


def _default_seed_path() -> str | None:
    try:
        from ament_index_python.packages import get_package_share_directory
        path = os.path.join(
            get_package_share_directory("pinky_navigation"), "params", "waypoint.yaml"
        )
        return path if os.path.isfile(path) else None
    except Exception:
        return None


def _empty() -> dict:
    return {"vertices": {}, "lanes": []}


def load() -> dict:
    """{"vertices": {...}, "lanes": [...]} 반환. 홈에 없으면 패키지 기본값으로 시드."""
    if not os.path.isfile(WP_FILE):
        seed = _default_seed_path()
        if seed:
            try:
                with open(seed, "r") as f:
                    data = yaml.safe_load(f) or {}
                save({
                    "vertices": data.get("vertices") or {},
                    "lanes": data.get("lanes") or [],
                })
            except Exception as e:
                print(f"[waypoints] 기본값 시드 실패: {e}")
    try:
        with open(WP_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return _empty()
            return {
                "vertices": data.get("vertices") or {},
                "lanes": data.get("lanes") or [],
            }
    except FileNotFoundError:
        return _empty()
    except Exception as e:
        print(f"[waypoints] load error: {e}")
        return _empty()


def save(graph: dict) -> None:
    os.makedirs(os.path.dirname(WP_FILE), exist_ok=True)
    payload = {
        "vertices": graph.get("vertices") or {},
        "lanes": graph.get("lanes") or [],
    }
    with open(WP_FILE, "w") as f:
        yaml.safe_dump(payload, f, default_flow_style=False, sort_keys=True, allow_unicode=True)


def get_graph() -> dict:
    with _lock:
        return load()


def set_graph(vertices: dict, lanes: list) -> dict:
    graph = {"vertices": vertices, "lanes": lanes}
    with _lock:
        save(graph)
    return graph


def set_vertex(name: str, x: float, y: float, yaw: float) -> dict:
    entry = {"x": round(x, 4), "y": round(y, 4), "yaw": round(yaw, 4)}
    with _lock:
        graph = load()
        graph["vertices"][name] = entry
        save(graph)
    return entry


def rename_vertex(old_name: str, new_name: str) -> bool:
    with _lock:
        graph = load()
        if old_name not in graph["vertices"] or old_name == new_name:
            return False
        graph["vertices"][new_name] = graph["vertices"].pop(old_name)
        for lane in graph["lanes"]:
            if lane.get("from") == old_name:
                lane["from"] = new_name
            if lane.get("to") == old_name:
                lane["to"] = new_name
        save(graph)
        return True


def delete_vertex(name: str) -> bool:
    with _lock:
        graph = load()
        if name not in graph["vertices"]:
            return False
        del graph["vertices"][name]
        graph["lanes"] = [
            ln for ln in graph["lanes"] if ln.get("from") != name and ln.get("to") != name
        ]
        save(graph)
        return True


def set_lane(from_name: str, to_name: str, bidirectional: bool) -> None:
    with _lock:
        graph = load()
        for lane in graph["lanes"]:
            if {lane.get("from"), lane.get("to")} == {from_name, to_name}:
                lane["from"], lane["to"], lane["bidirectional"] = from_name, to_name, bidirectional
                save(graph)
                return
        graph["lanes"].append({"from": from_name, "to": to_name, "bidirectional": bidirectional})
        save(graph)


def nearest_vertex(x: float, y: float, vertices: dict | None = None) -> str | None:
    """(x, y)에 가장 가까운 노드 이름. 노드가 없으면 None."""
    v = vertices if vertices is not None else load()["vertices"]
    if not v:
        return None
    return min(v, key=lambda n: math.hypot(v[n]["x"] - x, v[n]["y"] - y))


def route(start_name: str, goal_name: str, graph: dict | None = None) -> list[str] | None:
    """간선(lane)만 따라가는 최단경로 — 다익스트라. [start,...,goal] 또는 못 찾으면 None.
    격자식(축정렬, 대각선 금지) 그래프이므로 직선 최단거리가 아니라 항상 간선을 탄다."""
    g = graph if graph is not None else load()
    vertices, lanes = g["vertices"], g["lanes"]
    if start_name not in vertices or goal_name not in vertices:
        return None
    if start_name == goal_name:
        return [start_name]

    adj: dict[str, list[str]] = {n: [] for n in vertices}
    for ln in lanes:
        a, b = ln.get("from"), ln.get("to")
        if a not in vertices or b not in vertices:
            continue
        adj[a].append(b)
        if ln.get("bidirectional", True):
            adj[b].append(a)

    def dist(a: str, b: str) -> float:
        return math.hypot(vertices[a]["x"] - vertices[b]["x"], vertices[a]["y"] - vertices[b]["y"])

    dists = {start_name: 0.0}
    prev: dict[str, str] = {}
    visited = set()
    heap = [(0.0, start_name)]
    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        if u == goal_name:
            break
        for v in adj[u]:
            nd = d + dist(u, v)
            if nd < dists.get(v, float("inf")):
                dists[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if goal_name not in dists:
        return None
    path = [goal_name]
    while path[-1] != start_name:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def delete_lane(from_name: str, to_name: str) -> bool:
    with _lock:
        graph = load()
        before = len(graph["lanes"])
        graph["lanes"] = [
            ln for ln in graph["lanes"]
            if {ln.get("from"), ln.get("to")} != {from_name, to_name}
        ]
        if len(graph["lanes"]) == before:
            return False
        save(graph)
        return True
