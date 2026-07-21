from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Request, status, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_admin_db, get_robot_db
from app.deps import get_current_admin
from app.models import Admin, Conversation, Message, Robot, RobotLocation
from app.robot_dispatch import call_local_api as _call_local_api
from app.routers.robot_learning import (
    InterpretIn,
    interpret as interpret_robot_command,
    _robot_number,
    _extract_named_destination,
    _resolve_robot,
)
from app import fleet_telemetry

import asyncio
import re
import time

router = APIRouter(prefix="/api/chat", tags=["admin-chat"])

# 복합 명령 분해: 한 문장에 여러 동작(예: 'LCD 표시하면서 홈으로 이동')이 있으면
# 연결어로 쪼개 각각 interpret 에 태운다. 음성 경로(run_robot_tasks)와 동일한 철학.
# '완료되면/끝나면/도착하면' 은 goto 도착 대기(순차)를 유도하는 연결어로도 쓴다.
_COMPOUND_SPLIT = re.compile(
    # '뒤/후' 는 반드시 '한 …' 접두 또는 '에' 접미와 함께여야 커넥터로 본다
    # (그렇지 않으면 '뒤로 돌아'·'후진' 의 글자를 잘못 잘라먹는다).
    r"\s*(?:,|、|그리고|하면서|하며|면서|한?\s*다음에?|그\s*다음에?|한\s*뒤에?|뒤에|한\s*후에?|후에|고\s*나서"
    r"|켜고|키고|틀고|하고"
    r"|완료\s*(?:되면|후에?|하면)|끝나면|끝난\s*뒤에?|도착\s*(?:하면|후에?))\s*"
)
# 쪼갠 뒤 남는 무의미한 꼬리말(중복 '이동해줘' 등)은 버린다.
_COMPOUND_FILLER = re.compile(r"^(?:그리고|또|그\s*다음|다음|이동(?:해줘|해|할게|하자)?|해줘|줘|시작|완료|도착)$")

_COMPOUND_MAX = 4  # 한 번에 처리할 하위 명령 상한 (그 이상은 잘라낸다)


def _preprocess_compound(text: str) -> str:
    """복합 문장을 규칙 파서가 다루기 쉽게 정규화한다.
    - '앞에 장애물이 없으면' 같은 조건절은 제거(라이다 안전정지가 그 조건을 대신 지킨다).
    - 좌/우 '이동'(횡이동)은 diff-drive 로 불가하므로 '그 방향 90도 회전 + 직진' 으로 확장.
    - 유턴/뒤로 돌아 는 독립 조각으로 분리한다.
    """
    t = text
    t = re.sub(r"앞에\s*장애물[가이]?\s*(?:없으면|없다면)", " ", t)
    t = re.sub(r"장애물[가이]?\s*(?:없으면|없다면)", " ", t)
    # 우측/오른쪽 N(cm|m) (이동) → 오른쪽 90도 회전, N 직진
    t = re.sub(
        r"(?:오른쪽|우측|오른편)\s*(?:으로|쪽으로|측으로)?\s*(\d+(?:\.\d+)?)\s*(cm|센티|센치|m|미터)\s*(?:이동|가|주행|가줘|이동해)?",
        r"오른쪽 90도 회전, \1\2 직진", t,
    )
    t = re.sub(
        r"(?:왼쪽|좌측|왼편)\s*(?:으로|쪽으로|측으로)?\s*(\d+(?:\.\d+)?)\s*(cm|센티|센치|m|미터)\s*(?:이동|가|주행|가줘|이동해)?",
        r"왼쪽 90도 회전, \1\2 직진", t,
    )
    # 유턴/뒤로 돌아(서/하고/…) → 독립 조각으로 (뒤의 동작과 콤마로 분리)
    t = re.sub(r"(유턴|뒤로\s*돌아|반대로\s*돌아)(?:서|하고|한?\s*뒤에?|한?\s*다음에?|해서|해)?", r"\1, ", t)
    return t


