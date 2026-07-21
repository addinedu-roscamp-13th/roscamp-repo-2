"""음성 대화 기반 로봇 제어 — OpenAI Realtime API(WebRTC) 연동.

흐름:
  1) 프론트가 POST /api/voice/session 으로 ephemeral(1회용) 세션 토큰을 받는다.
     - OPENAI_API_KEY 는 서버에만 두고, 브라우저에는 만료 짧은 client_secret 만 내려간다.
     - 세션에는 로봇 페르소나 instructions + function tool(control_robot/stop_robot) 이 박혀 있다.
  2) 브라우저는 이 토큰으로 OpenAI Realtime 과 WebRTC 직결(마이크→STT, TTS→스피커).
  3) 모델이 control_robot(steps=[...]) 같은 function 을 호출하면, 프론트가 그 인자를
     POST /api/voice/execute 로 전달한다. 여기서 스텝을 순차로 실제 로봇 모터에 보낸다.

⚠️ CLAUDE.md 규칙: 로봇을 실제로 움직이는 명령은 사람 감독하에서만 실행한다.
   execute 는 confirm=True 일 때만 주행하며, 프론트의 '음성 주행 허용' 토글이 이를 제어한다.
   모터 이동은 오도메트리 없는 시간기반(open-loop) 추정이라 거리/각도는 근사값이다.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.database import get_admin_db, get_robot_db
from app.deps import get_current_admin
from app.models import Admin, Robot
from app.robot_dispatch import DRIVING_ACTIONS, resolve_learned_command
from app.routers.robot_learning import (
    InterpretIn,
    _auth_headers,
    _command_success,
    _dispatch_call,
    _list_driving_robots,
    _resolve_robot,
    interpret as interpret_robot_command,
)
# 챗과 동일한 복합명령 분해 + goto 도착 대기를 음성에서도 재사용한다(다중 주문 동일 동작).
from app.routers.chat import _split_compound, _wait_arrival, _COMPOUND_MAX

router = APIRouter(prefix="/api/voice", tags=["admin-voice"])

# GA(정식) 엔드포인트. 구 베타 /v1/realtime/sessions 는 삭제됨(2025 GA 전환).
#   mint: POST /v1/realtime/client_secrets  → {value: "ek_...", expires_at, session}
#   브라우저 SDP 교환: POST /v1/realtime/calls (프론트에서 ek_ 로 직접 호출)
OPENAI_SESSION_URL = "https://api.openai.com/v1/realtime/client_secrets"

# 로봇 페르소나: 로봇이 직접 대답하는 말투. 짧고 공손하게, 실행 전 한 문장으로 복창.
ROBOT_PERSONA = (
    "너는 주행로봇 관제 음성 비서다. 사용자의 한국어 음성 명령을 듣고 로봇을 제어한다. "
    "로봇이 직접 말하는 것처럼 공손하고 짧게 대답한다. 예: '네, 알겠습니다. 이동할게요.' "
    "명령에 로봇 번호가 없으면 어느 로봇인지 되묻는다. "
    "도구 선택 규칙: "
    "(1) '직진 10cm 이동 후 왼쪽으로 회전하고 10cm 이동 후 정지'처럼 전진/후진/회전이 "
    "여러 단계로 이어지는 주행은 control_robot 함수의 steps 배열에 순서대로 담아 호출한다. "
    "(2) 정지/멈춰 같은 긴급 정지는 stop_robot 을 호출한다. "
    "(3) 그 밖의 모든 명령(LCD 문구 출력, 표정/감정, 벨·삐·알람 등 사운드, 특정 위치·구역 이동, "
    "주차, 순회, 홈 복귀, 단순 이동)은 run_robot_tasks 함수를 쓴다. commands 배열에 "
    "'각각 독립적으로 실행 가능한 완성된 문장'을 실행 순서대로 담되, 문장마다 로봇 번호를 포함한다. "
    "명령이 하나면 원소 1개, 여러 동작을 한 문장으로 말하면(예: 'LCD에 홈 이동중 표시하면서 홈으로 이동') "
    "동작 단위로 쪼개 여러 원소로 담는다. 예: ['주행로봇1 LCD에 홈 이동중 출력', '주행로봇1 홈으로 이동']. "
    "LCD/표정/사운드는 즉시 끝나므로 이동보다 먼저 오도록 순서를 잡는다. "
    "거리 단위가 없으면 cm 로, 회전 각도가 없으면 90도로 가정한다. "
    "함수를 호출한 뒤에는 결과(spoken 필드)를 참고해 로봇 말투로 짧게 확인해 준다."
)

# 모델이 호출할 수 있는 function tool 정의(Realtime tools 스키마).
VOICE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "control_robot",
        "description": (
            "주행로봇을 순차적으로 제어한다. 전진/후진(cm)과 좌/우 회전(도), 정지를 "
            "steps 배열 순서대로 실행한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "robot_number": {
                    "type": "integer",
                    "description": "주행로봇 번호. 예: 1, 2, 3",
                },
                "steps": {
                    "type": "array",
                    "description": "실행할 동작들을 순서대로 나열",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "forward",
                                    "backward",
                                    "turn_left",
                                    "turn_right",
                                    "stop",
                                ],
                                "description": "동작 종류",
                            },
                            "value": {
                                "type": "number",
                                "description": (
                                    "forward/backward 는 이동 거리(cm), "
                                    "turn_left/turn_right 는 회전 각도(도). stop 은 생략."
                                ),
                            },
                        },
                        "required": ["action"],
                    },
                },
            },
            "required": ["robot_number", "steps"],
        },
    },
    {
        "type": "function",
        "name": "stop_robot",
        "description": "로봇을 즉시 정지시킨다(긴급 정지).",
        "parameters": {
            "type": "object",
            "properties": {
                "robot_number": {"type": "integer", "description": "주행로봇 번호"},
            },
            "required": ["robot_number"],
        },
    },
    {
        "type": "function",
        "name": "run_robot_tasks",
        "description": (
            "LCD 문구 출력, 표정/감정, 사운드(벨·삐·알람), 특정 위치·구역 이동, 주차, 순회, "
            "홈 복귀, 단순 이동 등 일반 로봇 명령을 실행한다. 여러 동작을 한 번에 말하면 "
            "commands 배열에 순서대로 담아 순차 실행한다(예: LCD 표시 후 이동). 각 원소는 관제 "
            "챗봇과 동일한 자연어 해석 파이프라인으로 처리된다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "description": (
                        "실행 순서대로의 완성 문장들. 각 문장에 로봇 번호 포함. "
                        "예: ['주행로봇1 LCD에 홈 이동중 출력', '주행로봇1 홈으로 이동']"
                    ),
                    "items": {"type": "string"},
                },
            },
            "required": ["commands"],
        },
    },
]


# ── /session : ephemeral 토큰 발급 ────────────────────────────────────────────


def _mint_ephemeral_session() -> dict[str, Any]:
    """OpenAI 에 Realtime 세션을 만들고 ephemeral(client_secret) 응답을 돌려준다(blocking).

    GA 스키마: 세션 설정을 {"session": {...}} 로 감싸고, 오디오/전사/VAD 는 audio.input/output
    아래에 둔다. 응답의 최상위 value 가 브라우저용 ephemeral 키(ek_...)다.
    """
    body = json.dumps(
        {
            "session": {
                "type": "realtime",
                "model": config.OPENAI_REALTIME_MODEL,
                "instructions": ROBOT_PERSONA,
                "audio": {
                    "input": {
                        "transcription": {"model": "whisper-1"},
                        # 푸시투토크(PTT): 서버 VAD 를 끄고, 클라이언트가 버튼을 뗄 때
                        # input_audio_buffer.commit + response.create 로 발화 종료를 명시한다.
                        # (항상 듣기 대신 누르는 동안만 듣게 해 오작동을 없앤다.)
                        "turn_detection": None,
                    },
                    "output": {"voice": config.OPENAI_REALTIME_VOICE},
                },
                "tools": VOICE_TOOLS,
                "tool_choice": "auto",
            }
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_SESSION_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))


@router.post("/session")
async def create_session(_admin: Admin = Depends(get_current_admin)):
    """프론트가 WebRTC 로 OpenAI Realtime 에 붙을 ephemeral 토큰을 발급한다."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY 가 설정되지 않았습니다(backend/.env 확인).",
        )
    try:
        data = await asyncio.to_thread(_mint_ephemeral_session)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") or str(exc)
        raise HTTPException(status_code=exc.code, detail=f"OpenAI 세션 발급 실패: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OpenAI 에 연결할 수 없습니다: {exc.reason}",
        ) from exc
    # GA: 최상위 value 가 ephemeral 키. (구 베타는 client_secret.value 였음)
    secret = data.get("value") or (data.get("client_secret") or {}).get("value")
    if not secret:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="ephemeral 토큰이 응답에 없습니다.")
    return {
        "client_secret": secret,
        "expires_at": data.get("expires_at") or (data.get("client_secret") or {}).get("expires_at"),
        "model": config.OPENAI_REALTIME_MODEL,
        "voice": config.OPENAI_REALTIME_VOICE,
    }


