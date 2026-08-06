"""`NavPhase` — 목표 실행 단계 판단.

증상은 하나("명령을 줬는데 안 움직인다")인데 원인이 넷이었다. 여기서 둘을 닫고,
넷 다 `snapshot()` 으로 구분 가능해야 한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.nav_phase import (  # noqa: E402
    ABORTED, AWAITING_ACCEPT, DRIVING, DUPLICATE, IDLE, LOST, QUEUE, QUEUED,
    RECOVERING, REJECTED, SEND, NavPhase,
)

A = (1.0, 2.0, 0.0)
B = (3.0, 4.0, 1.57)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _driving(clock=None):
    """A 로 출발해 정상 주행 중인 상태."""
    clock = clock or Clock()
    p = NavPhase(clock)
    assert p.request(A) == SEND
    p.on_sent(A)
    p.on_accepted()
    p.on_feedback(distance_remaining=1.2, recoveries=0)
    return p, clock


# ── 결정 ────────────────────────────────────────────────────────────────

def test_처음_목표는_보낸다():
    p = NavPhase(Clock())
    assert p.request(A) == SEND


def test_같은_목표가_살아있으면_안_보낸다():
    """선점하면 controller 가 cmd_vel 을 끊어 모터 워치독이 선다 — 그래서 무시가 맞다."""
    p, _ = _driving()
    assert p.request(A) == DUPLICATE
    assert p.phase == DRIVING


def test_다른_목표는_대기열로():
    p, _ = _driving()
    assert p.request(B) == QUEUE
    assert p.phase == QUEUED
    assert p.queued == B


def test_대기_목표는_인플라이트가_비어야_나간다():
    p, _ = _driving()
    p.request(B)
    assert p.take_queued() is None, "인플라이트가 살아 있는데 다음 목표를 내보냈다"
    p.on_result(ok=True)
    assert p.take_queued() == B
    assert p.take_queued() is None, "같은 대기 목표를 두 번 내보냈다"


# ── 유실 판정 — 이 파일의 핵심 ────────────────────────────────────────────

def test_응답이_안_오면_유실로_보고_다시_보낸다():
    """응답 콜백이 유실되면 인플라이트를 비우는 곳이 없어 **이후 모든 목표가 영원히
    대기열로만 간다**(codex P0)."""
    clock = Clock()
    p = NavPhase(clock)
    p.request(A)
    p.on_sent(A)                       # AWAITING_ACCEPT 인 채로 응답이 안 온다
    clock.t += p.accept_ttl_sec - 0.1
    assert p.request(A) == DUPLICATE, "아직 기다릴 시간이다"
    clock.t += 0.2
    assert p.request(A) == SEND, "응답이 영영 안 와도 계속 기다린다(예전 버그)"


def test_주행_중_feedback_이_끊겨도_서버가_살아있으면_안_건드린다():
    """⚠️ feedback 부재만으로 LOST 를 확정하면 오탐이다 — DDS 손실·executor 정지로도
    끊긴다(codex 반박). 유예를 넘긴 **뒤 서버 생존까지** 실패해야 유실이다."""
    p, clock = _driving()
    clock.t += p.feedback_grace_sec + 5.0
    assert p.request(A, server_ready=True) == DUPLICATE, "서버가 멀쩡한데 목표를 갈아치웠다"
    assert p.request(A, server_ready=False) == SEND, "서버도 없는데 계속 기다린다"


def test_유예_안에서는_서버가_없어도_안_건드린다():
    """잠깐의 끊김으로 목표를 갈아치우면 그때마다 로봇이 멈칫한다."""
    p, clock = _driving()
    clock.t += p.feedback_grace_sec - 0.1
    assert p.request(A, server_ready=False) == DUPLICATE


def test_회복_중에는_재전송하지_않는다():
    """nav2 회복은 살아 있는 동작이다 — 재전송하면 회복 진행이 초기화된다."""
    p, clock = _driving()
    p.on_feedback(distance_remaining=1.0, recoveries=2)
    assert p.phase == RECOVERING
    assert p.request(A) == DUPLICATE


# ── 취소 ────────────────────────────────────────────────────────────────

def test_취소는_세대를_올린다():
    """`send` 결정과 실제 전송 사이(액션서버 대기 최대 2초)에 취소가 끼면, 호출자가
    이 값을 대조해 **취소가 조용히 무시되는 창**을 닫는다(codex P0)."""
    p, _ = _driving()
    before = p.cancel_seq
    assert p.cancel() == before + 1
    assert p.inflight is None and p.queued is None and p.phase == IDLE


def test_취소는_대기_목표도_지운다():
    p, _ = _driving()
    p.request(B)
    p.cancel()
    assert p.take_queued() is None, "취소했는데 대기 목표가 살아남았다"


# ── 보고 ────────────────────────────────────────────────────────────────

def test_desired_와_inflight_를_둘_다_싣는다():
    """FMS 가 '내가 시킨 것'과 '로봇이 하는 것'을 대조할 수 있어야 한다 — 이게 이
    보고의 존재 이유다."""
    p, _ = _driving()
    p.request(B)                       # 대기열로 갔다 = 아직 A 를 달린다
    s = p.snapshot()
    assert s["desired"]["x"] == 3.0, "시킨 것은 B 다"
    assert s["inflight"]["x"] == 1.0, "달리는 것은 아직 A 다"
    assert s["queued"]["x"] == 3.0
    assert s["phase"] == QUEUED


def test_원인_넷이_스냅샷에서_구분된다():
    """증상이 하나로 뭉뚱그려지던 것을 가르는 것이 목적이다."""
    # ① 물림: 대기 상태가 오래 지속
    p, clock = _driving()
    p.request(B)
    clock.t += 120.0
    assert p.snapshot()["phase"] == QUEUED and p.snapshot()["since"] > 100

    # ② 죽은 목표: feedback 이 한 번도 안 옴
    q = NavPhase(Clock())
    q.request(A); q.on_sent(A); q.on_accepted()
    assert q.snapshot()["feedback_age"] is None

    # ③ 회복 반복: recoveries 가 증가
    r, _ = _driving()
    r.on_feedback(recoveries=5)
    assert r.snapshot()["recoveries"] == 5 and r.snapshot()["phase"] == RECOVERING

    # ④ 거절: phase 로 바로 드러난다
    s = NavPhase(Clock())
    s.request(A); s.on_sent(A); s.on_rejected()
    assert s.snapshot()["phase"] == REJECTED and s.snapshot()["inflight"] is None


def test_실패_결과는_aborted_로_남는다():
    """성공과 실패를 같은 idle 로 뭉치면 '도착했나 죽었나'를 FMS 가 못 가린다."""
    p, _ = _driving()
    p.on_result(ok=False)
    assert p.phase == ABORTED
    p2, _ = _driving()
    p2.on_result(ok=True)
    assert p2.phase == IDLE


def test_보낸_뒤에는_awaiting_accept_다():
    p = NavPhase(Clock())
    p.request(A)
    p.on_sent(A)
    assert p.phase == AWAITING_ACCEPT
    assert p.snapshot()["inflight"]["x"] == 1.0
