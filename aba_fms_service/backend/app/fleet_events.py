"""주문 진행 중에 일어난 **사건**을 모아 두고 밀어 준다 — "도착했다"를 전할 통로.

## 왜 상태만으로는 부족한가

관제 화면과 회원 화면은 둘 다 **상태를 폴링**한다:

    관제   GET /api/fleet/orders          1.5초마다
    회원   GET /api/member/requests       화면을 열 때

그래서 "지금 어떤 상태인가"는 알 수 있지만 **"방금 무슨 일이 있었나"는 모른다.**
책이 자리에 도착한 순간과, 도착한 지 10분 지난 순간이 화면에서 똑같이 보인다.
알림을 띄우려면 상태가 아니라 **사건**이 필요하다.

사건은 놓치면 끝이므로(상태와 달리 다시 읽을 수 없다) 여기서 **보관**한다.
`since(seq)` 로 "내가 마지막으로 본 것 다음부터" 받아 갈 수 있어, 화면이 잠깐 닫혀
있었어도 놓친 알림을 뒤늦게 받을 수 있다.

## 두 가지 소비 방식을 다 지원하는 이유

    push   웹소켓 — 관제처럼 계속 열려 있는 화면
    pull   GET /api/fleet/events?since=N — 회원 웹처럼 폴링하는 쪽

회원 웹(aba_service)은 별도 서비스라 FMS 의 웹소켓을 직접 물기 번거롭다. 같은 사건을
두 방식으로 내보내면 양쪽 다 코드를 크게 안 바꾸고 붙는다.

## 보관 정책

메모리에만 둔다. 재기동하면 사라진다 — 주문(orchestrator)도 메모리에만 있으므로
사건만 살아남아 봐야 가리킬 대상이 없다. 개수 상한을 두어 오래 도는 서버에서
메모리가 늘어나지 않게 한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

log = logging.getLogger("fleet_events")

#: 보관할 사건 수. 하나가 ~200바이트라 넉넉히 잡아도 가볍다.
#: 이 수를 넘겨 밀려난 사건은 `since()` 로 못 받는다 — 화면이 그만큼 오래 닫혀 있었다면
#: 알림보다 현재 상태(주문 목록)를 보는 게 맞다.
MAX_EVENTS = 500

#: 구독자 큐 크기. 넘치면 **가장 오래된 것부터 버린다** — 느린 구독자 하나 때문에
#: 발행하는 쪽(오케스트레이터 락 안)이 막히면 배차 전체가 멈춘다.
LISTENER_QUEUE_MAX = 64

_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_seq = 0
#: (asyncio 루프, asyncio.Queue) — 웹소켓 핸들러가 등록한다.
_listeners: list[tuple[Any, Any]] = []


def publish(kind: str, **fields: Any) -> dict[str, Any]:
    """사건 하나를 기록하고 구독자에게 민다. **절대 예외를 밖으로 내지 않는다.**

    오케스트레이터의 락 안에서 불리므로, 여기서 실패하거나 막히면 배차가 멈춘다.
    알림을 못 보내는 것보다 나쁜 결과다.
    """
    global _seq
    try:
        with _lock:
            _seq += 1
            event = {"seq": _seq, "ts": time.time(), "kind": kind, **fields}
            _events.append(event)
            targets = list(_listeners)
    except Exception:  # noqa: BLE001
        log.exception("[events] 기록 실패 kind=%s", kind)
        return {}

    for loop, queue in targets:
        try:
            loop.call_soon_threadsafe(_offer, queue, event)
        except Exception:  # noqa: BLE001 — 이미 닫힌 루프
            pass
    return event


def _offer(queue: Any, event: dict[str, Any]) -> None:
    """큐가 차 있으면 가장 오래된 것을 버리고 넣는다."""
    if queue.full():
        try:
            queue.get_nowait()
        except Exception:  # noqa: BLE001
            return
    try:
        queue.put_nowait(event)
    except Exception:  # noqa: BLE001
        pass


def since(seq: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """`seq` 보다 큰 사건들. 폴링하는 쪽이 "놓친 것부터" 받아 가는 통로다."""
    with _lock:
        out = [e for e in _events if e["seq"] > seq]
    return out[-limit:] if limit and len(out) > limit else out


def latest_seq() -> int:
    with _lock:
        return _seq


def add_listener(loop: Any, queue: Any) -> None:
    with _lock:
        _listeners.append((loop, queue))


def remove_listener(loop: Any, queue: Any) -> None:
    with _lock:
        try:
            _listeners.remove((loop, queue))
        except ValueError:
            pass


def listener_count() -> int:
    with _lock:
        return len(_listeners)


def reset() -> None:
    """시험용. 사건·순번을 비운다(구독자는 그대로)."""
    global _seq
    with _lock:
        _events.clear()
        _seq = 0
