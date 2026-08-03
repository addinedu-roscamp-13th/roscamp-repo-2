"""FMS(관제) 서버-투-서버 클라이언트.

## 왜 서버가 대신 부르나
`/api/fleet/order` 는 FMS 의 **관리자 인증**이 걸려 있다. 회원 브라우저가 직접 부르려면
FMS 관리자 자격증명을 클라이언트에 내려보내야 하는데 그건 불가하다. 그래서 도서관 백엔드가
서비스 계정으로 대신 호출한다 — 회원 인증은 이쪽(`aba_service`)에서 끝내고, FMS 에는
서버만 접근한다. `robot_control.py` 가 이미 쓰는 서버-투-서버 관례를 따른다.

## 실패 처리
예외를 올리지 않고 `(ok, value_or_reason)` 을 돌려준다. 호출부(`routers/delivery.py`)가
사용자에게 보여줄 거절 사유를 만들어야 하므로, 실패도 값으로 다뤄야 한다.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

FMS_URL = os.getenv("FMS_URL", "http://127.0.0.1:9001").rstrip("/")
FMS_USER = os.getenv("FMS_SERVICE_USER", "admin")
FMS_PASSWORD = os.getenv("FMS_SERVICE_PASSWORD", "admin1234")
TIMEOUT_SEC = 8

_token: str | None = None
_lock = threading.Lock()


def _request(
    path: str,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    timeout: int = TIMEOUT_SEC,
) -> tuple[int, str]:
    """(status_code, body_text). 네트워크 실패는 (0, 사유)."""
    req = urllib.request.Request(
        f"{FMS_URL}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            return e.code, str(e.reason)
    except Exception as e:  # noqa: BLE001 — 연결 거부/타임아웃 등
        return 0, str(e)


#: FMS 로그인 경로. 도서관 웹(`/api/admin/auth/login`)과 **다르다** — FMS 는 `/api/auth/login`.
FMS_LOGIN_PATH = "/api/auth/login"


def _login(timeout: int = TIMEOUT_SEC) -> tuple[bool, str]:
    status, text = _request(
        FMS_LOGIN_PATH,
        "POST",
        {"username": FMS_USER, "password": FMS_PASSWORD},
        timeout=timeout,
    )
    if status == 0:
        return False, f"FMS 연결 실패: {text}"
    if status != 200:
        return False, f"FMS 로그인 실패({status})"
    try:
        return True, json.loads(text)["access_token"]
    except (ValueError, KeyError):
        return False, "FMS 로그인 응답 형식 오류"


def _authed(
    path: str, method: str, body: dict | None, timeout: int = TIMEOUT_SEC
) -> tuple[bool, str]:
    """토큰을 붙여 호출하고, 401 이면 **한 번만** 재로그인 후 재시도한다.

    재시도를 1회로 제한하는 이유: 주문 생성은 멱등하지 않다. 무한 재시도하면 같은 주문이
    여러 건 들어갈 수 있다.
    """
    global _token
    with _lock:
        if _token is None:
            ok, value = _login(timeout=timeout)
            if not ok:
                return False, value
            _token = value
        token = _token

    status, text = _request(path, method, body, token, timeout=timeout)
    if status == 401:
        with _lock:
            ok, value = _login(timeout=timeout)
            if not ok:
                return False, value
            _token = value
            token = _token
        status, text = _request(path, method, body, token, timeout=timeout)

    if status == 0:
        return False, f"FMS 연결 실패: {text}"
    if status >= 400:
        return False, f"FMS 거절({status}): {text[:200]}"
    return True, text


def submit_order(
    *,
    book: str = "",
    pickup: str = "",
    dropoff: str,
    requester: str = "",
    priority: int = 0,
    kind: str = "delivery",
    tier: int = 0,
    row: int = 0,
) -> tuple[bool, str]:
    """주문 접수. 성공하면 `(True, task_id)`, 실패하면 `(False, 사유)`.

    `kind` 가 다리 구성을 정한다 — `delivery`(주행→집기→주행→놓기 4다리) 또는
    `navigate`(주행 1다리). 주행만 하는 지시(정리·파견)를 배달로 내보내면 로봇이 있지도
    않은 책을 집으러 간다.

    `tier`/`row` 는 **로봇팔용 서가 좌표**다(`books.tier`/`books.row`). `pickup` 정점은
    "어느 서가"까지만 말해주고, 팔이 손을 뻗으려면 층·줄이 필요하다. `0` 은 "정보 없음"
    이고 팔이 시각으로 찾는다 — 추측해서 채우지 않는다.
    """
    ok, text = _authed(
        "/api/fleet/order",
        "POST",
        {
            "kind": kind,
            "book": book,
            "pickup": pickup,
            "dropoff": dropoff,
            "requester": requester,
            "priority": priority,
            "tier": tier,
            "row": row,
        },
    )
    if not ok:
        return False, text
    try:
        return True, json.loads(text)["task_id"]
    except (ValueError, KeyError):
        return False, "FMS 주문 응답 형식 오류"


def request_transition(
    robot: str, target_state: str, force: bool = False
) -> tuple[bool, dict]:
    """로봇 모드(미션 FSM 상태) 변경 요청. `(호출성공, 응답)`.

    ## 왜 `/api/fleet/mode` 가 아닌가
    FMS 에는 모드를 건드리는 경로가 둘이다.
      - `POST /api/fleet/mode` → `fleet_link.set_robot_mode`. **FMS 가 들고 있는 관측값만**
        바꾼다(fleet_node 의 `robot_mode_` 맵). 자기 docstring 에 "sim·디버그 전용,
        여기서 바꾸면 로봇의 실제 FSM 과 어긋난다"고 적혀 있다. 로봇은 아무 것도 모른다.
      - `POST /api/fsm/transition` → `fsm_link.request_transition`. `/libi/fsm_transition_request`
        로 **로봇에게 실제로 요청**하고, 로봇의 `state_io` 가 전이표로 판정해 수락/거부한다.
    사서가 「복귀」를 눌렀을 때 로봇이 진짜 복귀해야 하므로 후자를 쓴다.

    `ok=False` 는 FMS 를 못 불렀다는 뜻이고, 로봇이 거부한 것은 `ok=True` + `accepted=False`
    로 온다(전이표에 없는 간선 등). 둘은 사서에게 다르게 보여야 한다.
    """
    ok, text = _authed(
        "/api/fsm/transition",
        "POST",
        {"robot_id": robot, "target_state": target_state, "force": force},
    )
    if not ok:
        return False, {"reason": text}
    try:
        return True, json.loads(text)
    except ValueError:
        return False, {"reason": "FMS 전이 응답 형식 오류"}


def list_orders() -> tuple[bool, list[dict]]:
    """FMS 주문 스냅샷. 실패하면 `(False, [])` — 화면은 진행상황 없이도 떠야 한다."""
    ok, text = _authed("/api/fleet/orders", "GET", None)
    if not ok:
        return False, []
    try:
        return True, json.loads(text).get("orders", [])
    except ValueError:
        return False, []


def list_events(since: int = 0, limit: int = 50) -> tuple[bool, list[dict], int]:
    """FMS 주문 **사건** (도착·완료 알림). `(성공?, 사건들, 마지막 seq)`.

    주문 **상태**(`list_orders`)와 다른 것을 가져온다. 상태로는 "방금 도착했다"를
    표현할 수 없다 — 도착한 순간과 10분 뒤가 똑같이 보인다. 알림은 사건이어야 한다.

    `since` 에 마지막으로 본 seq 를 주면 그 뒤 것만 온다. 실패해도 화면은 떠야 하므로
    `(False, [], since)` 로 조용히 물러난다 — 알림이 없다고 도서 목록까지 막을 이유는 없다.
    """
    ok, text = _authed(f"/api/fleet/events?since={int(since)}&limit={int(limit)}", "GET", None)
    if not ok:
        return False, [], since
    try:
        data = json.loads(text)
    except ValueError:
        return False, [], since
    return True, data.get("events", []), int(data.get("latest", since))


def cancel_order(task_id: str) -> tuple[bool, str]:
    ok, text = _authed(f"/api/fleet/order/{task_id}/cancel", "POST", None)
    return ok, text


def assign_order(task_id: str, robot: str) -> tuple[bool, str]:
    ok, text = _authed(
        f"/api/fleet/order/{task_id}/assign", "POST", {"robot": robot}
    )
    return ok, text


def fleet_snapshot(*, timeout: int = TIMEOUT_SEC) -> tuple[bool, dict]:
    """로봇 관측 스냅샷(위치·상태·배터리·교통). 실패하면 `(False, {})`.

    `timeout` 은 기본 `TIMEOUT_SEC`(8초, 재로그인 재시도 포함 최대 ~24초) — 대부분의
    호출부는 그대로 쓰면 된다. "알림 먼저" 설계인 호출부(`ops_extra._zone_from_fleet`)만
    짧은 값을 명시로 넘긴다.
    """
    ok, text = _authed("/api/fleet/snapshot", "GET", None, timeout=timeout)
    if not ok:
        return False, {}
    try:
        return True, json.loads(text).get("snapshot", {})
    except ValueError:
        return False, {}


def waypoints_shared() -> tuple[bool, dict]:
    """FMS 공유 내비 그래프(정본 waypoint.yaml) — `{"vertices": {name: pose}, "lanes": [...]}`.

    사서 실시간 모니터링 지도가 현재 노드·간선을 그리려면 필요하다. FMS 의
    `/api/control/waypoints/shared` 는 관리자 인증이 걸려 있어 브라우저가 직접 못 부르므로,
    도서관 백엔드가 서비스 계정으로 대신 읽어 내려준다(FMS 자격증명 미노출). waypoint.yaml
    이 바뀌면 자동 반영된다. 실패하면 `(False, {})` — 지도는 로봇 점만이라도 떠야 한다.
    """
    ok, text = _authed("/api/control/waypoints/shared", "GET", None)
    if not ok:
        return False, {}
    try:
        data = json.loads(text)
    except ValueError:
        return False, {}
    return True, {"vertices": data.get("vertices", {}), "lanes": data.get("lanes", [])}
