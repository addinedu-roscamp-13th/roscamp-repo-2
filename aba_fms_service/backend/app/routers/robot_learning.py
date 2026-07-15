from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import OLLAMA_URL
from app.database import get_admin_db, get_robot_db
from app.deps import get_current_admin
from app.models import Admin, AiModelSettings, Robot, RobotLearnedAction, RobotLearnedScenario
from app.robot_dispatch import ROBOT_SERVER_URL, CONTROL_ACTIONS, DRIVING_ACTIONS, call_local_api, resolve_learned_command

router = APIRouter(prefix="/api/robot-learning", tags=["robot-learning"])

OLLAMA_DEFAULT_MODEL = "qwen3:1.7b"


def _ollama_generate_json(prompt: str, model: str = OLLAMA_DEFAULT_MODEL, url: str = OLLAMA_URL, timeout: int = 45) -> dict[str, Any]:
    """로봇 채팅/학습 명령 해석 전용 Ollama JSON 호출."""
    payload = {
        "model": model or OLLAMA_DEFAULT_MODEL,
        "prompt": f"{prompt}\n\n/no_think",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = (body.get("response") or "").strip()
    return json.loads(text)


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _effective_success(http_ok: bool, body: str) -> bool:
    """로봇이 HTTP 200 으로 {"success": false} 를 돌려줄 수 있으므로 본문의 success 도 반영."""
    if not http_ok:
        return False
    try:
        data = json.loads(body)
        if isinstance(data, dict) and "success" in data:
            return bool(data["success"])
    except Exception:
        pass
    return True


def _command_success(action_type: str, http_ok: bool, body: str) -> bool:
    if _effective_success(http_ok, body):
        return True
    if action_type in CONTROL_ACTIONS:
        msg = (body or "").lower()
        if "timed out" in msg or "timeout" in msg or "시간이 초과" in body or "응답 시간이 초과" in body:
            return True
    return False


class RunIn(BaseModel):
    confirm: bool = False


def _auth_headers(request: Request) -> dict:
    headers: dict = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    return headers


class LearnedActionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = None
    trigger_phrases: list[str] = Field(default_factory=list)
    robot_scope: str = "selected"
    action_type: str = Field(..., min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class LearnedScenarioIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = None
    trigger_phrases: list[str] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True


def action_out(row: RobotLearnedAction) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "trigger_phrases": _loads(row.trigger_phrases, []),
        "robot_scope": row.robot_scope,
        "action_type": row.action_type,
        "params": _loads(row.params, {}),
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def scenario_out(row: RobotLearnedScenario) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "trigger_phrases": _loads(row.trigger_phrases, []),
        "nodes": _loads(row.nodes, []),
        "edges": _loads(row.edges, []),
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/actions")
async def list_actions(db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    rows = (await db.execute(select(RobotLearnedAction).order_by(RobotLearnedAction.updated_at.desc()))).scalars().all()
    return {"items": [action_out(row) for row in rows]}


@router.post("/actions")
async def save_action(body: LearnedActionIn, db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    row = RobotLearnedAction(
        name=body.name.strip(),
        description=body.description,
        trigger_phrases=_dumps([p.strip() for p in body.trigger_phrases if p.strip()]),
        robot_scope=body.robot_scope,
        action_type=body.action_type,
        params=_dumps(body.params),
        enabled=body.enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return action_out(row)


@router.put("/actions/{action_id}")
async def update_action(action_id: int, body: LearnedActionIn, db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    row = await db.get(RobotLearnedAction, action_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 액션을 찾을 수 없습니다.")
    row.name = body.name.strip()
    row.description = body.description
    row.trigger_phrases = _dumps([p.strip() for p in body.trigger_phrases if p.strip()])
    row.robot_scope = body.robot_scope
    row.action_type = body.action_type
    row.params = _dumps(body.params)
    row.enabled = body.enabled
    await db.commit()
    await db.refresh(row)
    return action_out(row)


@router.delete("/actions/{action_id}")
async def delete_action(action_id: int, db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    row = await db.get(RobotLearnedAction, action_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 액션을 찾을 수 없습니다.")
    await db.delete(row)
    await db.commit()
    return {"success": True}


def _dispatch_call(action_type: str, path: str, headers: dict, body: dict, robot: Robot | None) -> tuple[bool, str]:
    """액션 종류에 맞는 목적지로 호출.

    - 주행(nav2) 계열(CONTROL_ACTIONS): 중앙서버 /api/control/*?robot_id=<id> (→ROS→nav2)
    - 그 외 하드웨어(LCD/모터/부저): 대상 로봇의 :9001 /api/robot/*
    """
    if action_type in CONTROL_ACTIONS:
        base = ROBOT_SERVER_URL
        if robot is not None:
            sep = "&" if "?" in path else "?"
            path = f"{path}{sep}robot_id={robot.id}"
        # 주행(nav2)은 ROS ack + 폴백 여유가 필요 → 넉넉한 타임아웃
        return call_local_api(path, headers, body, base, timeout=25)
    base = f"http://{robot.ip_address}:{robot.port}" if robot is not None else ROBOT_SERVER_URL
    return call_local_api(path, headers, body, base)


def _run_action_row(
    row: RobotLearnedAction,
    params: dict[str, Any],
    confirm: bool,
    headers: dict,
    robot: Robot | None = None,
) -> dict[str, Any]:
    """단일 학습 액션 실행. /run 과 /interpret 이 공유."""
    path, cmd_body, note = resolve_learned_command(row.action_type, params)
    if path is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=note or f"실행할 수 없는 액션입니다: {row.action_type}")

    # CLAUDE.md 규칙: 주행/도킹 액션은 confirm 없이 자동 실행하지 않는다.
    if row.action_type in DRIVING_ACTIONS and not confirm:
        return {
            "success": False,
            "requires_confirm": True,
            "action_type": row.action_type,
            "message": f"'{row.name}'은(는) 로봇을 실제로 주행시킵니다. 확인 후 다시 실행하세요.",
            "note": note,
        }

    if path == "":  # wait 등 API 호출이 필요 없는 액션
        return {"success": True, "response": "no-op", "action_type": row.action_type, "note": note}

    aux_results: list[dict[str, Any]] = []
    aux_emotion = params.get("emotion") if row.action_type == "goto" else None
    if aux_emotion and robot is not None:
        e_ok, e_resp = _dispatch_call("emotion", "/api/robot/lcd/emotion", headers, {"emotion": aux_emotion}, robot)
        aux_results.append({"action_type": "emotion", "success": _effective_success(e_ok, e_resp), "response": e_resp})

    http_ok, response_text = _dispatch_call(row.action_type, path, headers, cmd_body, robot)
    result = {
        "success": _command_success(row.action_type, http_ok, response_text),
        "response": response_text,
        "action_type": row.action_type,
        "path": path,
        "note": note,
    }
    if aux_results:
        result["aux_results"] = aux_results
    return result


@router.post("/actions/{action_id}/run")
async def run_action(
    action_id: int,
    body: RunIn,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    _admin: Admin = Depends(get_current_admin),
):
    row = await db.get(RobotLearnedAction, action_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 액션을 찾을 수 없습니다.")
    if not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="비활성화된 액션입니다.")
    return await asyncio.to_thread(_run_action_row, row, _loads(row.params, {}), body.confirm, _auth_headers(request))


@router.get("/scenarios")
async def list_scenarios(db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    rows = (await db.execute(select(RobotLearnedScenario).order_by(RobotLearnedScenario.updated_at.desc()))).scalars().all()
    return {"items": [scenario_out(row) for row in rows]}


@router.post("/scenarios")
async def save_scenario(body: LearnedScenarioIn, db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    row = RobotLearnedScenario(
        name=body.name.strip(),
        description=body.description,
        trigger_phrases=_dumps([p.strip() for p in body.trigger_phrases if p.strip()]),
        nodes=_dumps(body.nodes),
        edges=_dumps(body.edges),
        enabled=body.enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return scenario_out(row)


@router.put("/scenarios/{scenario_id}")
async def update_scenario(scenario_id: int, body: LearnedScenarioIn, db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    row = await db.get(RobotLearnedScenario, scenario_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 시나리오를 찾을 수 없습니다.")
    row.name = body.name.strip()
    row.description = body.description
    row.trigger_phrases = _dumps([p.strip() for p in body.trigger_phrases if p.strip()])
    row.nodes = _dumps(body.nodes)
    row.edges = _dumps(body.edges)
    row.enabled = body.enabled
    await db.commit()
    await db.refresh(row)
    return scenario_out(row)


def _ordered_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """start 노드에서 edge(from→to)를 따라 실행 순서대로 노드를 나열한다."""
    by_id = {str(n.get("id")): n for n in nodes}
    next_of: dict[str, str] = {}
    for e in edges:
        frm, to = str(e.get("from")), str(e.get("to"))
        if frm and to and frm not in next_of:
            next_of[frm] = to

    start = next((n for n in nodes if n.get("type") == "start"), None)
    if start is None:
        return list(nodes)  # start 없으면 저장 순서대로

    order: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur = str(start.get("id"))
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        order.append(by_id[cur])
        cur = next_of.get(cur, "")
    return order


async def _run_scenario_row(
    row: RobotLearnedScenario,
    confirm: bool,
    headers: dict,
    robot: Robot | None = None,
) -> dict[str, Any]:
    """시나리오 실행. /run 과 /interpret 이 공유."""
    nodes = _loads(row.nodes, [])
    edges = _loads(row.edges, [])
    ordered = _ordered_nodes(nodes, edges)

    # 실행할 액션 노드를 미리 해석 (검증 + 주행 여부 판단)
    steps: list[dict[str, Any]] = []
    has_driving = False
    for node in ordered:
        ntype = node.get("type")
        cfg = node.get("config", {}) or {}
        if ntype in ("start", "end"):
            continue
        if ntype == "wait":
            steps.append({"kind": "wait", "seconds": max(0.0, float(cfg.get("seconds", 1)))})
            continue
        # action 노드
        action_type = str(cfg.get("action_type", "")).strip()
        path, cmd_body, note = resolve_learned_command(action_type, cfg)
        if path is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"노드 '{node.get('label', node.get('id'))}' 실행 불가: {note}",
            )
        if action_type in DRIVING_ACTIONS:
            has_driving = True
        steps.append({"kind": "action", "action_type": action_type, "path": path, "body": cmd_body, "label": node.get("label"), "note": note})

    if has_driving and not confirm:
        return {
            "success": False,
            "requires_confirm": True,
            "message": f"'{row.name}' 시나리오는 로봇을 실제로 주행시키는 단계를 포함합니다. 확인 후 다시 실행하세요.",
        }

    results: list[dict[str, Any]] = []
    for step in steps:
        if step["kind"] == "wait":
            await asyncio.sleep(step["seconds"])
            results.append({"kind": "wait", "seconds": step["seconds"], "success": True})
            continue
        if step["path"] == "":  # no-op 액션
            results.append({"label": step["label"], "action_type": step["action_type"], "success": True, "response": "no-op"})
            continue
        http_ok, response_text = await asyncio.to_thread(_dispatch_call, step["action_type"], step["path"], headers, step["body"], robot)
        success = _command_success(step["action_type"], http_ok, response_text)
        results.append({
            "label": step["label"],
            "action_type": step["action_type"],
            "success": success,
            "response": response_text,
        })
        if not success:  # 실패하면 이후 단계 중단
            return {"success": False, "stopped_at": step["label"], "results": results}

    return {"success": True, "results": results}


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(
    scenario_id: int,
    body: RunIn,
    request: Request,
    robot_id: int | None = Query(None),
    db: AsyncSession = Depends(get_admin_db),
    _admin: Admin = Depends(get_current_admin),
):
    row = await db.get(RobotLearnedScenario, scenario_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 시나리오를 찾을 수 없습니다.")
    if not row.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="비활성화된 시나리오입니다.")
    robot = await _resolve_robot_id(db, robot_id) if robot_id is not None else None
    if robot_id is not None and robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="대상 주행로봇을 찾을 수 없습니다.")
    return await _run_scenario_row(row, body.confirm, _auth_headers(request), robot)


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: int, db: AsyncSession = Depends(get_admin_db), _admin: Admin = Depends(get_current_admin)):
    row = await db.get(RobotLearnedScenario, scenario_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 시나리오를 찾을 수 없습니다.")
    await db.delete(row)
    await db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────
# LLM 트리거 매핑: 자연어 지시 → 등록된 액션/시나리오
# ─────────────────────────────────────────────────────────────

class InterpretIn(BaseModel):
    text: str = Field(..., min_length=1)
    execute: bool = False
    confirm: bool = False


def _catalog(actions: list[RobotLearnedAction], scenarios: list[RobotLearnedScenario]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for a in actions:
        items.append({"kind": "action", "id": a.id, "name": a.name, "desc": a.description or "",
                      "phrases": _loads(a.trigger_phrases, []), "row": a})
    for s in scenarios:
        items.append({"kind": "scenario", "id": s.id, "name": s.name, "desc": s.description or "",
                      "phrases": _loads(s.trigger_phrases, []), "row": s})
    return items


def _score(text: str, item: dict[str, Any]) -> float:
    """결정적 매칭 점수: 이름/트리거 문구가 지시문에 얼마나 겹치는지."""
    t = text.lower()
    score = 0.0
    name = item["name"].lower().strip()
    if name and name in t:
        score += 3.0
    for p in item["phrases"]:
        pl = str(p).lower().strip()
        if not pl:
            continue
        if pl in t:
            score += 2.5
            continue
        toks = [w for w in re.split(r"\s+", pl) if len(w) >= 2]
        if toks:
            hit = sum(1 for w in toks if w in t)
            score += 1.5 * hit / len(toks)
    return score


def _robot_number(text: str) -> int | None:
    m = re.search(r"(\d+)\s*번", text)
    if m:
        return int(m.group(1))
    m = re.search(r"로봇\s*(\d+)", text)
    return int(m.group(1)) if m else None


def _strip_robot_ref(text: str) -> str:
    """매칭 전 로봇 지칭어(주행로봇1/로봇 2/3번 등)를 제거해 '의도'만 남긴다.

    '로봇' 같은 흔한 단어가 트리거와 우연히 겹쳐 오매칭되는 것을 막는다.
    로봇 번호 자체는 _robot_number 가 원문에서 따로 뽑는다.
    """
    t = text
    t = re.sub(r"주행\s*로봇\s*\d*\s*번?", " ", t)
    t = re.sub(r"로봇\s*\d+\s*번?", " ", t)
    t = re.sub(r"\d+\s*번", " ", t)
    t = t.replace("주행로봇", " ").replace("로봇", " ").replace("주행", " ")
    return re.sub(r"\s+", " ", t).strip()


_LCD_VERBS = r"(?:출력|표시|보여|띄워|써|나타내|말해)"


def _extract_lcd_text(text: str) -> str | None:
    """LCD 명령 문장에서 표시할 문구를 추출.

    예) '주행로봇1 LCD에 하이 2 라고 출력' → '하이 2'
        '로봇1 화면에 "환영합니다" 띄워'   → '환영합니다'
    """
    # 1) 따옴표 안 우선
    m = re.search(r"[\"'“”]([^\"'“”]+)[\"'“”]", text)
    if m:
        return m.group(1).strip()
    # 2) LCD/화면 에 <문구> (라고) <동사>
    m = re.search(
        r"(?:LCD|화면|스크린)\s*에?\s*(.+?)\s*(?:라고|이라고|라는|이라는)?\s*" + _LCD_VERBS,
        text,
    )
    if m:
        return m.group(1).strip() or None
    return None


ZONE_KEYBOARD_MAP = {"ㅁ": "A", "ㅠ": "B", "ㅊ": "C", "ㅇ": "D", "ㄷ": "E", "ㄹ": "F", "ㅎ": "G", "ㅗ": "H"}


def _extract_location_names(text: str) -> list[str]:
    """문장에서 A~H 구역명을 DB 위치명(A~H)으로 추출한다.

    한글 입력 상태에서 친 A~H(ㅁ/ㅠ/ㅊ/ㅇ/ㄷ/ㄹ/ㅎ/ㅗ)도 보정한다.
    """
    names: list[str] = []
    patterns = [
        r"(?<![A-Za-z0-9가-힣])([A-Ha-hㅁㅠㅊㅇㄷㄹㅎㅗ])\s*(?:구역|위치|존|zone|로|으로|이동|가|에)?",
        r"(?:로봇|주행로봇)\s*([A-Ha-hㅁㅠㅊㅇㄷㄹㅎㅗ])\s*(?:구역|위치|존|zone|로|으로|이동|가|에)?",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            token = m.group(1)
            name = ZONE_KEYBOARD_MAP.get(token, token.upper())
            if name not in names:
                names.append(name)
    return names


def _extract_emotion(text: str) -> str | None:
    if re.search(r"웃|미소|스마일|행복|기뻐", text):
        return "happy"
    if re.search(r"슬퍼|울어|우울|시무룩", text):
        return "sad"
    if re.search(r"화나|화내|짜증", text):
        return "angry"
    return None


def _apply_overrides(action_type: str, text: str, params: dict[str, Any]) -> dict[str, Any]:
    """자연어 문장에서 파라미터를 추출해 저장된 params 를 덮어쓴다."""
    p = dict(params)
    if action_type == "goto":
        names = _extract_location_names(text)
        if names:
            p["location"] = names[-1]
            p["name"] = names[-1]
        emotion = _extract_emotion(text)
        if emotion:
            p["emotion"] = emotion

    if action_type == "mission_start":
        names = _extract_location_names(text)
        if len(names) >= 2:
            p["names"] = names

    if action_type == "lcd_text":
        msg = _extract_lcd_text(text)
        if msg:
            p["text"] = msg
            p["lcd_text"] = msg
    return p


def _llm_pick(text: str, candidates: list[dict[str, Any]], model: str, url: str) -> tuple[int, float]:
    """후보 중 하나를 LLM 으로 선택. (index, confidence) 반환. 실패 시 (-1, 0)."""
    lines = []
    for i, c in enumerate(candidates):
        phrases = ", ".join(str(p) for p in c["phrases"][:6])
        kind_ko = "액션" if c["kind"] == "action" else "시나리오"
        lines.append(f"[{i}] ({kind_ko}) 이름: {c['name']} / 트리거: {phrases} / 설명: {c['desc']}")
    catalog = "\n".join(lines)
    prompt = (
        "너는 로봇 명령 라우터다. 아래 목록에서 사용자 지시에 가장 잘 맞는 항목 하나를 고른다.\n"
        f"목록:\n{catalog}\n\n"
        f'사용자 지시: "{text}"\n\n'
        '반드시 JSON 으로만 답하라. 형식: {"index": <목록번호 정수 또는 -1>, "confidence": <0~1 실수>}\n'
        "적절한 항목이 없으면 index 를 -1 로 한다."
    )
    try:
        data = _ollama_generate_json(prompt, model=model, url=url)
        idx = int(data.get("index", -1))
        conf = float(data.get("confidence", 0.0))
        if 0 <= idx < len(candidates):
            return idx, conf
    except Exception:
        pass
    return -1, 0.0


def _llm_verify(text: str, item: dict[str, Any], model: str, url: str) -> bool:
    """어휘 근거가 없을 때 '이 명령이 이 동작을 의미하나?'를 LLM 으로 재확인(yes/no).

    소형 모델이 목록에서 아무거나 억지로 고르는 것을 이 관문에서 걸러낸다.
    """
    phrases = ", ".join(str(p) for p in item["phrases"][:6])
    prompt = (
        f'사용자 명령: "{text}"\n'
        f'후보 동작: {item["name"]} (예시 표현: {phrases}; 설명: {item.get("desc", "")})\n\n'
        "이 명령이 위 동작을 의도한 것이 맞으면 match=true, 전혀 관련 없으면 match=false.\n"
        '반드시 JSON 으로만 답하라. 형식: {"match": true 또는 false}'
    )
    try:
        data = _ollama_generate_json(prompt, model=model, url=url)
        return bool(data.get("match", False))
    except Exception:
        return False  # 확인 실패 시 안전하게 거부


def _robot_sort_key(r: Robot) -> tuple[int, int]:
    """이름 끝 숫자 오름차순(없으면 뒤로)으로 정렬하기 위한 키."""
    m = re.search(r"(\d+)\s*$", (r.name or "").strip())
    return (int(m.group(1)) if m else 10_000, r.id)


async def _list_driving_robots(db: AsyncSession) -> list[dict[str, Any]]:
    """활성 주행로봇 목록 (되묻기용). [{number, name}] 이름 끝 숫자 오름차순."""
    rows = (await db.execute(select(Robot).where(Robot.is_active == True))).scalars().all()  # noqa: E712
    driving = sorted([r for r in rows if r.robot_type in ("pinky", "mobile")], key=_robot_sort_key)
    out: list[dict[str, Any]] = []
    for r in driving:
        m = re.search(r"(\d+)\s*$", (r.name or "").strip())
        out.append({"number": int(m.group(1)) if m else None, "name": r.name})
    return out


async def _resolve_robot(db: AsyncSession, number: int | None) -> Robot | None:
    """지시문의 로봇 번호 → 대상 주행로봇 Row.

    pinky/mobile 타입만 후보. 이름 끝 숫자가 정확히 일치하는 로봇 우선.
    (예: '주행로봇 1' → 1. 'JetCobot-1' 같은 팔 로봇은 제외.) 못 찾으면 None.
    """
    if number is None:
        return None
    rows = (await db.execute(select(Robot).where(Robot.is_active == True))).scalars().all()  # noqa: E712
    driving = sorted([r for r in rows if r.robot_type in ("pinky", "mobile")], key=_robot_sort_key)
    pool = driving or sorted(rows, key=_robot_sort_key)
    # 1) 이름 끝 숫자가 정확히 일치
    for r in pool:
        m = re.search(r"(\d+)\s*$", (r.name or "").strip())
        if m and int(m.group(1)) == number:
            return r
    # 2) 폴백: 이름 어딘가에 숫자 포함
    for r in pool:
        if str(number) in (r.name or ""):
            return r
    return None


async def _resolve_robot_id(db: AsyncSession, robot_id: int | None) -> Robot | None:
    """FMS처럼 DB robot_id를 이미 알고 있는 호출에서 대상 주행로봇 Row를 찾는다."""
    if robot_id is None:
        return None
    row = await db.get(Robot, robot_id)
    if row is None or not row.is_active or row.robot_type not in ("pinky", "mobile"):
        return None
    return row


def _extract_named_destination(text: str) -> str | None:
    """Nav2 위치명을 자연어에서 추출한다. A~H 구역은 _extract_location_names가 우선 처리한다."""
    names = _extract_location_names(text)
    if names:
        return names[-1]
    intent = _strip_robot_ref(text)
    patterns = [
        r"(.+?)\s*(?:로|으로|에)\s*(?:가|이동|주행|보내|보내줘|출발)",
        r"(.+?)\s*(?:이동|주행|도착|가줘|가라)",
    ]
    for pattern in patterns:
        m = re.search(pattern, intent)
        if not m:
            continue
        dest = m.group(1).strip(" .,!?\t\n\r'\"")
        dest = re.sub(r"^(?:저기|거기|여기|위치|장소)\s*", "", dest).strip()
        if dest and not re.fullmatch(r"(?:홈|복귀|정지|멈춰|멈춤|취소|중단|순회)", dest):
            return dest
    return None


def _extract_numeric_value(text: str, unit_pattern: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:" + unit_pattern + r")", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _direct_relative_motion_intent(text: str) -> dict[str, Any] | None:
    """LLM 없이 처리할 수 있는 짧은 수동 이동/회전 명령."""
    intent = _strip_robot_ref(text) or text
    lower = intent.lower()

    distance = _extract_numeric_value(lower, r"cm|센치|센티|센티미터")
    meter = _extract_numeric_value(lower, r"m|미터")
    if distance is None and meter is not None:
        distance = meter * 100.0
    angle = _extract_numeric_value(lower, r"도|deg|degree")

    if re.search(r"(뒤|후진|back)", lower):
        cm = max(1.0, min(40.0, distance or 10.0))
        return {
            "action_type": "relative_move",
            "name": f"{cm:g}cm 후진",
            "params": {"direction": "backward", "distance_cm": cm},
            "source": "rule-motor",
        }

    if re.search(r"(앞|전진|forward)", lower):
        cm = max(1.0, min(40.0, distance or 10.0))
        return {
            "action_type": "relative_move",
            "name": f"{cm:g}cm 전진",
            "params": {"direction": "forward", "distance_cm": cm},
            "source": "rule-motor",
        }

    if re.search(r"(왼|좌|left)", lower):
        deg = max(5.0, min(180.0, angle or 20.0))
        return {
            "action_type": "relative_turn",
            "name": f"왼쪽 {deg:g}도 회전",
            "params": {"direction": "left", "angle_deg": deg},
            "source": "rule-motor",
        }

    if re.search(r"(오른|우|right)", lower):
        deg = max(5.0, min(180.0, angle or 20.0))
        return {
            "action_type": "relative_turn",
            "name": f"오른쪽 {deg:g}도 회전",
            "params": {"direction": "right", "angle_deg": deg},
            "source": "rule-motor",
        }

    return None


def _direct_nav_intent(text: str) -> dict[str, Any] | None:
    """LLM 없이 처리할 수 있는 기본 Nav2 명령을 판별한다."""
    intent = _strip_robot_ref(text) or text
    if re.search(r"(정지|멈춰|멈춤|스톱|stop|취소|중단)", intent, re.IGNORECASE):
        return {"action_type": "mission_stop", "name": "Nav2 정지", "params": {}}
    if re.search(r"(홈|home|복귀|원점)", intent, re.IGNORECASE):
        return {"action_type": "home", "name": "홈 복귀", "params": {}}
    names = _extract_location_names(intent)
    if len(names) >= 2 and re.search(r"(순회|미션|巡回|돌아|돌고|방문)", intent):
        return {"action_type": "mission_start", "name": "Nav2 순회", "params": {"names": names, "loop": bool(re.search(r"(반복|계속|loop)", intent, re.IGNORECASE))}}
    dest = _extract_named_destination(text)
    if dest and re.search(r"(가|이동|주행|보내|출발|도착)", intent):
        return {"action_type": "goto", "name": f"{dest} 이동", "params": {"location": dest, "name": dest}}
    return None


async def _run_direct_nav_intent(
    nav: dict[str, Any],
    text: str,
    number: int | None,
    execute: bool,
    confirm: bool,
    headers: dict,
    db: AsyncSession,
) -> dict[str, Any]:
    action_type = nav["action_type"]
    name = nav["name"]

    if number is None:
        return {
            "matched": True,
            "needs_robot": True,
            "kind": "action",
            "id": None,
            "name": name,
            "action_type": action_type,
            "robots": await _list_driving_robots(db),
            "message": f"'{name}'을(를) 어느 로봇에서 실행할까요?",
            "source": nav.get("source", "rule-nav2"),
        }

    robot = await _resolve_robot(db, number)
    preview = {
        "matched": True,
        "kind": "action",
        "id": None,
        "name": name,
        "confidence": 1.0,
        "source": nav.get("source", "rule-nav2"),
        "robot_number": number,
        "target_robot": robot.name if robot else None,
        "action_type": action_type,
    }
    if not execute:
        return preview

    path, cmd_body, note = resolve_learned_command(action_type, nav.get("params", {}))
    if path is None:
        return {**preview, "result": {"success": False, "action_type": action_type, "message": note or "Nav2 명령을 만들 수 없습니다."}}

    if action_type in DRIVING_ACTIONS and not confirm:
        return {
            **preview,
            "result": {
                "success": False,
                "requires_confirm": True,
                "action_type": action_type,
                "message": f"'{name}'은(는) 로봇을 실제로 움직입니다. 확인 후 다시 실행하세요.",
                "note": note,
            },
        }

    http_ok, response_text = await asyncio.to_thread(_dispatch_call, action_type, path, headers, cmd_body, robot)
    return {
        **preview,
        "result": {
            "success": _command_success(action_type, http_ok, response_text),
            "response": response_text,
            "action_type": action_type,
            "path": path,
            "note": note,
        },
    }


@router.post("/interpret")
async def interpret(
    body: InterpretIn,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    rdb: AsyncSession = Depends(get_robot_db),
    _admin: Admin = Depends(get_current_admin),
):
    number = _robot_number(body.text)

    direct_nav = _direct_relative_motion_intent(body.text) or _direct_nav_intent(body.text)
    if direct_nav is not None:
        return await _run_direct_nav_intent(
            direct_nav,
            body.text,
            number,
            body.execute,
            body.confirm,
            _auth_headers(request),
            db,
        )

    actions = (await db.execute(select(RobotLearnedAction).where(RobotLearnedAction.enabled == True))).scalars().all()  # noqa: E712
    scenarios = (await db.execute(select(RobotLearnedScenario).where(RobotLearnedScenario.enabled == True))).scalars().all()  # noqa: E712
    items = _catalog(list(actions), list(scenarios))
    if not items:
        return {"matched": False, "message": "실행 가능한 학습 액션/시나리오가 없습니다."}

    # 로봇 지칭어를 제거한 '의도' 텍스트로 매칭 (흔한 '로봇' 단어 오매칭 방지)
    intent = _strip_robot_ref(body.text) or body.text

    # 1) 결정적 점수로 후보 정렬. 점수가 0으로 평평할 때 시드 액션이 잘려나가지 않도록
    #    상한을 넉넉히 둔다(등록 수가 많아지면 조정).
    ranked = sorted(items, key=lambda it: _score(intent, it), reverse=True)
    candidates = ranked[:25]

    # 2) LLM 선택 (Ollama 설정 로드)
    settings = (await rdb.execute(select(AiModelSettings))).scalars().first()
    model = (settings.selected_model if settings else None) or OLLAMA_DEFAULT_MODEL
    url = (settings.ollama_url if settings else None) or OLLAMA_URL
    idx, conf = await asyncio.to_thread(_llm_pick, intent, candidates, model, url)

    chosen: dict[str, Any] | None = None
    source = "llm"
    if idx >= 0:
        chosen = candidates[idx]
    else:
        # 3) LLM 실패/미매칭 → 결정적 점수 폴백
        top = ranked[0]
        if _score(intent, top) > 0:
            chosen = top
            source = "keyword"

    # 4) 어휘 근거가 없는 LLM 선택은 재확인. (스마일→정지 같은 억지 매칭 차단하되,
    #    '울어봐'→슬픔 처럼 글자는 안 겹쳐도 의미가 맞는 경우는 통과시킨다.)
    if chosen is not None and _score(intent, chosen) <= 0:
        verified = await asyncio.to_thread(_llm_verify, intent, chosen, model, url)
        if not verified:
            return {
                "matched": False,
                "message": f'"{body.text}"에 맞는 액션/시나리오가 없습니다. (등록된 트리거와 일치하지 않음)',
                "suggestion": chosen["name"],
            }

    if chosen is None:
        return {"matched": False, "message": f'"{body.text}"에 맞는 액션/시나리오를 찾지 못했습니다.'}

    # 로봇 번호가 없으면 임의로 정하지 말고 되묻는다.
    if number is None:
        robots = await _list_driving_robots(db)
        return {
            "matched": True,
            "needs_robot": True,
            "kind": chosen["kind"],
            "id": chosen["id"],
            "name": chosen["name"],
            "action_type": chosen["row"].action_type if chosen["kind"] == "action" else None,
            "robots": robots,
            "message": f"'{chosen['name']}'을(를) 어느 로봇에서 실행할까요?",
        }

    robot = await _resolve_robot(db, number)

    preview = {
        "matched": True,
        "kind": chosen["kind"],
        "id": chosen["id"],
        "name": chosen["name"],
        "confidence": conf,
        "source": source,
        "robot_number": number,
        "target_robot": robot.name if robot else None,
    }

    if not body.execute:
        return preview

    headers = _auth_headers(request)
    row = chosen["row"]
    if chosen["kind"] == "action":
        params = _apply_overrides(row.action_type, body.text, _loads(row.params, {}))
        result = await asyncio.to_thread(_run_action_row, row, params, body.confirm, headers, robot)
    else:
        result = await _run_scenario_row(row, body.confirm, headers, robot)
    return {**preview, "result": result}


@router.post("/stop-all")
async def stop_all(
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    _admin: Admin = Depends(get_current_admin),
):
    """모든 주행로봇 리셋 — 모터 정지 + LCD 화면 클리어. 챗의 '전체 정지/취소' 버튼용."""
    rows = (await db.execute(select(Robot).where(Robot.is_active == True))).scalars().all()  # noqa: E712
    driving = sorted([r for r in rows if r.robot_type in ("pinky", "mobile")], key=_robot_sort_key)
    headers = _auth_headers(request)
    results: list[dict[str, Any]] = []
    for r in driving:
        base = f"http://{r.ip_address}:{r.port}"
        m_ok, m_resp = call_local_api("/api/robot/motor/stop", headers, {}, base)
        l_ok, l_resp = call_local_api("/api/robot/lcd/stop", headers, {}, base)  # 표정/문구 클리어
        results.append({
            "name": r.name,
            "success": _effective_success(m_ok, m_resp),
            "lcd_cleared": _effective_success(l_ok, l_resp),
        })
    ok = all(x["success"] for x in results) if results else False
    return {"success": ok, "count": len(results), "results": results}