# ── /execute : function call 실행(로봇 모터로 순차 전송) ───────────────────────


class VoiceStep(BaseModel):
    action: Literal["forward", "backward", "turn_left", "turn_right", "stop"]
    value: float | None = None


class VoiceExecuteReq(BaseModel):
    robot_number: int = Field(..., ge=1)
    steps: list[VoiceStep] = Field(default_factory=list)
    # CLAUDE.md: 실제 주행은 사람 감독(=프론트 '음성 주행 허용' 토글)하에서만.
    confirm: bool = False


def _step_to_action(step: VoiceStep) -> tuple[str, dict[str, Any]]:
    """음성 step → (action_type, params). robot_dispatch.resolve_learned_command 규격."""
    if step.action == "forward":
        return "relative_move", {"direction": "forward", "distance_cm": step.value or 10.0}
    if step.action == "backward":
        return "relative_move", {"direction": "backward", "distance_cm": step.value or 10.0}
    if step.action == "turn_left":
        return "relative_turn", {"direction": "left", "angle_deg": step.value or 90.0}
    if step.action == "turn_right":
        return "relative_turn", {"direction": "right", "angle_deg": step.value or 90.0}
    return "stop", {}


@router.post("/execute")
async def execute(
    body: VoiceExecuteReq,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    _admin: Admin = Depends(get_current_admin),
):
    """모델이 호출한 control_robot/stop_robot 을 실제 로봇 모터로 순차 실행한다.

    반환의 spoken 필드는 프론트가 function_call_output 으로 모델에 돌려주면,
    모델이 그 내용을 로봇 말투로 음성 확인한다.
    """
    robot = await _resolve_robot(db, body.robot_number)
    if robot is None:
        return {
            "success": False,
            "spoken": f"{body.robot_number}번 로봇을 찾지 못했어요.",
            "robots": await _list_driving_robots(db),
        }

    if not body.steps:
        return {"success": False, "spoken": "실행할 동작이 없어요."}

    # 주행 confirm 게이트 — 정지(stop)만 있는 경우는 즉시 허용(긴급 정지).
    has_driving = any(_step_to_action(s)[0] in DRIVING_ACTIONS and _step_to_action(s)[0] != "stop" for s in body.steps)
    if has_driving and not body.confirm:
        return {
            "success": False,
            "requires_confirm": True,
            "spoken": "음성 주행이 꺼져 있어요. '음성 주행 허용'을 켠 뒤 다시 말씀해 주세요.",
        }

    headers = _auth_headers(request)
    results: list[dict[str, Any]] = []
    for idx, step in enumerate(body.steps):
        action_type, params = _step_to_action(step)
        path, cmd_body, note = resolve_learned_command(action_type, params)
        if path is None:
            results.append({"step": idx, "action": step.action, "success": False, "detail": note})
            break
        # 모터 명령은 blocking(urllib) + 로봇쪽에서 duration 동안 블록 → 자연 직렬화된다.
        http_ok, resp = await asyncio.to_thread(_dispatch_call, action_type, path, headers, cmd_body, robot)
        # ⚠️ 로봇은 모터가 안 움직여도 HTTP 200 + {"success": false} 를 줄 수 있다.
        # HTTP 상태만 보면 안 되고 본문의 success 까지 반영해야 '완료' 오보를 막는다.
        ok = _command_success(action_type, http_ok, resp)
        results.append(
            {"step": idx, "action": step.action, "value": step.value, "success": ok, "detail": resp, "note": note}
        )
        if not ok:
            break

    done = sum(1 for r in results if r.get("success"))
    total = len(body.steps)
    all_ok = done == total
    # 실패 원인(로봇 응답 본문)을 짧게 뽑아 음성으로 알려 준다.
    fail_detail = next((str(r.get("detail") or "") for r in results if not r.get("success")), "")
    reason = f" ({fail_detail[:60]})" if fail_detail else ""
    if all_ok:
        spoken = f"{robot.name}, {total}개 동작을 모두 완료했어요."
    elif done == 0:
        spoken = f"{robot.name} 동작을 실행하지 못했어요.{reason}"
    else:
        spoken = f"{robot.name}, {total}개 중 {done}개까지만 실행하고 멈췄어요.{reason}"
    return {"success": all_ok, "spoken": spoken, "robot": robot.name, "results": results}


