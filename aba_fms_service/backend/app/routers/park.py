"""색으로 주차 — 색 → 매핑된 마커 도킹.

동작(모든 신규 로직은 중앙 서버에서, 로봇은 기존 엔드포인트만 사용):
  1) GET  /api/park/perceive     — 로봇 카메라 스냅샷을 받아 중앙에서 ArUco 마커 + 주변 색을
                                    인식해 돌려준다. + 색↔마커 매핑(marker_actions)도 함께.
  2) POST /api/park/by-color     — 지정한 색에 매핑된 마커를 찾아 로봇의 기존 dock/start 로
                                    도킹(주차)시킨다.

⚠️ CLAUDE.md: 실제 도킹은 모터가 움직이므로 confirm=True(사용자 확인) 일 때만 시작한다.
   색↔마커 매핑은 marker_actions.params.color 에 저장한다(관리자 UI 에서 지정).
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_admin_db, get_robot_db
from app.deps import get_current_admin
from app.models import Admin, MarkerAction, Robot
from app.security import create_access_token
from app.routers.aruco_dock import detect_from_jpeg
from app.routers.robot_learning import _resolve_robot, _resolve_robot_id

router = APIRouter(prefix="/api/park", tags=["park-by-color"])


def _robot_base(robot: Robot) -> str:
    return f"http://{robot.ip_address}:{robot.port}"


async def _pick_robot(db: AsyncSession, robot_id: int | None, robot_number: int | None) -> Robot | None:
    if robot_id is not None:
        return await _resolve_robot_id(db, robot_id)
    return await _resolve_robot(db, robot_number)


def _fetch_snapshot(base: str) -> bytes | None:
    token = create_access_token("admin")
    try:
        req = urllib.request.Request(f"{base}/api/robot/camera/snapshot?token={token}", method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as r:
            return r.read()
    except Exception:
        return None


def _post_robot(base: str, path: str, body: dict[str, Any]) -> tuple[bool, str]:
    token = create_access_token("admin")
    try:
        req = urllib.request.Request(
            f"{base}{path}?token={token}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8.0) as r:
            return True, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, str(e)


async def _color_map(rdb: AsyncSession) -> list[dict[str, Any]]:
    """색↔마커 주차 매핑 목록.

    params.park_color(한국어 색 이름)에 저장한다. params.color 는 lcd_text 글자색(hex)으로
    이미 쓰이므로 충돌을 피해 별도 키를 쓴다.
    """
    rows = (await rdb.execute(select(MarkerAction).order_by(MarkerAction.marker_id))).scalars().all()
    out: list[dict[str, Any]] = []
    for m in rows:
        params = {}
        if m.params:
            try:
                params = json.loads(m.params)
            except Exception:
                params = {}
        color = (params.get("park_color") or "").strip()
        if color:
            out.append({"marker_id": m.marker_id, "color": color, "label": m.label,
                        "action_type": m.action_type, "enabled": m.enabled})
    return out


# ── perceive : 로봇 스냅샷 → 마커 + 색 ────────────────────────────────────────
@router.get("/perceive")
async def perceive(
    robot_id: int | None = None,
    robot_number: int | None = None,
    dictionary: str = "DICT_4X4_50",
    db: AsyncSession = Depends(get_admin_db),
    rdb: AsyncSession = Depends(get_robot_db),
    _admin: Admin = Depends(get_current_admin),
):
    """'지금 앞에 뭐 보여' — 로봇 카메라 스냅샷에서 마커+색을 인식(모터 미동작)."""
    robot = await _pick_robot(db, robot_id, robot_number)
    if robot is None:
        raise HTTPException(404, "로봇을 찾을 수 없습니다.")
    base = _robot_base(robot)
    jpeg = await asyncio.to_thread(_fetch_snapshot, base)
    if not jpeg:
        raise HTTPException(502, f"{robot.name} 카메라 스냅샷을 가져오지 못했습니다.")
    res = await asyncio.to_thread(detect_from_jpeg, jpeg, dictionary, None, True)
    if res is None:
        raise HTTPException(502, "스냅샷 이미지를 해석하지 못했습니다.")
    ms = res.get("markers") or []
    if not ms:
        res["spoken"] = "앞에 마커가 안 보여요."
    else:
        parts = []
        for m in ms[:4]:
            col = (m.get("color") or {}).get("name")
            parts.append((f"{col} " if col else "") + f"{m['id']}번 마커")
        res["spoken"] = "앞에 " + ", ".join(parts) + "이(가) 보여요."
    res["mapping"] = await _color_map(rdb)
    res["robot"] = {"id": robot.id, "name": robot.name}
    return res


# ── by-color : 색 → 매핑된 마커로 도킹 ────────────────────────────────────────
class ByColorReq(BaseModel):
    color: str = Field(..., min_length=1)
    robot_id: int | None = None
    robot_number: int | None = None
    dictionary: str = "DICT_4X4_50"
    # CLAUDE.md: 실제 주차(모터)는 사람 확인 하에서만.
    confirm: bool = False


@router.post("/by-color")
async def by_color(
    body: ByColorReq,
    db: AsyncSession = Depends(get_admin_db),
    rdb: AsyncSession = Depends(get_robot_db),
    _admin: Admin = Depends(get_current_admin),
):
    """지정한 색에 매핑된 마커를 찾아 그 마커로 주차(도킹)한다."""
    robot = await _pick_robot(db, body.robot_id, body.robot_number)
    if robot is None:
        return {"success": False, "spoken": "대상 로봇을 찾지 못했어요."}

    want = body.color.strip()
    mapping = await _color_map(rdb)
    hit = next((m for m in mapping if m["color"] == want and m["enabled"]), None)
    if hit is None:
        avail = ", ".join(sorted({m["color"] for m in mapping})) or "없음"
        return {"success": False, "spoken": f"'{want}'에 매핑된 마커가 없어요. 등록된 색: {avail}",
                "mapping": mapping}
    marker_id = hit["marker_id"]

    base = _robot_base(robot)
    # 도킹 전 현재 그 마커가 보이는지 확인(안내용, 실패해도 진행 가능)
    jpeg = await asyncio.to_thread(_fetch_snapshot, base)
    visible = False
    if jpeg:
        det = await asyncio.to_thread(detect_from_jpeg, jpeg, body.dictionary, None, False)
        visible = any(int(m["id"]) == marker_id for m in (det or {}).get("markers", []))

    if not body.confirm:
        return {"success": False, "requires_confirm": True, "marker_id": marker_id, "visible": visible,
                "spoken": f"{want} 자리({marker_id}번 마커)에 주차할까요? '주차 허용'을 켜고 다시 말씀해 주세요."}

    if not visible:
        return {"success": False, "marker_id": marker_id, "visible": False,
                "spoken": f"{want} 자리의 {marker_id}번 마커가 화면에 안 보여요. 로봇을 마커 쪽으로 돌려 주세요."}

    ok, resp = await asyncio.to_thread(
        _post_robot, base, "/api/robot/dock/start",
        {"marker_id": marker_id, "dictionary": body.dictionary},
    )
    if ok:
        return {"success": True, "marker_id": marker_id, "robot": robot.name,
                "spoken": f"네, {want} 자리({marker_id}번 마커)로 주차 시작할게요.", "detail": resp}
    return {"success": False, "marker_id": marker_id,
            "spoken": f"주차 시작에 실패했어요. ({resp[:60]})", "detail": resp}
