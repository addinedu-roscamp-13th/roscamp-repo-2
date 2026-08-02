"""패널 요청의 ROS2 통로 — libi_gui 가 HTTP 대신 토픽으로 같은 요청을 보낸다.

## 무엇이 오가나

    로봇 /panel_request  ──bridge──▶  111 /{key}/panel_request   {"id","op",...args}
    로봇 /panel_result   ◀──bridge──  111 /{key}/panel_result    {"id","ok",...body}

`op` 는 기존 HTTP 엔드포인트와 1:1 이고, `...args`/`...body` 도 그 라우터의 요청/응답
스키마 **그대로**다. 그래서 패널은 파싱 코드를 바꾸지 않고 통로만 갈아탄다.

## 왜 라우터 로직을 함수로 빼지 않았나

빼도 됐지만 **뺄 이유가 없다.** 핸들러들은 `Depends` 없이 pydantic 모델 하나만 받는
평범한 코루틴이라 여기서 그대로 `await` 하면 된다. 로직을 옮기면 HTTP 경로까지 같이
흔들리는데, 그쪽은 이 통로가 죽었을 때 쓰는 폴백이라 건드리지 않는 편이 낫다.

## 왜 서비스(srv)가 아니라 토픽 두 개인가

domain_bridge 가 서비스를 중계하지 못한다(`config/domain_bridge.template.yaml` 의
fsm_transition 주석과 같은 이유). 그래서 이 레포는 요청/결과를 id 로 상관시키는
토픽 쌍으로 통일돼 있고, 여기도 그 모양을 따른다.

## 방향 주의

`fleet_cmd`(중앙→로봇 명령) 와 **반대다.** 여기서는 로봇이 요청하고 서버가 승인한다.
그래서 상관 상태(`id` → 콜백·타임아웃)는 FMS 가 아니라 **패널 쪽에** 있고, FMS 는 받은
`id` 를 그대로 되돌려 주기만 한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder

log = logging.getLogger(__name__)

#: 로봇 쪽 토픽 이름. 브릿지가 서버 도메인에서 `/{key}/...` 로 개명한다.
REQUEST_TOPIC = "panel_request"
RESULT_TOPIC = "panel_result"

_pub_lock = threading.Lock()


def _ops() -> dict[str, tuple[Any, Callable]]:
    """op → (요청 모델, 핸들러 코루틴).

    라우터를 **지연 임포트**한다 — `guide.py` 가 `fleet_telemetry` 를 import 하고
    `fleet_telemetry` 가 이 모듈을 import 하므로, 최상단에 두면 순환이 된다.
    """
    from app.routers import admin_follow, guide, panel
    return {
        "guide.request":    (guide.GuideRequest, guide.request_guide),
        "guide.release":    (guide.ReleaseRequest, guide.release_guide),
        "follow.request":   (admin_follow.AdminFollowRequest, admin_follow.request_admin_follow),
        "follow.release":   (admin_follow.AdminFollowRequest, admin_follow.release_admin_follow),
        "panel.transition": (panel.TransitionRequest, panel.transition),
    }


def handle(payload: dict) -> dict:
    """요청 하나를 처리해 회신 봉투를 만든다.

    **예외를 밖으로 내보내지 않는다.** 응답이 아예 안 가면 패널은 타임아웃까지
    "요청 중" 화면에 갇히고, 사용자에게는 사유가 안 보인다. 실패도 답은 보낸다.
    """
    req_id = str(payload.get("id") or "")
    op = str(payload.get("op") or "")

    ops = _ops()
    if op not in ops:
        log.warning("[panel] 알 수 없는 요청 op=%s id=%s", op, req_id)
        return {"id": req_id, "ok": False, "reason": f"알 수 없는 요청입니다: {op}"}

    model_cls, fn = ops[op]
    # ⚠️ [2026-08-02] **이 통로를 지나간 요청은 서버 로그에 흔적이 남아야 한다.**
    #
    #   패널이 HTTP 대신 이 토픽 쌍을 쓰면 uvicorn 액세스 로그가 안 남는다. 그런데
    #   아래 `except` 가 예외를 **로그 없이** 삼켜 패널에만 돌려주므로, 서버 로그만
    #   보는 사람에게는 **요청이 온 적조차 없는 것처럼 보인다.**
    #   실측 2026-08-02: 길잡이가 분명히 돌았는데 `[guide]` 로그가 한 줄도 없어서
    #   "요청이 이 백엔드에 안 온다" 와 "와서 조용히 실패한다" 를 가릴 수 없었다.
    log.info("[panel] %s id=%s", op, req_id)
    try:
        args = {k: v for k, v in payload.items() if k not in ("id", "op")}
        # 핸들러는 `asyncio.to_thread` 로만 블로킹을 다룬다 — 새 루프에서 돌려도 된다.
        body = asyncio.run(fn(model_cls(**args)))
    except Exception as e:  # noqa: BLE001 — 어떤 실패든 사유를 실어 돌려보낸다
        # 스택까지 남긴다. 사유 문자열만으로는 어느 줄에서 터졌는지 못 찾는다.
        log.exception("[panel] %s 처리 실패 id=%s", op, req_id)
        return {"id": req_id, "ok": False, "reason": f"{type(e).__name__}: {e}"}

    out = jsonable_encoder(body)
    if not isinstance(out, dict):
        out = {"result": out}
    # 봉투가 이긴다 — 핸들러 응답에 id/ok 가 있어도 상관 키를 덮어쓰지 못하게.
    return {**out, "id": req_id, "ok": True}


def attach(node, string_msg, prefix: str) -> None:
    """fleet_telemetry 의 ROS 노드에 로봇 하나 몫의 요청/결과 토픽을 붙인다."""
    pub = node.create_publisher(string_msg, f"{prefix}/{RESULT_TOPIC}", 10)

    def on_request(msg) -> None:
        try:
            payload = json.loads(msg.data)
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        def _work() -> None:
            res = handle(payload)
            m = string_msg()
            m.data = json.dumps(res, ensure_ascii=False)
            with _pub_lock:
                pub.publish(m)

        # 핸들러가 미션 PC 응답을 기다리는 블로킹 호출이라, executor 스레드에서 그대로
        # 돌리면 그동안 **텔레메트리 수신이 통째로 멈춘다.** 요청은 사람 터치라 드물다.
        threading.Thread(target=_work, daemon=True, name="panel-bridge").start()

    node.create_subscription(string_msg, f"{prefix}/{REQUEST_TOPIC}", on_request, 10)