# ── /command : LCD·표정·사운드·주행 등 일반 명령(관제 챗봇과 동일 파이프라인) ────


class VoiceCommandReq(BaseModel):
    command_text: str = Field(..., min_length=1)
    # 주행성 명령(위치이동/주차/순회 등)만 이 토글의 영향을 받는다. LCD/표정/사운드는 무관.
    confirm: bool = False


class VoiceTasksReq(BaseModel):
    commands: list[str] = Field(default_factory=list)
    confirm: bool = False


def _spoken_from_interpret(result: dict[str, Any]) -> str:
    """interpret 결과 → 로봇이 말할 짧은 한국어 문장."""
    if not result.get("matched"):
        return "죄송해요, 그 명령은 이해하지 못했어요."
    name = result.get("name") or "명령"
    target = result.get("target_robot") or (
        f"{result.get('robot_number')}번 로봇" if result.get("robot_number") else "로봇"
    )
    if result.get("needs_robot"):
        return f"'{name}'을(를) 어느 로봇에서 실행할까요?"
    run = result.get("result") or {}
    if run.get("requires_confirm"):
        return "주행 명령이라 확인이 필요해요. '음성 주행 허용'을 켠 뒤 다시 말씀해 주세요."
    ok = bool(run.get("success")) if "result" in result else True
    if ok:
        return f"네, {target}에 {name} 실행했어요."
    detail = run.get("response") or run.get("message") or result.get("message") or "알 수 없는 오류"
    return f"{name} 실행에 실패했어요. {str(detail)[:60]}"


