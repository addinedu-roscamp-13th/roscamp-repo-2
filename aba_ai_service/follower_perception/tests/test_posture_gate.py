"""자세 게이트. 로봇도 카메라도 없이 규칙만 검증한다."""
from follower_perception.posture_gate import PostureGate, STANDING, SIDE


def test_standing_allows():
    assert PostureGate(unknown_limit=10).update("Standing") is True


def test_lying_stops_immediately():
    g = PostureGate(unknown_limit=10)
    g.update("Standing")
    assert g.update("Lying") is False


def test_calibrating_stops():
    """기준을 재는 동안은 판정이 성립하지 않는다 — 틀린 기준으로 달리는 것보다 서 있는 게 낫다."""
    assert PostureGate(unknown_limit=10).update("Calibrating") is False


def test_unknown_holds_previous_until_limit():
    g = PostureGate(unknown_limit=3)
    g.update("Standing")
    assert g.update("Unknown") is True       # 1
    assert g.update("Unknown") is True       # 2
    assert g.update("Unknown") is False      # 3 — 한계 도달


def test_unknown_at_limit_stays_stopped():
    g = PostureGate(unknown_limit=3)
    g.update("Standing")
    for _ in range(5):
        g.update("Unknown")
    assert g.allowed is False


def test_unknown_counter_resets_on_standing():
    g = PostureGate(unknown_limit=3)
    g.update("Standing")
    g.update("Unknown")
    g.update("Unknown")
    g.update("Standing")
    assert g.unknown_run == 0
    assert g.update("Unknown") is True


def test_unknown_after_lying_stays_stopped():
    """직전이 정지였으면 Unknown 은 그 정지를 유지한다 — 유지가 곧 안전이다."""
    g = PostureGate(unknown_limit=3)
    g.update("Lying")
    assert g.update("Unknown") is False


def test_recovers_from_lying_on_standing():
    g = PostureGate(unknown_limit=3)
    g.update("Lying")
    assert g.update("Standing") is True


def test_none_posture_does_not_block():
    """판정 소스가 없는 배포(옛 payload)에서 추종이 통째로 죽으면 안 된다."""
    assert PostureGate(unknown_limit=3).update(None) is True


def test_none_after_stop_releases():
    """소스가 사라지면 근거가 없어진 것이다 — 근거 없이 계속 세워두지 않는다."""
    g = PostureGate(unknown_limit=3)
    g.update("Lying")
    assert g.update(None) is True


def test_unrecognised_string_is_treated_as_unknown():
    """모르는 문자열이 '주행 허용'으로 새는 것이 가장 위험하다."""
    g = PostureGate(unknown_limit=2)
    g.update("Standing")
    g.update("Sitting?")
    assert g.update("Sitting?") is False


def test_reset_clears_state():
    g = PostureGate(unknown_limit=2)
    g.update("Lying")
    g.reset()
    assert g.allowed is True
    assert g.unknown_run == 0


def test_limit_below_one_is_clamped():
    """0 을 주면 나눗셈이 아니라 '첫 Unknown 에 정지' 가 되게 한다(무한 허용 방지)."""
    g = PostureGate(unknown_limit=0)
    g.update("Standing")
    assert g.update("Unknown") is False


def test_side_stops_immediately():
    """측면이면 bbox 폭이 줄어 거리 추정이 '멀다' 로 오판한다 — 즉시 세운다."""
    g = PostureGate()
    assert g.update(SIDE) is False


def test_side_recovers_on_standing():
    g = PostureGate()
    g.update(SIDE)
    assert g.update(STANDING) is True


def test_side_does_not_consume_the_unknown_budget():
    """즉시 정지 판정은 Unknown 연속 카운터와 무관하다."""
    g = PostureGate(unknown_limit=3)
    g.update(SIDE)
    assert g.unknown_run == 0
