from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Request, status, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_admin_db, get_robot_db
from app.deps import get_current_admin
from app.models import Admin, Conversation, Message
from app.robot_dispatch import call_local_api as _call_local_api
from app.routers.robot_learning import InterpretIn, interpret as interpret_robot_command, _robot_number

import re

router = APIRouter(prefix="/api/chat", tags=["admin-chat"])

# 복합 명령 분해: 한 문장에 여러 동작(예: 'LCD 표시하면서 홈으로 이동')이 있으면
# 연결어로 쪼개 각각 interpret 에 태운다. 음성 경로(run_robot_tasks)와 동일한 철학.
_COMPOUND_SPLIT = re.compile(r"\s*(?:,|、|그리고|하면서|하며|면서|한?\s*다음에?|그\s*다음에?|한?\s*뒤에?|한?\s*후에?|고\s*나서)\s*")
# 쪼갠 뒤 남는 무의미한 꼬리말(중복 '이동해줘' 등)은 버린다.
_COMPOUND_FILLER = re.compile(r"^(?:그리고|또|그\s*다음|다음|이동(?:해줘|해|할게|하자)?|해줘|줘|시작)$")


def _split_compound(text: str) -> list[str]:
    """복합 문장을 하위 명령 리스트로. 로봇 번호가 빠진 조각엔 앞 문장의 번호를 붙인다."""
    parts = [p.strip(" .\t,") for p in _COMPOUND_SPLIT.split(text)]
    frags = [p for p in parts if len(p) >= 2 and not _COMPOUND_FILLER.match(p)]
    num = _robot_number(text)
    if num is not None:
        frags = [f if _robot_number(f) is not None else f"주행로봇{num} {f}" for f in frags]
    return frags

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
        return f"🤖 실행했습니다.\n\n- 대상: {target}\n- 동작: {name}"

    detail = run.get("response") or run.get("message") or result.get("message") or "알 수 없는 오류"
    return f"❌ 실행 실패\n\n- 대상: {target}\n- 동작: {name}\n- 상세: {detail}"


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
        sub = [(f, await _interpret(f)) for f in frags]
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