async def _run_one_command(
    command_text: str, confirm: bool, request: Request, db: AsyncSession, rdb: AsyncSession, admin: Admin,
) -> dict[str, Any]:
    """한 개의 자연어 명령을 interpret 파이프라인으로 실행하고 결과 dict 를 돌려준다."""
    result = await interpret_robot_command(
        InterpretIn(text=command_text, execute=True, confirm=confirm),
        request,
        db,
        rdb,
        admin,
    )
    if isinstance(result, dict):
        return {
            "command": command_text,
            "success": bool((result.get("result") or {}).get("success", result.get("matched"))),
            "spoken": _spoken_from_interpret(result),
            "interpret": result,
        }
    return {"command": command_text, "success": False, "spoken": "명령 처리 중 문제가 생겼어요."}


async def _run_sequence(
    frags: list[str], confirm: bool, request: Request, db: AsyncSession, rdb: AsyncSession, admin: Admin,
) -> list[dict[str, Any]]:
    """하위 명령들을 순서대로 실행. goto 성공 뒤에는 도착까지 대기한다('완료되면' 순차).
    챗의 복합 실행과 동일한 규칙 — 음성/채팅 다중 주문 동작을 일치시킨다."""
    results: list[dict[str, Any]] = []
    for f in frags:
        out = await _run_one_command(f, confirm, request, db, rdb, admin)
        results.append(out)
        interp = out.get("interpret") or {}
        run = interp.get("result") or {}
        atype = run.get("action_type") or interp.get("action_type")
        if atype == "goto" and bool(run.get("success")):
            await _wait_arrival(f, interp, db)
    return results


