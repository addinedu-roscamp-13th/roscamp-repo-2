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
from app.models import Robot, RobotLocation

STATE_TIMEOUT = 2.0
MOVE_EPS = 0.02  # m — 틱 사이 이동량이 이보다 크면 "이동 중"으로 간주
IMMINENT_FACTOR = 0.7  # min_distance × 이 비율 이내면 '임박' — 둘 다 이동 중이면 양쪽 다 정지
EVASION_LOOKAHEAD = 1.5    # m — 이동 로봇 현재위치 기준 이 반경 안의 경로 구간만 '막힘' 판정
# 회피 목적지 구역은 이동 경로/다른 로봇에서 이만큼 떨어져야 한다. 맵이 작고(구역 간격 ~0.85m)
# 로봇이 작으므로 min_distance 가 아니라 풋프린트 기준의 작은 값을 쓴다(안 그러면 후보가 없어짐).
EVASION_CLEARANCE = 0.35
EVASION_RETURN_TIMEOUT = 30.0  # s — 원위치 복귀 지시 후 이 시간 지나면 회피 상태 정리


def _robot_state(name: str, base: str) -> Any | None:
    """[2026-07-08] ROS 텔레메트리 캐시 우선 — 1초 HTTP 폴링 제거. stale 시에만 HTTP 폴백."""
    state = fleet_telemetry.get_state_for_robot(name)
    if state is not None:
        return state
    return _fetch_json(base, "/api/state")


def _stop_robot(name: str, base: str) -> None:
    """근접 자동 정지 — ROS 명령 우선, 링크 불가 시 HTTP 폴백."""
    res = fleet_telemetry.send_command_for_robot(name, "mission_stop", {}, timeout=2.0)
    if res is None:
        _fetch_json(base, "/api/mission/stop", "POST", {})


def _move_robot(robot_name: str, base: str, name: str, coords: tuple[float, float, float]) -> None:
    """회피 이동 — 지정 구역으로 goto (ROS 우선, HTTP 폴백). coords=(x,y,yaw)."""
    x, y, yaw = coords
    loc = {"x": x, "y": y, "yaw": yaw}
    res = fleet_telemetry.send_command_for_robot(robot_name, "goto", {"name": name, "location": loc}, timeout=2.0)
    if res is None:
        _fetch_json(base, "/api/goto", "POST", {"name": name})


def _path_min_dist_ahead(b_xy: tuple[float, float], path: list[dict], m_xy: tuple[float, float], lookahead: float) -> float | None:
    """B 가 M 의 계획 경로(M 현재위치 기준 lookahead 반경 안 구간)에 얼마나 가까운지 최소거리. 해당 구간 없으면 None."""
    best: float | None = None
    for p in path:
        px, py = float(p["x"]), float(p["y"])
        if math.hypot(px - m_xy[0], py - m_xy[1]) > lookahead:
            continue
        d = math.hypot(px - b_xy[0], py - b_xy[1])
        if best is None or d < best:
            best = d
    return best


def _nearest_zone(xy: tuple[float, float], zones: dict[str, tuple[float, float, float]]) -> tuple[str, tuple[float, float, float]] | None:
    best: tuple[float, str, tuple[float, float, float]] | None = None
    for name, c in zones.items():
        d = math.hypot(c[0] - xy[0], c[1] - xy[1])
        if best is None or d < best[0]:
            best = (d, name, c)
    return (best[1], best[2]) if best else None


