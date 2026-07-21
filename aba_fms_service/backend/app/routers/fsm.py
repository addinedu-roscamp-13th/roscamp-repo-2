"""FSM + BT 관제 API — 상태 조회, 직접 전이, 감사 이력, 실시간 push.

INSTRUCTION.md 2단계. **폴링 API 를 만들지 않는다** — 실시간 갱신은 `/ws/state` 전용이고
`GET /state` 는 페이지 최초 진입 시 1회용이다.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import fsm_audit, fsm_link, fsm_model
from app.database import get_admin_db
from app.deps import get_current_admin
from app.models import Admin
from app.security import decode_token

router = APIRouter(prefix="/api/fsm", tags=["fsm"])

HISTORY_DEFAULT_LIMIT = 20

# WORKING 이탈 시 FMS 에 태스크 취소를 알리는 sentinel 목표값.
# 진짜 계약은 RMF 태스크 상태를 소유한 쪽에 있다 — 아래 _report_task_cancelled 주석 참고.
TASK_CANCELLED_SENTINEL = "__task_cancelled__"


def _match_key(s: str) -> str:
    """비교 전용 정규화 — 하이픈·밑줄·공백을 지우고 대소문자를 무시한다.

    표기 차이를 흡수하려는 것뿐이라 **이 값을 밖으로 내보내지 않는다**
    (resolve_robot_id 는 캐시에 있는 원본 키를 돌려준다).
    """
    return s.replace("-", "").replace("_", "").replace(" ", "").casefold()


def resolve_robot_id(raw: str, known: Iterable[str] | None = None) -> str:
    """UI 가 보낸 로봇 식별자를 **FSM 캐시에 실제로 들어있는 키**로 맞춘다.

    FSM 경로의 정본은 로봇이 스스로 발행하는 `robot_id` 다. `/libi/fsm_state` 는
    로봇별 네임스페이스가 없는 공용 토픽이고(domain_bridge 설정 주석 참고),
    fsm_link 는 payload 의 `robot_id` 를 그대로 캐시 키로 쓴다. 로봇 쪽
    (libi_modes `ros/state_io.py`)도 전이 요청을 그 문자열과 **정확히** 비교해 거른다.

    그래서 여기서 브릿지 키(`Pinky-3` → `pinky3`)로 바꾸면 안 된다. 예전엔 그렇게
    했는데 캐시엔 `Pinky-3` 로 들어있어 조회가 늘 빗나갔고(화면은 항상 "수신 대기"),
    전이 요청도 로봇이 이름 불일치로 버렸다. 브릿지 키는 토픽 **접두사**를 쓰는
    텔레메트리 경로(fleet_telemetry)의 규칙이지 FSM 경로의 규칙이 아니다.
    같은 캐시를 읽는 admin_follow 도 원본 이름을 그대로 쓴다.

    표기 차이(하이픈·대소문자)는 흡수하되 돌려주는 값은 **캐시의 원본 키**다.
    캐시에 없으면 입력을 그대로 통과시킨다 — 조용히 버리면 "왜 안 보이지"가 되고,
    상태가 안 오면 화면에 "수신 대기"로 드러나는 편이 낫다.
    """
    if not raw:
        return raw
    ids = list(fsm_link.known_ids() if known is None else known)
    if raw in ids:
        return raw
    want = _match_key(raw)
    for rid in ids:
        if _match_key(rid) == want:
            return rid
    return raw


class TransitionRequest(BaseModel):
    robot_id: str = Field(..., min_length=1, max_length=40)
    target_state: str = Field(..., min_length=1, max_length=24)
    force: bool = False


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
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


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