def _sequence_spoken(results: list[dict[str, Any]]) -> tuple[bool, str]:
    done = sum(1 for r in results if r.get("success"))
    total = len(results)
    if total == 1:
        return bool(results[0].get("success")), results[0]["spoken"]
    if done == total:
        return True, f"네, {total}개 동작을 순서대로 실행했어요."
    if done == 0:
        return False, "동작을 실행하지 못했어요. " + (results[0]["spoken"] if results else "")
    fail = next((r for r in results if not r.get("success")), None)
    return False, f"{total}개 중 {done}개는 됐는데 일부 실패했어요. " + (fail["spoken"] if fail else "")


@router.post("/command")
async def command(
    body: VoiceCommandReq,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    rdb: AsyncSession = Depends(get_robot_db),
    admin: Admin = Depends(get_current_admin),
):
    """음성으로 받은 일반 명령을 관제 챗봇과 동일한 interpret 파이프라인으로 실행한다.

    한 문장에 여러 동작이 있으면 챗과 동일하게 분해해 순차 실행한다(다중 주문).
    LCD/표정/사운드는 즉시 실행되고, 위치이동·주차 같은 주행성 명령은 confirm(=음성 주행 허용)이
    켜져 있어야 실제로 움직인다.
    """
    frags = _split_compound(body.command_text)
    if len(frags) >= 2:
        results = await _run_sequence(frags, body.confirm, request, db, rdb, admin)
        ok, spoken = _sequence_spoken(results)
        return {"success": ok, "spoken": spoken,
                "interpret": {"compound": True, "results": [r.get("interpret") for r in results]}}
    out = await _run_one_command(body.command_text, body.confirm, request, db, rdb, admin)
    return {"success": out["success"], "spoken": out["spoken"], "interpret": out.get("interpret")}


@router.post("/tasks")
async def tasks(
    body: VoiceTasksReq,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    rdb: AsyncSession = Depends(get_robot_db),
    admin: Admin = Depends(get_current_admin),
):
    """여러 하위 명령을 순서대로 실행한다(복합 명령). 예: 'LCD 표시 후 홈 이동'.

    각 명령을 순차로 interpret 파이프라인에 태운다. LCD/표정/사운드는 즉시 끝나고, 주행(goto 등)은
    nav2 가 목표만 받고 바로 반환하므로, 'LCD 표시 → 이동 시작'이 자연스럽게 이어진다.
    """
    cmds = [c.strip() for c in body.commands if c and c.strip()]
    if not cmds:
        return {"success": False, "spoken": "실행할 명령이 없어요.", "results": []}

    # 각 명령을 챗과 동일 규칙으로 한 번 더 분해(횡이동→회전+전진, 유턴 등)한 뒤 평탄화.
    frags: list[str] = []
    for c in cmds:
        frags.extend(_split_compound(c) or [c])
    frags = frags[:_COMPOUND_MAX]  # 다중 주문 상한(3~4개)

    results = await _run_sequence(frags, body.confirm, request, db, rdb, admin)
    all_ok, spoken = _sequence_spoken(results)
    return {"success": all_ok, "spoken": spoken, "results": results}