def _pick_evasion_zone(
    b_xy: tuple[float, float],
    zones: dict[str, tuple[float, float, float]],
    path: list[dict],
    others_xy: list[tuple[float, float]],
    clearance: float,
) -> tuple[str, tuple[float, float, float]] | None:
    """B 에서 가장 가깝고, 경로에서 clearance 이상 떨어지고, 다른 로봇과도 clearance 이상 떨어진 구역."""
    cands: list[tuple[float, str, tuple[float, float, float]]] = []
    for name, c in zones.items():
        zx, zy = c[0], c[1]
        near_path = min((math.hypot(zx - float(p["x"]), zy - float(p["y"])) for p in path), default=1e9)
        if near_path < clearance:
            continue
        if any(math.hypot(zx - ox, zy - oy) < clearance for ox, oy in others_xy):
            continue
        cands.append((math.hypot(zx - b_xy[0], zy - b_xy[1]), name, c))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    return cands[0][1], cands[0][2]


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
        # 설정 (기본 ON — 안전 방향. DB(rc_fleet_coordinator_settings)에서 startup 시 덮어쓴다)
        self.enabled: bool = True
        # 맵 스케일(구역 간격 ~0.85m)에 맞춘 값. 너무 크면 인접 구역 순회 중 계속 오정지한다.
        self.min_distance: float = 0.45  # m — 이보다 가까우면 정지
        self.clear_distance: float = 0.7  # m — 이보다 멀어지면 재개 허용(히스테리시스)
        self.interval: float = 0.4        # s — 폴링 주기(1.0→0.4: 근접 감지 지연 축소)
        # 막는 로봇을 경로 밖으로 자동 비켜세우기. 자동 주행이라 기본 OFF(명시적 opt-in).
        self.evasion_enabled: bool = False

        # 운영자 지정 우선순위: robot_id -> 순위값(작을수록 높음). 미지정 로봇은 robot_id 를 순위로 사용.
        self.priorities: dict[int, int] = {}

        # 런타임 상태
        self.robots: dict[int, dict[str, Any]] = {}   # id -> snapshot
        self.holds: dict[int, dict[str, Any]] = {}    # id -> {reason, peer_id, peer_name, since}
        self.evasions: dict[int, dict[str, Any]] = {}  # id -> {mover_id, target_zone, origin_zone, origin_coords, returning, since}
        self.last_tick: float = 0.0
        self.last_error: str | None = None
        self._last_pose: dict[int, tuple[float, float]] = {}

    def priority_of(self, robot_id: int) -> int:
        return self.priorities.get(robot_id, robot_id)

    # ── 설정 ────────────────────────────────────────────────
    def update_config(self, *, enabled: bool | None = None, min_distance: float | None = None, clear_distance: float | None = None, priorities: dict[int, int] | None = None, evasion_enabled: bool | None = None) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        if min_distance is not None:
            self.min_distance = max(0.1, float(min_distance))
        if clear_distance is not None:
            self.clear_distance = float(clear_distance)
        if priorities is not None:
            self.priorities = {int(k): int(v) for k, v in priorities.items()}
        if evasion_enabled is not None:
            self.evasion_enabled = bool(evasion_enabled)
        # clear_distance 는 항상 min_distance 이상이 되도록 보정
        if self.clear_distance < self.min_distance:
            self.clear_distance = self.min_distance + 0.2

    async def load(self) -> None:
        """DB 에 저장된 설정을 읽어 현재 설정을 덮어쓴다. 행이 없으면 현재 기본값으로 생성."""
        from app.models import FleetCoordinatorSettings
        try:
            async with AdminSessionLocal() as db:
                row = (await db.execute(select(FleetCoordinatorSettings).where(FleetCoordinatorSettings.id == 1))).scalar_one_or_none()
                if row is None:
                    db.add(FleetCoordinatorSettings(
                        id=1, enabled=self.enabled, min_distance=self.min_distance,
                        clear_distance=self.clear_distance, evasion_enabled=self.evasion_enabled,
                        priorities=json.dumps(self.priorities) if self.priorities else None,
                    ))
                    await db.commit()
                    return
                self.enabled = bool(row.enabled)
                self.min_distance = max(0.1, float(row.min_distance))
                self.clear_distance = float(row.clear_distance)
                self.evasion_enabled = bool(row.evasion_enabled)
                if self.clear_distance < self.min_distance:
                    self.clear_distance = self.min_distance + 0.2
                if row.priorities:
                    try:
                        self.priorities = {int(k): int(v) for k, v in json.loads(row.priorities).items()}
                    except (ValueError, TypeError, json.JSONDecodeError):
                        self.priorities = {}
        except Exception as exc:  # noqa: BLE001 — DB 미준비 시에도 기본값으로 동작해야 함
            self.last_error = f"설정 로드 실패(기본값 사용): {exc!r}"

    async def save(self) -> None:
        """현재 설정을 DB 에 영속화 — 백엔드 재시작에도 유지되도록."""
        from app.models import FleetCoordinatorSettings
        async with AdminSessionLocal() as db:
            row = (await db.execute(select(FleetCoordinatorSettings).where(FleetCoordinatorSettings.id == 1))).scalar_one_or_none()
            if row is None:
                row = FleetCoordinatorSettings(id=1)
                db.add(row)
            row.enabled = self.enabled
            row.min_distance = self.min_distance
            row.clear_distance = self.clear_distance
            row.evasion_enabled = self.evasion_enabled
            row.priorities = json.dumps(self.priorities) if self.priorities else None
            await db.commit()

    def config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_distance": self.min_distance,
            "clear_distance": self.clear_distance,
            "interval": self.interval,
            "priorities": self.priorities,
            "evasion_enabled": self.evasion_enabled,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.config(),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "robots": list(self.robots.values()),
            "holds": self.holds,
            "evasions": self.evasions,
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
        await self.load()  # 재시작 시 저장된 설정(enabled/거리/우선순위) 복원
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

            # evasion 용: 로봇별 등록 구역(A~H, HOME 제외) 좌표. 회피 목적지/복귀 지점 계산에 쓴다.
            zones_by_robot: dict[int, dict[str, tuple[float, float, float]]] = {}
            if self.evasion_enabled:
                loc_rows = (await db.execute(select(RobotLocation).where(RobotLocation.name != "HOME"))).scalars().all()
                for lr in loc_rows:
                    zones_by_robot.setdefault(lr.robot_id, {})[lr.name] = (float(lr.x), float(lr.y), float(lr.yaw))

        # 각 로봇 상태 병렬 조회 (ROS 캐시 우선, stale 시 HTTP 폴백)
        results = await asyncio.gather(*[asyncio.to_thread(_robot_state, name, base) for _, name, _, base in robots])

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
                "path": (state or {}).get("path") or [],
                "moving": moving, "status": status,
                "priority": self.priority_of(rid),
                "near_id": None, "near_name": None, "near_dist": None,
                "held": rid in self.holds, "resumable": False,
            }

        # 더 이상 없는 로봇의 hold/evasion 정리
        for rid in list(self.holds.keys()):
            if rid not in snap:
                self.holds.pop(rid, None)
                self._last_pose.pop(rid, None)
        for rid in list(self.evasions.keys()):
            if rid not in snap:
                self.evasions.pop(rid, None)

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

                # 정지 판정: min_distance 이내면 접근 중인(또는 우선순위 낮은) 쪽을 정지.
                # 임박(min_distance×IMMINENT_FACTOR 이내)하고 둘 다 이동 중이면 양쪽 다 정지한다 —
                # victim 만 세우면 고순위 로봇이 정지한 로봇을 그대로 들이받을 수 있어서다.
                if d <= self.min_distance:
                    mi, mj = snap[ia]["moving"], snap[ib]["moving"]
                    imminent = d <= self.min_distance * IMMINENT_FACTOR
                    victims: list[int] = []
                    if mi and mj:
                        if imminent:
                            victims = [ia, ib]
                        else:
                            # 외곽 밴드 — 우선순위 낮은(값 큰) 쪽만 양보. 동순위면 robot_id 큰 쪽.
                            victims = [ia if (self.priority_of(ia), ia) > (self.priority_of(ib), ib) else ib]
                    elif mi:
                        victims = [ia]
                    elif mj:
                        victims = [ib]
                    for victim in victims:
                        if victim in self.holds:
                            continue
                        peer = ib if victim == ia else ia
                        await asyncio.to_thread(_stop_robot, snap[victim]["name"], snap[victim]["base"])
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

        self.last_error = None  # 핵심(근접 정지) 틱 성공 — 이전 전이 오류 클리어

        # 막는 로봇 자동 비켜세우기(자동 주행 — 명시적 opt-in 시에만)
        if self.evasion_enabled:
            try:
                await self._evasion_pass(snap, zones_by_robot, now)
            except Exception as exc:  # noqa: BLE001 — 회피 실패가 루프를 죽이지 않도록
                self.last_error = f"evasion 오류: {exc!r}"

        self.robots = snap
        self.last_tick = now

    async def _evasion_pass(self, snap: dict[int, dict[str, Any]], zones_by_robot: dict[int, dict[str, tuple[float, float, float]]], now: float) -> None:
        """이동 로봇의 계획 경로를 막고 있는 유휴/하위 로봇을 경로 밖 빈 구역으로 비켜세우고, 통과 후 원위치 복귀시킨다.

        정책(운영자 합의): 막는 로봇이 유휴면 우선순위 무관하게 비킴. 둘 다 이동 중이면 우선순위 낮은 쪽만.
        """
        ids = list(snap.keys())
        block_radius = self.min_distance
        blockers_now: set[int] = set()

        for mid in ids:
            M = snap[mid]
            if not M["moving"] or mid in self.holds or not M["pose"]:
                continue
            mpath = M.get("path") or []
            if len(mpath) < 2:
                continue
            m_xy = (M["pose"]["x"], M["pose"]["y"])
            for bid in ids:
                if bid == mid or bid in self.holds or not snap[bid]["pose"]:
                    continue
                B = snap[bid]
                b_idle = not B["moving"]
                b_lower = (self.priority_of(bid), bid) > (self.priority_of(mid), mid)
                if not (b_idle or b_lower):  # 둘 다 이동 중이면서 B 가 상위면 B 는 안 비킨다
                    continue
                b_xy = (B["pose"]["x"], B["pose"]["y"])
                nd = _path_min_dist_ahead(b_xy, mpath, m_xy, EVASION_LOOKAHEAD)
                if nd is None or nd > block_radius:
                    continue
                blockers_now.add(bid)
                if bid in self.evasions:
                    continue  # 이미 회피 지시함
                zones = zones_by_robot.get(bid, {})
                others = [(snap[o]["pose"]["x"], snap[o]["pose"]["y"]) for o in ids if o != bid and snap[o]["pose"]]
                target = _pick_evasion_zone(b_xy, zones, mpath, others, EVASION_CLEARANCE)
                origin = _nearest_zone(b_xy, zones)
                if target is None or origin is None:
                    # 비켜설 곳이 없으면 안전하게 정지(충돌 방지 우선)
                    await asyncio.to_thread(_stop_robot, B["name"], B["base"])
                    self.holds[bid] = {"reason": "회피 불가 정지", "peer_id": mid, "peer_name": M["name"], "distance": round(nd, 3), "since": now}
                    continue
                tname, tcoords = target
                await asyncio.to_thread(_move_robot, B["name"], B["base"], tname, tcoords)
                self.evasions[bid] = {
                    "mover_id": mid, "mover_name": M["name"],
                    "target_zone": tname, "origin_zone": origin[0], "origin_coords": origin[1],
                    "returning": False, "since": now,
                }

        # 더 이상 안 막는 회피 로봇 → 원위치 복귀(1회) 후 도착/타임아웃 시 정리
        for bid in list(self.evasions.keys()):
            if bid in blockers_now:
                continue
            ev = self.evasions[bid]
            B = snap.get(bid)
            if B is None:
                self.evasions.pop(bid, None)
                continue
            if not ev["returning"]:
                await asyncio.to_thread(_move_robot, B["name"], B["base"], ev["origin_zone"], ev["origin_coords"])
                ev["returning"] = True
                ev["since"] = now
            else:
                ox, oy, _ = ev["origin_coords"]
                reached = B["pose"] and math.hypot(B["pose"]["x"] - ox, B["pose"]["y"] - oy) < block_radius
                if reached or (now - ev["since"] > EVASION_RETURN_TIMEOUT):
                    self.evasions.pop(bid, None)

        # snap 에 회피 상태 표시(UI/스냅샷용)
        for bid, ev in self.evasions.items():
            if bid in snap:
                snap[bid]["evading"] = {"target_zone": ev["target_zone"], "mover_name": ev["mover_name"], "returning": ev["returning"]}


coordinator = FleetCoordinator()
