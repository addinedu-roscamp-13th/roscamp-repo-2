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
    path: str, method: str = "GET", body: dict | None = None, token: str | None = None
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
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
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


def _login() -> tuple[bool, str]:
    status, text = _request(
        FMS_LOGIN_PATH,
        "POST",
        {"username": FMS_USER, "password": FMS_PASSWORD},
    )
    if status == 0:
        return False, f"FMS 연결 실패: {text}"
    if status != 200:
        return False, f"FMS 로그인 실패({status})"
    try:
        return True, json.loads(text)["access_token"]
    except (ValueError, KeyError):
        return False, "FMS 로그인 응답 형식 오류"


def _authed(path: str, method: str, body: dict | None) -> tuple[bool, str]:
    """토큰을 붙여 호출하고, 401 이면 **한 번만** 재로그인 후 재시도한다.

    재시도를 1회로 제한하는 이유: 주문 생성은 멱등하지 않다. 무한 재시도하면 같은 주문이
    여러 건 들어갈 수 있다.
    """
    global _token
    with _lock:
        if _token is None:
            ok, value = _login()
            if not ok:
                return False, value
            _token = value
        token = _token

    status, text = _request(path, method, body, token)
    if status == 401:
        with _lock:
            ok, value = _login()
            if not ok:
                return False, value
            _token = value
            token = _token
        status, text = _request(path, method, body, token)

    if status == 0:
        return False, f"FMS 연결 실패: {text}"
    if status >= 400:
        return False, f"FMS 거절({status}): {text[:200]}"
    return True, text


def submit_order(
    *,
    book: str,
    pickup: str,
    dropoff: str,
    requester: str = "",
    priority: int = 0,
) -> tuple[bool, str]:
    """배달 주문 접수. 성공하면 `(True, task_id)`, 실패하면 `(False, 사유)`."""
    ok, text = _authed(
        "/api/fleet/order",
        "POST",
        {
            "book": book,
            "pickup": pickup,
            "dropoff": dropoff,
            "requester": requester,
            "priority": priority,
        },
    )
    if not ok:
        return False, text
    try:
        return True, json.loads(text)["task_id"]
    except (ValueError, KeyError):
        return False, "FMS 주문 응답 형식 오류"


def list_orders() -> tuple[bool, list[dict]]:
    """FMS 주문 스냅샷. 실패하면 `(False, [])` — 화면은 진행상황 없이도 떠야 한다."""
    ok, text = _authed("/api/fleet/orders", "GET", None)
    if not ok:
        return False, []
    try:
        return True, json.loads(text).get("orders", [])
    except ValueError:
        return False, []


def cancel_order(task_id: str) -> tuple[bool, str]:
    ok, text = _authed(f"/api/fleet/order/{task_id}/cancel", "POST", None)
    return ok, text


def assign_order(task_id: str, robot: str) -> tuple[bool, str]:
    ok, text = _authed(
        f"/api/fleet/order/{task_id}/assign", "POST", {"robot": robot}
    )
    return ok, text


def fleet_snapshot() -> tuple[bool, dict]:
    """로봇 관측 스냅샷(위치·상태·배터리·교통). 실패하면 `(False, {})`."""
    ok, text = _authed("/api/fleet/snapshot", "GET", None)
    if not ok:
        return False, {}
    try:
        return True, json.loads(text).get("snapshot", {})
    except ValueError:
        return False, {}