def _split_compound(text: str) -> list[str]:
    """복합 문장을 하위 명령 리스트로. 로봇 번호가 빠진 조각엔 앞 문장의 번호를 붙인다."""
    parts = [p.strip(" .\t,") for p in _COMPOUND_SPLIT.split(_preprocess_compound(text))]
    frags = [p for p in parts if len(p) >= 2 and not _COMPOUND_FILLER.match(p)]
    num = _robot_number(text)
    if num is not None:
        frags = [f if _robot_number(f) is not None else f"주행로봇{num} {f}" for f in frags]
    return frags[:_COMPOUND_MAX]

class ChatMessageReq(BaseModel):
    session_id: str
    user_message: str
    execute: bool = True
    confirm: bool = False


def _format_interpret_bot_message(command: str, result: dict[str, Any]) -> str:
    if not result.get("matched"):
        return f"🤖 명령을 이해하지 못했습니다.\n\n- 입력: {command}\n- 상세: {result.get('message', '등록된 액션/시나리오와 일치하지 않습니다.')}"

    name = result.get("name") or "로봇 명령"
    target = result.get("target_robot") or (f"{result.get('robot_number')}번 로봇" if result.get("robot_number") else "로봇")

    if result.get("needs_robot"):
        robots = result.get("robots") or []
        robot_names = ", ".join(r.get("name", "") for r in robots if r.get("name")) or "선택 가능한 로봇 없음"
        return f"🤖 '{name}'을(를) 어느 로봇에서 실행할까요?\n\n- 선택 가능: {robot_names}"

    run = result.get("result") or {}
    if run.get("requires_confirm"):
        return f"⚠️ 확인이 필요합니다.\n\n- 대상: {target}\n- 동작: {name}\n- 상세: {run.get('message', '이 동작은 로봇을 실제로 움직입니다.')}"

    ok = bool(run.get("success")) if "result" in result else True
    if ok:
        msg = f"🤖 실행했습니다.\n\n- 대상: {target}\n- 동작: {name}"
        note = run.get("message")  # 안전정지 등 부가 설명이 있으면 함께 노출
        if note:
            msg += f"\n- 상세: {note}"
        return msg

    detail = run.get("response") or run.get("message") or result.get("message") or "알 수 없는 오류"
    return f"❌ 실행 실패\n\n- 대상: {target}\n- 동작: {name}\n- 상세: {detail}"


async def _wait_arrival(frag: str, interp: dict[str, Any], admin_db: AsyncSession, timeout_s: float = 90.0) -> bool:
    """goto 하위 명령 뒤에서 목표 구역 도착까지 대기한다('완료되면' 의미).
    amcl_pose(fleet_telemetry 캐시)가 목표 xy 허용오차(0.25m) 안에 들면 True.
    좌표/포즈를 못 잡으면 최소 지연 후 진행(타임아웃도 False 반환하되 순차는 계속)."""
    number = interp.get("robot_number") or _robot_number(frag)
    dest = _extract_named_destination(frag)
    if number is None or not dest:
        await asyncio.sleep(3.0)
        return False
    robot = await _resolve_robot(admin_db, number)
    if robot is None:
        return False
    loc = (
        await admin_db.execute(
            select(RobotLocation).where(RobotLocation.robot_id == robot.id, RobotLocation.name == dest)
        )
    ).scalar_one_or_none()
    if loc is None:
        await asyncio.sleep(3.0)
        return False
    ip = robot.ip_address
    tol = 0.25  # CLAUDE.md xy_goal_tolerance 와 동일
    start = time.time()
    await asyncio.sleep(1.0)  # 목표를 막 받은 직후엔 아직 출발 전 — 잠깐 뒤부터 확인
    while time.time() - start < timeout_s:
        st = fleet_telemetry.get_state(ip)
        pose = (st or {}).get("pose")
        if pose is not None:
            dx = float(pose["x"]) - float(loc.x)
            dy = float(pose["y"]) - float(loc.y)
            if (dx * dx + dy * dy) ** 0.5 <= tol:
                return True
        await asyncio.sleep(0.5)
    return False


