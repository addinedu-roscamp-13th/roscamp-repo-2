"""사람이 막았거나 도킹이 잡아 둔 정점을 유효시간과 소유자별로 들고 있다.

## ⚠️ 유효시간은 선택이 아니다

`libi_fleet_msgs/msg/RobotHold.msg` 가 같은 이유를 이미 적어 두었다 — **푸는 쪽이 죽으면
그 길이 영영 막힌다.** 그래서 만료를 읽는 쪽(여기)이 스스로 지운다. 해제는
`ttl_sec=0` 으로 다시 보내는 것이다.

## ⚠️ 소유자별로 따로 잡는다 (R4)

같은 정점을 사람 차단과 서가 도킹 잠금이 함께 잡을 수 있다. `owner` 로 구분하지 않으면
나중 요청이 앞선 요청을 덮어쓰고, 한쪽의 `ttl_sec=0` 해제가 남의 차단까지 지워버린다.
그래서 내부는 `(node, owner) -> 만료시각` 으로 잡는다 — 정점은 **살아 있는 owner 가
하나라도 있으면** 차단 상태다.
"""
from __future__ import annotations

import threading


class NodeBlockRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._until: dict[tuple[int, str], float] = {}
        self._reason: dict[tuple[int, str], str] = {}
        self._pending_expired: set[int] = set()

    def set(self, node: int, ttl_sec: float, now: float, owner: str = "", reason: str = "") -> None:
        """`owner` 별로 따로 잡는다. `ttl_sec <= 0` 은 **그 owner 의 것만** 푼다."""
        key = (int(node), str(owner))
        with self._lock:
            if float(ttl_sec) <= 0.0:
                self._until.pop(key, None)
                self._reason.pop(key, None)
                return
            self._until[key] = float(now) + float(ttl_sec)
            self._reason[key] = str(reason)

    def active(self, now: float) -> list:
        """차단된 정점 번호(오름차순) — 살아 있는 owner 가 하나라도 있으면 차단이다.
        만료된 (node, owner) 항목은 여기서 지워진다."""
        with self._lock:
            self._prune(now)
            return sorted({node for node, _owner in self._until})

    def owners_of(self, node: int) -> list:
        """이 정점을 지금 잡고 있는 owner 들(오름차순, 진단·사건용)."""
        node = int(node)
        with self._lock:
            return sorted(owner for (n, owner) in self._until if n == node)

    def reason_of(self, node: int) -> str:
        """살아 있는 owner 중 아무 사유(없으면 "")."""
        node = int(node)
        with self._lock:
            for (n, _owner), reason in self._reason.items():
                if n == node:
                    return reason
            return ""

    def expired_since(self, now: float) -> list:
        """이전 호출 이후 자연 만료로 완전히 풀린 정점(오름차순). 한 번 보고하면 지운다 —
        같은 만료를 두 번 보고하지 않는다(사건 중복 발행 방지)."""
        with self._lock:
            self._prune(now)
            result = sorted(self._pending_expired)
            self._pending_expired.clear()
            return result

    def clear(self) -> None:
        with self._lock:
            self._until.clear()
            self._reason.clear()
            self._pending_expired.clear()

    def _prune(self, now: float) -> None:
        """만료된 (node, owner) 항목을 지운다. 그걸로 정점이 완전히 풀리면
        `_pending_expired` 에 올린다. 호출자가 `self._lock` 을 들고 있어야 한다."""
        dead = [k for k, t in self._until.items() if float(now) >= t]
        if not dead:
            return
        touched = {node for node, _owner in dead}
        for k in dead:
            self._until.pop(k, None)
            self._reason.pop(k, None)
        still_blocked = {node for node, _owner in self._until}
        self._pending_expired.update(touched - still_blocked)
