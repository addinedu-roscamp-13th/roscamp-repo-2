from follower_BT.recovery import (
    search_command, DrivePolicy, SCAN_SEC, TURN_SEC, PEEK_SEC,
    IDLE, FOLLOWING, PEEK, SEARCHING,
)


class _Owner:
    def __init__(self, cx=50):
        self.cx = cx
        self.is_owner = True


def test_timeline_scan_turn_scan_turn_giveup():
    assert search_command(0.0)[2] == "SCAN1"
    assert search_command(SCAN_SEC + 0.1)[2] == "TURN180"
    assert search_command(SCAN_SEC + TURN_SEC + 0.1)[2] == "SCAN2"
    assert search_command(2 * SCAN_SEC + TURN_SEC + 0.1)[2] == "TURN180B"
    ang, done, phase = search_command(2 * SCAN_SEC + 2 * TURN_SEC + 1.0)
    assert done and ang == 0.0 and phase == "GIVEUP"


def test_scan_oscillates_both_ways_no_lkd():
    early = search_command(0.01)[0]
    mid = search_command(SCAN_SEC / 2.0)[0]
    assert early > 0 and mid < 0        # sweeps + then - regardless of any LKD


def test_starts_idle_then_following_on_owner():
    p = DrivePolicy(lambda det, w: {"drive": "FWD"})
    assert p.state == IDLE
    out = p.step(_Owner(), 100, 0.05, registered=True)
    assert p.state == FOLLOWING and out["state"] == FOLLOWING


def test_not_registered_is_idle_stop():
    p = DrivePolicy(lambda det, w: {"drive": "FWD"})
    out = p.step(None, 100, 0.05, registered=False)
    assert p.state == IDLE and out["drive"] == "STOP"


def test_lost_peeks_then_searches_then_idle():
    p = DrivePolicy(lambda det, w: {"drive": "FWD"})
    p.step(_Owner(cx=50), 100, 0.05)                            # following (centered)
    out = p.step(None, 100, 0.1, registered=True)               # lost -> PEEK first
    assert p.state == PEEK and out["drive"] == "PEEK"
    out = p.step(None, 100, PEEK_SEC + 0.1)                     # peek done -> SEARCH
    assert p.state == SEARCHING and out["drive"] == "SEARCH"
    out = p.step(None, 100, 2 * SCAN_SEC + 2 * TURN_SEC + 1.0)  # exhaust -> IDLE
    assert p.state == IDLE and out["drive"] == "STOP"


def test_peek_turns_toward_last_direction():
    p = DrivePolicy(lambda det, w: {"drive": "FWD"})
    p.step(_Owner(cx=10), 100, 0.05)                            # owner last on LEFT
    out = p.step(None, 100, 0.1, registered=True)
    assert p.state == PEEK and out["angular_z"] > 0             # peek LEFT (+)


def test_peek_turns_right_when_last_seen_right():
    p = DrivePolicy(lambda det, w: {"drive": "FWD"})
    p.step(_Owner(cx=90), 100, 0.05)                            # owner last on RIGHT
    out = p.step(None, 100, 0.1, registered=True)
    assert out["angular_z"] < 0                                 # peek RIGHT (-)


def test_reacquire_during_peek_returns_to_following():
    p = DrivePolicy(lambda det, w: {"drive": "FWD"})
    p.step(_Owner(), 100, 0.05)
    p.step(None, 100, 0.1)                                       # peeking (lost)
    out = p.step(_Owner(), 100, 0.05)                           # found again
    assert p.state == FOLLOWING and out["state"] == FOLLOWING