async def _get_or_create_conversation(db: AsyncSession, session_id: str, title_text: str) -> Conversation:
    stmt = select(Conversation).where(Conversation.session_id == session_id)
    conversation = (await db.execute(stmt)).scalar_one_or_none()
    if conversation:
        return conversation
    conversation = Conversation(
        session_id=session_id,
        title=title_text[:30] + "..." if len(title_text) > 30 else title_text,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.post("/message")
async def send_chat_message(
    req: ChatMessageReq,
    request: Request,
    chat_db: AsyncSession = Depends(get_robot_db),
    admin_db: AsyncSession = Depends(get_admin_db),
    _admin: Admin = Depends(get_current_admin),
):
    conversation = await _get_or_create_conversation(chat_db, req.session_id, req.user_message)

    chat_db.add(Message(conversation_id=conversation.id, role="user", content=req.user_message))
    await chat_db.commit()

    async def _interpret(text: str) -> dict[str, Any]:
        return await interpret_robot_command(
            InterpretIn(text=text, execute=req.execute, confirm=req.confirm),
            request=request,
            db=admin_db,
            rdb=chat_db,
            _admin=_admin,
        )

    # 1) 복합 명령이면 하위 명령별로 실행하고 결과를 합친다.
    frags = _split_compound(req.user_message)
    if len(frags) >= 2:
        sub: list[tuple[str, dict[str, Any]]] = []
        for f in frags:
            r = await _interpret(f)
            sub.append((f, r))
            # goto 가 성공하면 도착까지 기다렸다가 다음 동작을 실행한다('완료되면' 순차).
            run = r.get("result") or {}
            atype = run.get("action_type") or r.get("action_type")
            if atype == "goto" and bool(run.get("success")):
                await _wait_arrival(f, r, admin_db)
        matched = [(f, r) for f, r in sub if r.get("matched")]
        if matched:
            lines = [f"🤖 {len(matched)}개 동작을 순서대로 처리했어요."]
            for f, r in matched:
                lines.append("")
                lines.append(_format_interpret_bot_message(f, r))
            bot_msg_content = "\n".join(lines)
            success = all(bool((r.get("result") or {}).get("success", r.get("matched"))) for _, r in matched)
            result: dict[str, Any] = {"matched": True, "compound": True, "results": [r for _, r in matched]}
            chat_db.add(Message(conversation_id=conversation.id, role="assistant", content=bot_msg_content))
            await chat_db.commit()
            return {"success": success, "response": bot_msg_content, "bot_message": bot_msg_content, "interpretation": result}

    # 2) 단일 명령(또는 복합 분해 실패) → 원문 그대로 1회 해석.
    result = await _interpret(req.user_message)
    bot_msg_content = _format_interpret_bot_message(req.user_message, result)

    chat_db.add(Message(conversation_id=conversation.id, role="assistant", content=bot_msg_content))
    await chat_db.commit()

    return {
        "success": bool(result.get("result", {}).get("success", result.get("matched", False))),
        "response": bot_msg_content,
        "bot_message": bot_msg_content,
        "interpretation": result,
    }


class ChatCommandReq(BaseModel):
    session_id: str
    user_message: str
    robot_type: str  # "mobile" | "arm"
    action: str      # e.g., "move", "stop", etc.
    parameters: Optional[Dict[str, Any]] = None

@router.post("/execute")
async def execute_chat_command(
    req: ChatCommandReq,
    request: Request,
    db: AsyncSession = Depends(get_robot_db),
    _admin: Admin = Depends(get_current_admin)
):
    # 1. Get or create conversation
    conversation = await _get_or_create_conversation(db, req.session_id, req.user_message)

    # 2. Add user message to DB
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=req.user_message
    )
    db.add(user_msg)
    await db.commit()

    # 3. Process execution
    path = ""
    body = {}
    blocked_response: str | None = None

    if req.robot_type == "mobile":
        if req.action == "move":
            blocked_response = "챗에서 저수준 전진/후진 즉시 실행은 비활성화되었습니다. 구역 이동은 주행로봇 번호와 구역명을 포함해 확인 후 실행하세요."
        elif req.action == "stop":
            path = "/api/robot/motor/stop"
        elif req.action == "emotion":
            path = "/api/robot/lcd/emotion"
            body = {"emotion": req.parameters.get("emotion", "basic")}
        elif req.action == "text":
            path = "/api/robot/lcd/text"
            body = {
                "text": req.parameters.get("text", ""),
                "font_name": req.parameters.get("font_name", "default"),
                "font_size": int(req.parameters.get("font_size", 24)),
                "color": req.parameters.get("color", "#ffffff"),
                "bg_color": req.parameters.get("bg_color", "#000000"),
                "align": req.parameters.get("align", "center"),
                "scroll": bool(req.parameters.get("scroll", False)),
                "scroll_speed": int(req.parameters.get("scroll_speed", 3))
            }
        elif req.action == "buzzer":
            path = "/api/robot/buzzer"
            body = {
                "preset": req.parameters.get("preset", "bell"),
                "count": int(req.parameters.get("count", 1)),
                "freq": int(req.parameters.get("freq", 1000)),
                "duration": float(req.parameters.get("duration", 0.2))
            }
    elif req.robot_type == "arm":
        if req.action == "home":
            path = "/api/arm/home"
        elif req.action == "stop":
            path = "/api/arm/stop"
        elif req.action == "jog-stop":
            path = "/api/arm/jog-stop"
        elif req.action == "angles":
            path = "/api/arm/angles"
            body = {
                "angles": req.parameters.get("angles", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
                "speed": int(req.parameters.get("speed", 50))
            }
        elif req.action == "gripper":
            path = "/api/arm/gripper"
            body = {
                "angle": int(req.parameters.get("angle", 0))
            }
        elif req.action == "camera-view":
            path = "/api/arm/camera-view"
            body = {"preset": int(req.parameters.get("preset", 1))}
        elif req.action == "face-view":
            path = "/api/arm/face-view"
        elif req.action == "color-pick":
            path = "/api/arm/color-pick"
            body = {"color": req.parameters.get("color", "red")}
        elif req.action == "face-track":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/face-track/{'start' if start else 'stop'}"
        elif req.action == "gesture":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/gesture/{'start' if start else 'stop'}"
        elif req.action == "barcode-qr":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/barcode-qr/{'start' if start else 'stop'}"
        elif req.action == "classify":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/classify/{'start' if start else 'stop'}"
        elif req.action == "ocr":
            start = bool(req.parameters.get("start", True))
            path = f"/api/arm/ocr/{'start' if start else 'stop'}"

    # Extract auth header to proxy the request
    headers = {}
    auth_header = request.headers.get("Authorization")
    if auth_header:
        headers["Authorization"] = auth_header

    if blocked_response is not None:
        success = False
        response_text = blocked_response
    elif path:
        success, response_text = _call_local_api(path, headers=headers, data=body)
    else:
        success = False
        response_text = f"Unknown action: {req.action} for robot type: {req.robot_type}"

    # 4. Add bot response to DB
    bot_msg_content = f"🤖 로봇 명령 실행 결과: {'성공' if success else '실패'}\n\n- 대상: {req.robot_type}\n- 동작: {req.action}\n- 상세: {response_text}"
    bot_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=bot_msg_content
    )
    db.add(bot_msg)
    await db.commit()

    return {
        "success": success,
        "response": response_text,
        "bot_message": bot_msg_content
    }

@router.get("/history")
async def get_chat_history(
    session_id: str,
    db: AsyncSession = Depends(get_robot_db),
    _admin: Admin = Depends(get_current_admin)
):
    stmt = select(Conversation).where(Conversation.session_id == session_id)
    conversation = (await db.execute(stmt)).scalar_one_or_none()
    if not conversation:
        return []
        
    stmt_msgs = select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at.asc())
    rows = (await db.execute(stmt_msgs)).scalars().all()
    
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "created_at": r.created_at.isoformat() if r.created_at else None
        }
        for r in rows
    ]
