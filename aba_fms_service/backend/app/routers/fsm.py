"""FSM + BT 관제 API — 상태 조회, 직접 전이, 감사 이력, 실시간 push.

INSTRUCTION.md 2단계. **폴링 API 를 만들지 않는다** — 실시간 갱신은 `/ws/state` 전용이고
`GET /state` 는 페이지 최초 진입 시 1회용이다.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import fsm_audit, fsm_link, fsm_model
from app.database import get_admin_db
from app.deps import get_current_admin
from app.models import Admin, iso_utc
from app.security import decode_token

router = APIRouter(prefix="/api/fsm", tags=["fsm"])

log = logging.getLogger(__name__)

HISTORY_DEFAULT_LIMIT = 20

# WORKING 이탈 시 FMS 에 태스크 취소를 알리는 sentinel 목표값.
# 진짜 계약은 RMF 태스크 상태를 소유한 쪽에 있다 — 아래 _report_task_cancelled 주석 참고.
TASK_CANCELLED_SENTINEL = "__task_cancelled__"


def resolve_robot_id(raw: str, known: Iterable[str] | None = None) -> str:
    """UI 가 보낸 로봇 식별자를 **FSM 캐시에 실제로 있는 키**로 맞춘다.

    FSM 경로의 정본은 로봇이 스스로 발행하는 `robot_id` 다. `/libi/fsm_state` 는
    로봇별 네임스페이스가 없는 공용 토픽이고, fsm_link 는 payload 의 `robot_id` 를
    그대로 캐시 키로 쓴다. 로봇 쪽(libi_modes `ros/state_io.py`)도 전이 요청을 그
    문자열과 **정확히** 비교해 거른다.

    그래서 브릿지 키(`Pinky-3` → `pinky3`)로 바꾸면 안 된다. 예전엔 그렇게 했는데
    캐시엔 `Pinky-3` 로 들어있어 조회가 늘 빗나갔고(화면은 항상 "수신 대기"),
    전이 요청도 로봇이 이름 불일치로 버렸다. 브릿지 키는 토픽 **접두사**를 쓰는
    텔레메트리 경로의 규칙이지 FSM 경로의 규칙이 아니다.

    규칙 자체는 fsm_link 가 소유한다 — 배차 표(fleet_link.build_rows)도 같은 걸 쓴다.
    """
    return fsm_link.resolve_id(raw, known)


class TransitionRequest(BaseModel):
    robot_id: str = Field(..., min_length=1, max_length=40)
    target_state: str = Field(..., min_length=1, max_length=24)
    force: bool = False


class SetSimBatteryRequest(BaseModel):
    robot_id: str = Field(..., min_length=1, max_length=40, description="로그용")
    domain_id: int = Field(..., ge=0, le=232, description="대상 sim 로봇의 ROS_DOMAIN_ID")
    value: float = Field(..., ge=0.0, le=100.0)


@dataclass
class TransitionOutcome:
    accepted: bool
    reason: str
    should_dispatch: bool
    needs_task_cancel: bool


def _decide(
    *, current: str | None, target: str, force: bool, error_code: str | None
) -> TransitionOutcome:
    """전이 요청을 판정한다 — 순수 함수라 DB/ROS 없이 테스트된다."""
    if not current:
        return TransitionOutcome(
            accepted=False,
            reason="로봇의 현재 상태를 알 수 없습니다 (FSM 링크 끊김).",
            should_dispatch=False,
            needs_task_cancel=False,
        )
    accepted, reason = fsm_model.validate(current, target, force, error_code)
    return TransitionOutcome(
        accepted=accepted,
        reason=reason,
        should_dispatch=accepted,
        # INSTRUCTION.md 안전 규칙: WORKING 에서 나갈 때는 FMS 에 task_cancelled 보고
        needs_task_cancel=accepted and current == "WORKING",
    )


@router.get("/model")
async def get_model(_: Admin = Depends(get_current_admin)):
    """상태·간선·허용 전이·mermaid 소스. 프론트엔드의 유일한 정의 원천."""
    return {"ok": True, "model": fsm_model.as_dict()}


@router.get("/state")
async def get_state(
    robot_id: str = Query(...),
    _: Admin = Depends(get_current_admin),
):
    """최초 진입용 1회 조회. 실시간 갱신은 /ws/state 를 쓸 것."""
    key = resolve_robot_id(robot_id)
    snap = fsm_link.snapshot(key)
    if snap is None:
        return {
            "ok": False,
            "robot_id": key,
            "snapshot": None,
            "allowed_targets": [],
            "reason": "해당 로봇의 FSM 상태를 아직 수신하지 못했습니다.",
        }
    return {
        "ok": True,
        "robot_id": key,
        "snapshot": snap,
        "allowed_targets": fsm_model.allowed_targets(snap.get("current_state") or ""),
    }


@router.get("/history")
async def get_history(
    robot_id: str | None = Query(None),
    limit: int = Query(HISTORY_DEFAULT_LIMIT, ge=1, le=200),
    db: AsyncSession = Depends(get_admin_db),
    _: Admin = Depends(get_current_admin),
):
    key = resolve_robot_id(robot_id) if robot_id else None
    rows = await fsm_audit.recent_transitions(db, robot_id=key, limit=limit)
    return {
        "ok": True,
        "items": [
            {
                "id": r.id,
                "robot_id": r.robot_id,
                "from_state": r.from_state,
                "to_state": r.to_state,
                "forced": r.forced,
                "accepted": r.accepted,
                "reason": r.reason,
                "admin_username": r.admin_username,
                "created_at": iso_utc(r.created_at),
            }
            for r in rows
        ],
    }


@router.delete("/history")
async def clear_history(
    robot_id: str | None = Query(None, description="비우면 모든 로봇의 이력을 지운다"),
    db: AsyncSession = Depends(get_admin_db),
    admin: Admin = Depends(get_current_admin),
):
    """전이 이력 삭제 — 발표·녹화 전에 화면을 비우는 용도.

    ⚠️ 감사 로그를 지우는 기능이다. **지운 사실은 서버 로그에 WARNING 으로 남긴다** —
    화면에서 사라져도 누가 언제 몇 건을 지웠는지는 추적할 수 있어야 한다.
    자세한 배경은 `fsm_audit.clear_transitions` 참고.
    """
    key = resolve_robot_id(robot_id) if robot_id else None
    removed = await fsm_audit.clear_transitions(db, robot_id=key)
    log.warning(
        "FSM 전이 이력 삭제 — 관리자=%s 대상=%s 삭제=%d건",
        admin.username, key or "(전체 로봇)", removed,
    )
    return {"ok": True, "removed": removed, "robot_id": key}


async def _report_task_cancelled(robot_id: str) -> None:
    """WORKING 이탈 시 태스크 취소를 알린다.

    보고하지 않으면 해당 태스크가 RMF 에 잔존하여 재배차되지 않는다 (INSTRUCTION.md 6. WORKING).

    ⚠️ 현재는 전이 채널에 sentinel 목표값을 실어 보내는 **자리표시 형태**다. 진짜 계약은
    RMF 태스크 상태를 소유한 쪽에 있고 아직 정해지지 않았다 — 운영 투입 전에 확정해야 한다.
    """
    await asyncio.to_thread(
        fsm_link.request_transition, robot_id, TASK_CANCELLED_SENTINEL, False, 1.0
    )


@router.post("/transition")
async def post_transition(
    body: TransitionRequest,
    db: AsyncSession = Depends(get_admin_db),
    admin: Admin = Depends(get_current_admin),
):
    key = resolve_robot_id(body.robot_id)
    snap = fsm_link.snapshot(key) or {}
    current = snap.get("current_state")
    error_code = snap.get("error_code") or None

    outcome = _decide(
        current=current, target=body.target_state, force=body.force, error_code=error_code
    )

    result_state = current or ""
    reason = outcome.reason

    if outcome.should_dispatch:
        if outcome.needs_task_cancel:
            await _report_task_cancelled(key)
        link_result = await asyncio.to_thread(
            fsm_link.request_transition, key, body.target_state, body.force
        )
        if link_result is None:
            outcome = TransitionOutcome(
                accepted=False,
                reason="미션 PC FSM 링크에 연결할 수 없습니다 (브릿지 미기동 또는 오프라인).",
                should_dispatch=False,
                needs_task_cancel=False,
            )
            reason = outcome.reason
        else:
            outcome.accepted = link_result["accepted"]
            reason = link_result["reason"] or reason
            result_state = link_result["current_state"] or result_state

    # 수락/거부와 무관하게 남긴다 — 강제 전이 추적이 목적이라 실패 이력이 오히려 중요하다.
    await fsm_audit.record_transition(
        db,
        admin_id=admin.id,
        admin_username=admin.username,
        robot_id=key,
        from_state=current or "UNKNOWN",
        to_state=body.target_state,
        forced=body.force,
        accepted=outcome.accepted,
        reason=reason,
    )

    return {"accepted": outcome.accepted, "current_state": result_state, "reason": reason}


@router.post("/sim_battery")
async def set_sim_battery(
    body: SetSimBatteryRequest,
    admin: Admin = Depends(get_current_admin),
):
    """[sim 전용] 로봇 도메인의 sim 배터리 소스에 잔량을 강제 설정한다.

    FSM 배터리는 로봇이 발행하는 값이라 서버가 직접 못 바꾼다 — sim_battery.py 의
    `/sim_battery/set` 에 값을 밀어 넣어 전이 임계값(≤15% / ≥80%)을 실제로 밟아 본다.
    실물 로봇엔 구독자가 없어 delivered=False 로 돌아온다.
    """
    result = await asyncio.to_thread(
        fsm_link.set_sim_battery, body.domain_id, body.value
    )
    log.info(
        "sim 배터리 설정 — 관리자=%s 로봇=%s 도메인=%d 값=%.0f%% delivered=%s",
        admin.username, body.robot_id, body.domain_id, body.value,
        result.get("delivered"),
    )
    return {"ok": result.get("ok", False), **result}


@router.websocket("/ws/state")
async def fsm_state_ws(websocket: WebSocket, token: str = Query(...)):
    """상태/BT 스냅샷 실시간 push (폴링 아님)."""
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    fsm_link.add_listener(loop, queue)
    try:
        # 접속 즉시 현재 캐시를 한 번 밀어준다 (빈 화면 방지)
        for robot_id, snap in fsm_link.all_snapshots().items():
            await websocket.send_json({"ok": True, "robot_id": robot_id, "snapshot": snap})
        while True:
            message = await queue.get()
            await websocket.send_json({"ok": True, **message})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        fsm_link.remove_listener(loop, queue)
