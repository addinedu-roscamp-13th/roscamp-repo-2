"""nav2 목표 취소의 수명 규칙. 실기 없이 재현한다.

원래 결함: `send_nav_goal()` 이 `send_goal_async()` 의 future 를 버려서 응답 콜백이
영영 안 불렸고, 그 결과 `cancel_active_goal()` 이 **어떤 주행도 취소하지 못했다**.
길잡이의 "사람을 놓치면 멈춘다" 가 화면에만 뜨고 로봇은 계속 달렸다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.nav_goal_tracker import NavGoalTracker  # noqa: E402


class FakeHandle:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.canceled = False

    def cancel_goal_async(self):
        self.canceled = True
        return object()


class RaisingHandle(FakeHandle):
    def cancel_goal_async(self):
        raise RuntimeError("링크 끊김")


def test_cancel_after_response_cancels():
    t = NavGoalTracker()
    gen = t.begin()
    gh = FakeHandle()
    t.on_response(gh, gen)
    assert t.cancel() is True
    assert gh.canceled is True


def test_cancel_before_response_still_cancels():
    """취소가 goal 응답보다 먼저 와도, 응답 시점에 취소가 적용돼야 한다."""
    t = NavGoalTracker()
    gen = t.begin()
    assert t.cancel() is False          # 아직 핸들이 없다 — 대기로 넘어간다
    gh = FakeHandle()
    t.on_response(gh, gen)
    assert gh.canceled is True
    assert t.active_handle is None


def test_new_goal_does_not_cancel_previous_handle():
    """[2026-07-30] **뒤집힌 계약.** 새 목표를 낼 때 이전 것을 취소하지 않는다.

    예전에는 여기서 취소했다. 그런데 nav2 는 같은 액션에 새 goal 이 오면 선점으로
    이전 목표를 알아서 대체한다. 명시적 취소를 보내면 nav2 가 그걸 **취소로 처리해
    바퀴를 세운다**(`Cancellation was successful. Stopping the robot.`) — 그게 순회가
    웨이포인트마다 멈추던 원인이었다. 자세한 실측은 nav_goal_tracker.begin() 주석.

    ⚠️ 이 테스트가 깨지면 그 증상이 돌아온 것이다. 되돌리기 전에 begin() 주석부터 읽어라.
    """
    t = NavGoalTracker()
    g1 = t.begin()
    gh1 = FakeHandle()
    t.on_response(gh1, g1)
    t.begin()                            # 새 목표 — 선점이 처리한다
    assert gh1.canceled is False, "새 목표가 이전 것을 취소했다 — 로봇이 선다"
    assert t.active_handle is None       # 추적은 놓는다(세대가 바뀌었으므로)


def test_explicit_cancel_still_cancels():
    """정지 명령 경로는 **그대로**여야 한다 — '사람을 놓치면 멈춘다'가 여기 걸려 있다."""
    t = NavGoalTracker()
    g = t.begin()
    gh = FakeHandle()
    t.on_response(gh, g)
    assert t.cancel() is True
    assert gh.canceled is True


def test_generation_still_advances_without_cancel():
    """취소는 안 해도 세대는 올라가야 한다 — 낡은 응답을 가려내는 근거다."""
    t = NavGoalTracker()
    g1 = t.begin()
    g2 = t.begin()
    assert g2 != g1
    assert t.is_current(g2) and not t.is_current(g1)


def test_stale_generation_response_is_dropped():
    """대체된 목표의 응답이 늦게 와도 활성 핸들을 덮지 않는다."""
    t = NavGoalTracker()
    g1 = t.begin()
    g2 = t.begin()
    late = FakeHandle()
    t.on_response(late, g1)              # 낡은 세대
    assert t.active_handle is None
    fresh = FakeHandle()
    t.on_response(fresh, g2)
    assert t.active_handle is fresh


def test_rejected_goal_is_not_stored():
    t = NavGoalTracker()
    gen = t.begin()
    t.on_response(FakeHandle(accepted=False), gen)
    assert t.active_handle is None


def test_none_handle_is_not_stored():
    t = NavGoalTracker()
    gen = t.begin()
    t.on_response(None, gen)
    assert t.active_handle is None


def test_cancel_pending_resets_on_new_goal():
    """취소 의사가 다음 목표까지 새어 나가면, 방금 낸 목표가 즉시 취소된다."""
    t = NavGoalTracker()
    t.begin()
    t.cancel()                           # 대기 상태
    gen = t.begin()                      # 새 목표 — 여기서 의사가 지워져야 한다
    gh = FakeHandle()
    t.on_response(gh, gen)
    assert gh.canceled is False
    assert t.active_handle is gh


def test_cancel_failure_does_not_raise():
    """취소 실패로 호출자 스레드를 죽이지 않는다."""
    t = NavGoalTracker()
    gen = t.begin()
    t.on_response(RaisingHandle(), gen)
    t.cancel()                           # 예외가 새어 나오면 여기서 실패한다


def test_clear_drops_handle_and_pending():
    t = NavGoalTracker()
    gen = t.begin()
    t.on_response(FakeHandle(), gen)
    t.clear()
    assert t.active_handle is None
    gh = FakeHandle()
    t.on_response(gh, gen)               # clear 후에도 같은 세대면 다시 받는다
    assert t.active_handle is gh


# ── codex 리뷰(2026-07-27)에서 나온 결함 ────────────────────────────────────

def test_stale_accepted_response_is_cancelled_not_just_dropped():
    """그냥 버리면 **고아가 된다** — nav2 는 그 목표를 받아들였으므로,
    우리가 잊는다고 로봇이 멈추지 않는다."""
    t = NavGoalTracker()
    g1 = t.begin()
    t.begin()                            # 새 목표가 이전 세대를 대체
    late = FakeHandle()
    t.on_response(late, g1)              # 늦게 도착한 옛 세대의 수락 응답
    assert late.canceled is True
    assert t.active_handle is None


def test_cancel_before_response_then_new_goal_still_cancels_the_old():
    """begin() 이 pending 을 지우므로, 옛 응답은 세대 불일치 경로로 취소돼야 한다."""
    t = NavGoalTracker()
    g1 = t.begin()
    t.cancel()                           # 아직 핸들 없음 → 대기
    t.begin()                            # 새 목표
    old = FakeHandle()
    t.on_response(old, g1)
    assert old.canceled is True


def test_is_current_distinguishes_generations():
    """결과 콜백이 자기 목표의 결과인지 가리는 근거."""
    t = NavGoalTracker()
    g1 = t.begin()
    g2 = t.begin()
    assert t.is_current(g1) is False
    assert t.is_current(g2) is True
    assert t.is_current(None) is True     # 세대를 안 실어 보낸 옛 호출부 호환
