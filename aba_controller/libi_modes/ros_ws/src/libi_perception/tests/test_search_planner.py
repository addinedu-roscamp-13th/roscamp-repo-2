from types import SimpleNamespace
from libi_perception.search_planner import search_command


def _cfg(**over):
    base = dict(SEARCH_HOLD_SEC=10.0, SEARCH_SCAN_SEC=4.0,
                ANGULAR_Z_SEARCH=0.35, SEARCH_TURN_ANGLE=3.14159)
    base.update(over)
    return SimpleNamespace(**base)


def test_phase1_hold_is_stationary():
    ang, done = search_command(5.0, _cfg())
    assert ang == 0.0
    assert done is False


def test_phase1_scan_rotates():
    ang, done = search_command(12.0, _cfg(), lkd=1.0)   # 10..14 scan
    assert ang != 0.0
    assert done is False


def test_turn_phase_rotates():
    ang, done = search_command(16.0, _cfg())            # 14..(14+~8.98) turn
    assert ang != 0.0
    assert done is False


def test_exhausted_is_done():
    ang, done = search_command(1000.0, _cfg())
    assert done is True
    assert ang == 0.0


def test_direction_follows_lkd():
    a_pos, _ = search_command(12.0, _cfg(), lkd=1.0)
    a_neg, _ = search_command(12.0, _cfg(), lkd=-1.0)
    assert a_pos == -a_neg
