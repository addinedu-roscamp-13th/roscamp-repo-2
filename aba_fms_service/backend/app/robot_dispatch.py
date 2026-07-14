"""학습 액션(action_type) → 실제 로봇 API 호출 매핑 (공용).

robot_learning 의 run 엔드포인트와 chat.py 디스패처가 이 모듈을 공유한다.
프론트(robot-learning 페이지)가 저장하는 고수준 action_type 을 이미 존재하는
백엔드 엔드포인트로 변환한다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

ROBOT_SERVER_URL = "http://127.0.0.1:9001"

# 실제로 로봇을 '주행'시키는 action_type.
# CLAUDE.md 규칙: 주행/도킹은 사람 확인(confirm) 없이 자동 실행 금지.
DRIVING_ACTIONS: set[str] = {
    "goto",
    "mission_start",
    "parking_start",
    "home",
    "human_follow_start",
    "relative_move",
    "relative_turn",
}

# 주행(nav2)은 로봇 온보드 HTTP 가 아니라 중앙서버 /api/control/*(→ROS→nav2) 로 라우팅해야 한다.
# 실행부에서 base_url=중앙서버, path 에 ?robot_id=<DB id> 를 붙인다.
CONTROL_ACTIONS: set[str] = {"goto", "mission_start", "mission_stop", "home"}


def call_local_api(
    path: str,
    headers: dict | None = None,
    data: dict | None = None,
    base_url: str = ROBOT_SERVER_URL,
    timeout: int = 15,
) -> tuple[bool, str]:
    """로컬(또는 대상 로봇) FastAPI 로 POST 프록시. (성공여부, 본문) 반환."""
    url = f"{base_url}{path}"
    headers = headers or {}
    try:
        payload = json.dumps(data or {}).encode("utf-8")
        req_headers = {"Content-Type": "application/json"}
        if "Authorization" in headers:
            req_headers["Authorization"] = headers["Authorization"]
        req = urllib.request.Request(url, data=payload, headers=req_headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return True, response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            err_msg = e.read().decode("utf-8")
        except Exception:
            err_msg = e.reason
        return False, f"HTTP {e.code}: {err_msg}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _bounded_duration(value: float) -> float:
    return max(0.05, min(3.0, round(float(value), 2)))


def _lcd_text_body(params: dict[str, Any]) -> dict[str, Any]:
    text = params.get("text") or params.get("lcd_text") or ""
    return {
        "text": text,
        "font_name": params.get("font_name", "default"),
        "font_size": int(params.get("font_size", 24)),
        "color": params.get("color", "#ffffff"),
        "bg_color": params.get("bg_color", "#000000"),
        "align": params.get("align", "center"),
        "scroll": bool(params.get("scroll", False)),
        "scroll_speed": int(params.get("scroll_speed", 3)),
    }


def resolve_learned_command(action_type: str, params: dict[str, Any] | None) -> tuple[str | None, dict[str, Any], str | None]:
    """학습 액션을 (path, body, note) 로 변환.

    - path 가 None 이면 실행 불가(미지원). note 에 사유.
    - path 가 "" 이면 API 호출 없이 성공 처리(예: wait — 시나리오 러너가 sleep 처리).
    """
    p = params or {}

    # 주행 계열은 중앙서버 /api/control/*(→ROS→nav2). robot_id 는 실행부에서 부착.
    if action_type == "goto":
        name = (p.get("location") or p.get("name") or "").strip()
        if not name:
            return None, {}, "goto: 이동할 위치(location)가 없습니다."
        return "/api/control/goto", {"name": name}, None

    if action_type == "mission_start":
        return "/api/control/mission/start", {
            "names": p.get("names", []),
            "loop": bool(p.get("loop", False)),
        }, None

    if action_type == "mission_stop":
        return "/api/control/mission/stop", {}, None

    if action_type == "home":
        return "/api/control/home", {}, None

    if action_type == "parking_start":
        # 도킹(주차) 시작. zone 으로 먼저 이동하는 조합은 시나리오(goto→parking_start)로 구성.
        body: dict[str, Any] = {}
        if p.get("mode"):
            body["approach"] = p["mode"]
        note = None
        if p.get("zone"):
            note = f"parking_start: zone '{p['zone']}' 이동은 미포함(현재 위치에서 도킹). 이동이 필요하면 goto→parking_start 시나리오로 구성하세요."
        return "/api/robot/dock/start", body, note

    if action_type == "emotion":
        return "/api/robot/lcd/emotion", {"emotion": p.get("emotion", "basic")}, None

    if action_type == "buzzer":
        body = {"preset": p.get("preset", "bell")}
        for k in ("count", "freq", "duration"):
            if p.get(k) is not None:
                body[k] = p[k]
        return "/api/robot/buzzer", body, None

    # 멜로디 연주(엘리제/학교종)는 짧은 비프(buzzer)와 다른 엔드포인트다.
    # buzzer 로 잘못 매칭되면 엉뚱한 비프가 나므로 별도 action_type 로 분리한다.
    if action_type == "buzzer_melody":
        name = (p.get("melody") or p.get("name") or "").strip()
        if not name:
            return None, {}, "buzzer_melody: 연주할 멜로디(melody)가 없습니다."
        return "/api/robot/buzzer/melody/play", {"melody": name}, None

    if action_type == "relative_move":
        cm = max(1.0, min(40.0, abs(float(p.get("distance_cm", 10.0)))))
        direction = str(p.get("direction") or "forward")
        speed = int(p.get("speed", 35))
        sign = -1 if direction == "backward" else 1
        duration = _bounded_duration(cm / 12.0)
        note = f"상대 이동은 모터 시간 기반 추정입니다: {cm:g}cm, duration={duration}s"
        return "/api/robot/motor/move", {"left": sign * speed, "right": sign * speed, "duration": duration}, note

    if action_type == "relative_turn":
        deg = max(5.0, min(180.0, abs(float(p.get("angle_deg", 20.0)))))
        direction = str(p.get("direction") or "left")
        speed = int(p.get("speed", 35))
        duration = _bounded_duration((deg / 90.0) * 1.1)
        if direction == "right":
            left, right = speed, -speed
        else:
            left, right = -speed, speed
        note = f"상대 회전은 모터 시간 기반 추정입니다: {deg:g}도, duration={duration}s"
        return "/api/robot/motor/move", {"left": left, "right": right, "duration": duration}, note

    if action_type == "stop":
        # 모터 정지(주행 아님 — 즉시 실행). nav 미션 정지는 mission_stop 사용.
        return "/api/robot/motor/stop", {}, None

    if action_type == "lcd_text":
        return "/api/robot/lcd/text", _lcd_text_body(p), None

    if action_type == "lcd_image":
        filename = (p.get("filename") or "").strip()
        if not filename:
            return None, {}, "lcd_image: 표시할 이미지 filename 이 없습니다."
        return "/api/robot/lcd/image/select", {"filename": filename}, None

    if action_type == "human_follow_start":
        # 사람 추종 루프 시작(로봇 온보드). HumanFollowConfig 필드만 통과시키고 나머지는 기본값.
        allowed = {
            "ai_server_url", "model", "confidence", "target_area",
            "forward_speed", "turn_speed", "deadband", "invert_steering",
            "loop_hz", "lost_timeout_s", "labels", "max_duration_s",
        }
        body = {k: v for k, v in p.items() if k in allowed and v is not None}
        return "/api/robot/human-follow/start", body, None

    if action_type == "wait":
        return "", {}, None  # API 아님 — 러너가 처리

    return None, {}, f"미지원 action_type: {action_type}"
