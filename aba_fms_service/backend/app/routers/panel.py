"""터치패널(libi_gui) 전용 상태 전이.

## 왜 따로 있나

패널의 `/api/fsm/transition` 호출은 **전부 401 로 죽고 있었다** — 그 라우터는
`get_current_admin` 을 요구하는데 패널에는 토큰이 없다(`fsm.py:189`). 증상은 두 가지로
나타났다:

  - 관리자 화면에서 에러를 해제해도 **로봇은 ERROR 인 채**라 다음 상태 수신에 화면이
    도로 "에러"가 된다.
  - 더 나쁜 쪽: **비상정지가 로봇에 전달되지 않았다.** 화면엔 ⛔ 가 뜨는데 로봇 FSM 은
    그대로 굴러간다. 안전 기능이 화면 안에서만 동작하고 있었다는 뜻이다.

관리자 추종(`admin_follow.py`)·길잡이(`guide.py`)와 같은 자리다 — 패널은 익명이고,
FMS 가 대신 `fsm_link` 로 전이를 소유한다.

## 아무 상태나 열어주지 않는다

인증이 없는 통로이므로 **패널이 실제로 필요한 세 가지**만 받는다. 배달·충전·복귀 같은
관제 소관 상태는 여전히 관리자 인증이 붙은 `/api/fsm/transition` 을 거쳐야 한다.

  ERROR   비상정지 — 지연 없이 항상 받는다(force). 막을 이유가 없는 유일한 방향이다.
  IDLE    비상정지·에러 해제
  PATROL  대기 중인 로봇을 응대 가능 상태로 (ui_touch 는 PATROL 에서만 INTERACTING 으로
          갈 수 있다 — libi_modes/registry.py 의 전이표)
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from app import fsm_link

router = APIRouter(prefix="/api/robot/panel", tags=["panel"])

#: 패널이 요청할 수 있는 상태. 이 밖은 관리자 인증 경로로만 바꾼다.
ALLOWED_STATES = frozenset({"ERROR", "IDLE", "PATROL"})


class TransitionRequest(BaseModel):
    robot_id: str
    target_state: str
    #: 비상정지는 어느 상태에서든 즉시 들어가야 해서 패널이 force 를 준다.
    force: bool = False


@router.post("/transition")
async def transition(req: TransitionRequest):
    target = req.target_state.strip().upper()
    if target not in ALLOWED_STATES:
        return {"accepted": False,
                "reason": f"패널이 요청할 수 없는 상태입니다: {target} "
                          f"(허용: {', '.join(sorted(ALLOWED_STATES))})"}

    result = await asyncio.to_thread(
        fsm_link.request_transition, req.robot_id, target, req.force)
    if result is None:
        return {"accepted": False,
                "reason": "미션 PC FSM 링크에 연결할 수 없습니다 (브릿지 미기동 또는 오프라인)."}
    return {"accepted": bool(result.get("accepted")),
            "current_state": result.get("current_state"),
            "reason": result.get("reason") or ""}
