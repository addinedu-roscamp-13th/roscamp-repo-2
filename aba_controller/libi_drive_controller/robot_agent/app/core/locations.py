"""명명 위치(A~E 등) 저장소.

nav2_web_server.py(Flask)의 load_locations/save_locations 를 이전한 모듈.

- rebuild(install 덮어쓰기)에도 유지되도록 홈 디렉토리(~/.pinky/locations.yaml)에 저장한다.
- PINKY_LOCATIONS 환경변수로 경로를 변경할 수 있다.
- 저장 형식: {'A': {'x':.., 'y':.., 'yaw':..}, ...}
"""
from __future__ import annotations

import os
import threading

import yaml

LOC_FILE = os.environ.get(
    "PINKY_LOCATIONS",
    os.path.expanduser("~/.pinky/locations.yaml"),
)

_lock = threading.Lock()


def load() -> dict:
    """{'A': {'x':..,'y':..,'yaw':..}, ...} 형태 dict 반환."""
    try:
        with open(LOC_FILE, "r") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[locations] load error: {e}")
        return {}


def save(locs: dict) -> None:
    os.makedirs(os.path.dirname(LOC_FILE), exist_ok=True)
    with open(LOC_FILE, "w") as f:
        yaml.safe_dump(
            locs, f, default_flow_style=False, sort_keys=True, allow_unicode=True
        )


def get(name: str) -> dict | None:
    with _lock:
        return load().get(name)


def set_location(name: str, x: float, y: float, yaw: float) -> dict:
    entry = {"x": round(x, 4), "y": round(y, 4), "yaw": round(yaw, 4)}
    with _lock:
        locs = load()
        locs[name] = entry
        save(locs)
    return entry


def delete(name: str) -> bool:
    with _lock:
        locs = load()
        if name in locs:
            del locs[name]
            save(locs)
            return True
    return False


def names_sorted() -> list[str]:
    with _lock:
        return sorted(load().keys())
