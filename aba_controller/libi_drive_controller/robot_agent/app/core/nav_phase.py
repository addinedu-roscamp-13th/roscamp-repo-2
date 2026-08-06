"""목표 실행 단계 — **ROS 없이 도는 순수 상태기.**

`nav_goal_tracker.NavGoalTracker` 와 같은 이유로 뺐다: `RosNode` 는 `_bridge_thread()`
안에 중첩돼 있어 rclpy 없이 못 만들고, 그러면 이 판단들을 로봇 없이 검증할 수 없다.
여기 두면 `tests/test_nav_phase.py` 가 전부 덮는다.

## 무엇을 정하나

1. **들어온 목표를 어떻게 할 것인가** — 보낸다 / 대기열 / 무시(같은 목표)
2. **인플라이트가 죽었나** — 생존 신호가 끊겼는가
3. **지금 상태를 어떻게 보고할 것인가** — FMS 가 대조할 수 있는 형태로

## 왜 필요했나 (2026-08-06)

"명령을 줬는데 안 움직인다"의 원인이 넷인데 증상이 하나였다:

  · 인플라이트 영구 물림 — 응답/결과 콜백이 유실되면 비우는 곳이 없다
  · 같은 목표 heartbeat 이 **죽은 목표**까지 흡수
  · twist_mux `fsm_motion_lock` 이 조용히 차단
  · nav2 회복 무한 반복

앞의 둘을 여기서 닫고, 넷 다 `snapshot()` 으로 구분 가능하게 만든다.

## ⚠️ 인플라이트 자체에는 만료를 걸지 않는다

정상 주행이 몇 분씩 이어진다 — 나이로 자르면 멀쩡한 주행을 끊는다. 만료를 거는 것은
**생존 신호**다: 응답 대기(짧다)와 feedback 침묵이다.

## ⚠️ feedback 부재만으로 LOST 를 확정하지 않는다

DDS 손실·executor 정지·서버 일시정지·네트워크 단절로도 끊긴다(codex 반박 2026-08-06).
유예를 넘긴 **뒤에 액션서버 생존까지 확인**해서 둘 다 실패해야 LOST 다.
그래서 `is_lost()` 가 `server_ready` 를 인자로 받는다 — 판단은 여기서, 조회는 호출자가.
"""

#: 들어온 목표에 대한 결정
SEND = "send"            # 지금 보낸다
QUEUE = "queue"          # 현재 목표가 끝난 뒤에 보낸다(선점 금지)
DUPLICATE = "duplicate"  # 같은 목표가 이미 살아서 돈다 — 아무것도 안 한다

IDLE = "idle"
AWAITING_ACCEPT = "awaiting_accept"
DRIVING = "driving"
RECOVERING = "recovering"
QUEUED = "queued"
LOST = "lost"
ABORTED = "aborted"
REJECTED = "rejected"

#: 목표가 같다고 볼 오차(m, rad). 상위층이 같은 홉을 재발행할 때 부동소수 차이를 흡수한다.
SAME_TARGET_EPS = 1e-4


