"""주행로봇 근접 안전 코디네이터 (Phase 1).

중앙서버가 활성 주행로봇(pinky)들의 위치(pose)를 주기적으로 폴링해,
두 로봇이 지정 거리보다 가까워지면 우선순위가 낮은(또는 접근 중인) 로봇을
자동으로 정지(mission/stop)시킨다.

원칙:
  - 자동 "정지"만 수행한다(안전 방향). "재개(주행)"는 사람이 fleet 페이지에서 수동으로.
  - 브라우저와 무관하게 서버 백그라운드에서 상시 동작한다.
  - 실시간 최종 방어선은 각 로봇 온보드 센서 회피이며, 여기는 "미리 떼어놓기" 역할.

전제:
  - 로봇 pose(x,y)는 **공통(같은) 맵 프레임** 기준이어야 거리 비교가 유효하다.
    (같은 공간에서 하나의 공유 맵으로 로컬라이즈하는 구성을 가정)
  - 각 로봇 pose 는 이 프레임이 다르면 거리 판정이 무의미하므로 UI 에 경고를 노출한다.

우선순위: robot_id 가 작을수록 높음(계속 주행), 큰 쪽이 양보(정지).
"""
from __future__ import annotations

import asyncio
import json
import math
import socket
import time
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import select

from app import fleet_telemetry
from app.database import AdminSessionLocal
from app.models import Robot

STATE_TIMEOUT = 2.0
MOVE_EPS = 0.02  # m — 틱 사이 이동량이 이보다 크면 "이동 중"으로 간주


def _robot_state(ip: str, base: str) -> Any | None:
    """[2026-07-08] ROS 텔레메트리 캐시 우선 — 1초 HTTP 폴링 제거. stale 시에만 HTTP 폴백."""
    state = fleet_telemetry.get_state(ip)
    if state is not None:
        return state
    return _fetch_json(base, "/api/state")


def _stop_robot(ip: str, base: str) -> None:
    """근접 자동 정지 — ROS 명령 우선, 링크 불가 시 HTTP 폴백."""
    res = fleet_telemetry.send_command(ip, "mission_stop", {}, timeout=2.0)
    if res is None:
        _fetch_json(base, "/api/mission/stop", "POST", {})