class NavPhase:
    """한 로봇의 nav 목표 실행 단계.

    `now` 는 단조 시계 함수다(시험이 갈아끼운다). 락은 **호출자가** 잡는다 —
    여기서 잡으면 `RosNode` 의 기존 `_nav_goal_lock` 과 이중이 된다.
    """

    def __init__(self, now, accept_ttl_sec: float = 8.0,
                 feedback_grace_sec: float = 6.0):
        self._now = now
        self.accept_ttl_sec = float(accept_ttl_sec)
        self.feedback_grace_sec = float(feedback_grace_sec)

        self.phase = IDLE
        self.phase_at = now()
        self.desired = None        # 상위층이 마지막으로 요구한 목표
        self.inflight = None       # 액션서버에 던져 살아 있는 목표
        self.queued = None         # 현재 목표 뒤에 보낼 목표(latest-only 단일 슬롯)
        self.sent_at = None
        self.feedback_at = None
        self.distance_remaining = None
        self.recoveries = 0
        #: 취소 세대. `send` 결정과 실제 전송 사이에 취소가 끼면 값이 달라진다 —
        #: 호출자가 전송 직전에 대조해서 **취소가 조용히 무시되는 창**을 닫는다.
        self.cancel_seq = 0

    # ── 결정 ────────────────────────────────────────────────────────────
    def request(self, target, server_ready: bool = True) -> str:
        """상위층이 목표를 요구했다. `SEND` / `QUEUE` / `DUPLICATE` 를 돌려준다.

        같은 목표라도 **죽었으면 다시 보낸다** — 그게 heartbeat 분기가 죽은 목표까지
        흡수하던 자리다. 살아 있으면 안 보낸다(선점하면 controller 가 cmd_vel 을 끊어
        모터 워치독이 선다).
        """
        self.desired = target
        if self.inflight is None:
            return SEND
        if self.is_lost(server_ready):
            self.inflight = None
            self._set(LOST)
            return SEND
        if _same(self.inflight, target):
            return DUPLICATE
        self.queued = target
        self._set(QUEUED)
        return QUEUE

    def is_lost(self, server_ready: bool = True) -> bool:
        """인플라이트가 죽은 것으로 보이나. `server_ready` 는 호출자가 조회해 넘긴다."""
        if self.inflight is None:
            return False
        now = self._now()
        if self.phase == AWAITING_ACCEPT:
            return (self.sent_at is not None
                    and now - self.sent_at >= self.accept_ttl_sec)
        if self.phase in (DRIVING, RECOVERING):
            last = self.feedback_at or self.sent_at
            if last is None:
                return False
            if now - last < self.feedback_grace_sec:
                return False
            return not server_ready       # 유예를 넘겼고 서버도 없다 → 죽었다
        return False

    def take_queued(self):
        """대기 목표를 꺼낸다. 보낼 게 없거나 아직 인플라이트가 있으면 None."""
        if self.queued is None or self.inflight is not None:
            return None
        target, self.queued = self.queued, None
        return target

    # ── 사건 ────────────────────────────────────────────────────────────
    def on_sent(self, target) -> None:
        self.inflight = target
        self.sent_at = self._now()
        self.feedback_at = None
        self.distance_remaining = None
        self.recoveries = 0
        self._set(AWAITING_ACCEPT)

    def on_accepted(self) -> None:
        self._set(DRIVING)

    def on_rejected(self) -> None:
        self.inflight = None
        self._set(REJECTED)

    def on_feedback(self, distance_remaining=None, recoveries=None) -> None:
        self.feedback_at = self._now()
        if distance_remaining is not None:
            self.distance_remaining = float(distance_remaining)
        if recoveries is not None:
            self.recoveries = int(recoveries)
        # 회복 중은 "살아 있지만 못 간다" — 재전송하면 회복 진행이 초기화된다.
        self._set(RECOVERING if self.recoveries > 0 else DRIVING)

    def on_result(self, ok: bool) -> None:
        self.inflight = None
        self._set(IDLE if ok else ABORTED)

    def cancel(self) -> int:
        """취소. 새 세대를 돌려준다 — 호출자가 전송 직전에 대조한다."""
        self.inflight = None
        self.queued = None
        self.desired = None
        self.cancel_seq += 1
        self._set(IDLE)
        return self.cancel_seq

    # ── 보고 ────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        """`/fleet_status` 에 실린다.

        `desired` 와 `inflight` 를 **둘 다** 싣는 것이 핵심이다 — FMS 가 "내가 시킨 것"과
        "로봇이 하는 것"을 직접 대조할 수 있다. 지금까지는 그 대조가 불가능했다.
        """
        now = self._now()
        return {
            "desired": _xyz(self.desired),
            "inflight": _xyz(self.inflight),
            "queued": _xyz(self.queued),
            "phase": self.phase,
            "since": round(now - self.phase_at, 1),
            "distance_remaining": self.distance_remaining,
            "recoveries": self.recoveries,
            # None 이면 feedback 이 **한 번도** 안 왔다 — accepted 인데 nav2 가 안 도는
            # 상태의 신호다. 나이가 커지는 것은 조용해진 것이다.
            "feedback_age": (None if self.feedback_at is None
                             else round(now - self.feedback_at, 1)),
        }

    # ── 내부 ────────────────────────────────────────────────────────────
    def _set(self, phase: str) -> None:
        if phase != self.phase:
            self.phase = phase
            self.phase_at = self._now()


def _same(a, b) -> bool:
    return a is not None and b is not None and \
        all(abs(x - y) < SAME_TARGET_EPS for x, y in zip(a, b))


def _xyz(t):
    if t is None:
        return None
    return {"x": round(t[0], 3), "y": round(t[1], 3), "yaw": round(t[2], 3)}