def _fetch_json(base: str, path: str, method: str = "GET", payload: dict | None = None) -> Any | None:
    """로봇 에이전트 HTTP 호출. 실패 시 예외 대신 None 반환(백그라운드용)."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=STATE_TIMEOUT) as res:
            raw = res.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, OSError):
        return None


class FleetCoordinator:
    def __init__(self) -> None:
        # 설정 (기본 OFF — 운영자가 fleet 페이지에서 켠다)
        self.enabled: bool = False
        self.min_distance: float = 0.6   # m — 이보다 가까우면 정지
        self.clear_distance: float = 0.9  # m — 이보다 멀어지면 재개 허용(히스테리시스)
        self.interval: float = 1.0        # s — 폴링 주기

        # 운영자 지정 우선순위: robot_id -> 순위값(작을수록 높음). 미지정 로봇은 robot_id 를 순위로 사용.
        self.priorities: dict[int, int] = {}

        # 런타임 상태
        self.robots: dict[int, dict[str, Any]] = {}   # id -> snapshot
        self.holds: dict[int, dict[str, Any]] = {}    # id -> {reason, peer_id, peer_name, since}
        self.last_tick: float = 0.0
        self.last_error: str | None = None
        self._last_pose: dict[int, tuple[float, float]] = {}

    def priority_of(self, robot_id: int) -> int:
        return self.priorities.get(robot_id, robot_id)

    # ── 설정 ────────────────────────────────────────────────
    def update_config(self, *, enabled: bool | None = None, min_distance: float | None = None, clear_distance: float | None = None, priorities: dict[int, int] | None = None) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        if min_distance is not None:
            self.min_distance = max(0.1, float(min_distance))
        if clear_distance is not None:
            self.clear_distance = float(clear_distance)
        if priorities is not None:
            self.priorities = {int(k): int(v) for k, v in priorities.items()}
        # clear_distance 는 항상 min_distance 이상이 되도록 보정
        if self.clear_distance < self.min_distance:
            self.clear_distance = self.min_distance + 0.2

    def config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_distance": self.min_distance,
            "clear_distance": self.clear_distance,
            "interval": self.interval,
            "priorities": self.priorities,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.config(),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "robots": list(self.robots.values()),
            "holds": self.holds,
        }

    def resume(self, robot_id: int) -> dict[str, Any]:
        """수동 재개(정지 해제). 아직 근접(재개 불가) 상태면 거부."""
        hold = self.holds.get(robot_id)
        if hold is None:
            return {"success": True, "robot_id": robot_id, "msg": "정지 상태가 아닙니다."}
        snap = self.robots.get(robot_id)
        if snap and not snap.get("resumable", False):
            return {"success": False, "robot_id": robot_id, "msg": "아직 다른 로봇과 근접 상태라 재개할 수 없습니다."}
        self.holds.pop(robot_id, None)
        if snap:
            snap["held"] = False
        return {"success": True, "robot_id": robot_id, "msg": "정지를 해제했습니다. 콘솔에서 주행을 다시 시작하세요."}

    # ── 백그라운드 루프 ─────────────────────────────────────
    async def run(self) -> None:
        while True:
            try:
                if self.enabled:
                    await self._tick()
            except Exception as exc:  # noqa: BLE001 — 루프는 죽지 않아야 한다
                self.last_error = repr(exc)
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        async with AdminSessionLocal() as db:
            rows = (
                await db.execute(
                    select(Robot).where(Robot.robot_type == "pinky", Robot.is_active == True)  # noqa: E712
                )
            ).scalars().all()
            robots = [(r.id, r.name, r.ip_address, f"http://{r.ip_address}:{r.port}") for r in rows]

        # 각 로봇 상태 병렬 조회 (ROS 캐시 우선, stale 시 HTTP 폴백)
        results = await asyncio.gather(*[asyncio.to_thread(_robot_state, ip, base) for _, _, ip, base in robots])

        now = time.time()
        snap: dict[int, dict[str, Any]] = {}
        for (rid, name, ip, base), state in zip(robots, results):
            online = state is not None
            pose = (state or {}).get("pose") if online else None
            status = ((state or {}).get("mission") or {}).get("status") if online else None
            has_pose = bool(pose) and pose.get("x") is not None
            xy = (float(pose["x"]), float(pose["y"])) if has_pose else None

            moving = status == "running"
            if xy is not None and rid in self._last_pose:
                px, py = self._last_pose[rid]
                if math.hypot(xy[0] - px, xy[1] - py) > MOVE_EPS:
                    moving = True
            if xy is not None:
                self._last_pose[rid] = xy

            snap[rid] = {
                "id": rid, "name": name, "base": base, "ip": ip,
                "online": online, "pose": ({"x": xy[0], "y": xy[1]} if xy else None),
                "moving": moving, "status": status,
                "priority": self.priority_of(rid),
                "near_id": None, "near_name": None, "near_dist": None,
                "held": rid in self.holds, "resumable": False,
            }

        # 더 이상 없는 로봇의 hold 정리
        for rid in list(self.holds.keys()):
            if rid not in snap:
                self.holds.pop(rid, None)
                self._last_pose.pop(rid, None)

        ids = list(snap.keys())
        # 쌍별 최소 거리 계산
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                ia, ib = ids[a], ids[b]
                pa, pb = snap[ia]["pose"], snap[ib]["pose"]
                if not pa or not pb:
                    continue
                d = math.hypot(pa["x"] - pb["x"], pa["y"] - pb["y"])
                for i, j in ((ia, ib), (ib, ia)):
                    if snap[i]["near_dist"] is None or d < snap[i]["near_dist"]:
                        snap[i]["near_dist"] = d
                        snap[i]["near_id"] = j
                        snap[i]["near_name"] = snap[j]["name"]

                # 정지 판정: min_distance 이내면 접근 중인(또는 우선순위 낮은) 쪽을 정지
                if d <= self.min_distance:
                    mi, mj = snap[ia]["moving"], snap[ib]["moving"]
                    if mi and mj:
                        # 둘 다 이동 중 — 운영자 지정 우선순위가 낮은(값 큰) 쪽이 양보.
                        # 동순위면 robot_id 큰 쪽이 양보.
                        victim = ia if (self.priority_of(ia), ia) > (self.priority_of(ib), ib) else ib
                    elif mi:
                        victim = ia
                    elif mj:
                        victim = ib
                    else:
                        victim = None
                    if victim is not None and victim not in self.holds:
                        peer = ib if victim == ia else ia
                        await asyncio.to_thread(_stop_robot, snap[victim]["ip"], snap[victim]["base"])
                        self.holds[victim] = {
                            "reason": "근접 자동 정지",
                            "peer_id": peer, "peer_name": snap[peer]["name"],
                            "distance": round(d, 3), "since": now,
                        }
                        snap[victim]["held"] = True

        # hold 상태의 재개 가능 여부(히스테리시스)
        for rid, entry in snap.items():
            if rid in self.holds:
                nd = entry["near_dist"]
                entry["held"] = True
                entry["resumable"] = (nd is None) or (nd > self.clear_distance)

        self.robots = snap
        self.last_tick = now
        self.last_error = None


coordinator = FleetCoordinator()
