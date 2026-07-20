import math
from scripts.lidar_avoid import (
    sector_min, sectors4, sectors8, avoid_cmd, STOP_DIST, SIDE_AVOID, SIDE_DRIFT,
)

FAR = 5.0
BLOCK = STOP_DIST * 0.5             # clearly inside the stop distance
NEAR_SIDE = SIDE_AVOID * 0.5       # clearly inside the side-avoid threshold


def test_sector_min_front_only():
    # rays at -90,-45,0,45,90 deg
    ranges = [3.0, 2.0, 0.5, 2.0, 3.0]
    amin, ainc = math.radians(-90), math.radians(45)
    assert sector_min(ranges, amin, ainc, -30, 30) == 0.5     # only the 0deg ray
    assert sector_min(ranges, amin, ainc, 30, 90) == 2.0      # +45,+90 -> min 2.0


def test_sector_min_ignores_bad_values():
    ranges = [float("inf"), 0.0, -1.0, 1.5]
    amin, ainc = math.radians(-10), math.radians(10)
    assert sector_min(ranges, amin, ainc, -30, 30) == 1.5     # only the valid one


def test_sectors4_back():
    ranges = [0.5, 3, 3, 3, 3, 3, 3, 3]   # obstacle at -180deg (raw back)
    amin, ainc = math.radians(-180), math.radians(45)
    f, b, l, r = sectors4(ranges, amin, ainc)
    assert b == 0.5 and f == 3.0


def test_sectors4_flip_front_back():
    ranges = [3, 3, 3, 3, 0.5, 3, 3, 3]   # obstacle at 0deg (raw front, index 4)
    amin, ainc = math.radians(-180), math.radians(45)
    f, b, _, _ = sectors4(ranges, amin, ainc, flip_180=False)
    assert f == 0.5
    f, b, _, _ = sectors4(ranges, amin, ainc, flip_180=True)
    assert b == 0.5                        # 180 flip -> now behind


def test_sectors4_flip_sides():
    ranges = [3, 3, 3, 3, 3, 0.4, 3, 3]   # obstacle at +45deg (raw left)
    amin, ainc = math.radians(-180), math.radians(45)
    _, _, l, r = sectors4(ranges, amin, ainc, flip_180=False)
    assert l == 0.4
    _, _, l, r = sectors4(ranges, amin, ainc, flip_180=True)
    assert r == 0.4                        # 180 flip -> now on the right


def test_sectors8_keys_and_placement():
    ranges = [0.5, 3, 3, 3, 3, 0.4, 3, 3]   # idx0=-180 (back), idx5=+45 (front_left)
    amin, ainc = math.radians(-180), math.radians(45)
    s = sectors8(ranges, amin, ainc)
    assert set(s) == {"front", "front_left", "left", "back_left",
                      "back", "back_right", "right", "front_right"}
    assert s["back"] == 0.5 and s["front_left"] == 0.4


def test_sectors8_flip_maps_opposites():
    ranges = [3, 3, 3, 3, 3, 0.4, 3, 3]     # idx5=+45 (raw front_left)
    amin, ainc = math.radians(-180), math.radians(45)
    s = sectors8(ranges, amin, ainc, flip_180=True)
    assert s["back_right"] == 0.4           # front_left -> back_right under 180 flip


def test_sectors4_left_is_min_of_three_subsectors():
    # rays every 45deg from -180: idx 5=+45(좌상), 6=+90(좌), 7=+135(좌하)
    ranges = [3, 3, 3, 3, 3, 0.9, 0.5, 0.7]
    amin, ainc = math.radians(-180), math.radians(45)
    _, _, l, _ = sectors4(ranges, amin, ainc)
    assert l == 0.5                            # min(좌상=0.9, 좌=0.5, 좌하=0.7)


# --- avoid_cmd: brake head-on + steer away from a side wall within SIDE_AVOID ---

def test_clear_passes_through():
    lin, ang, r = avoid_cmd(0.06, 0.1, front=FAR, back=FAR, left=FAR, right=FAR)
    assert lin == 0.06 and ang == 0.1 and r == "clear"


def test_front_blocks_forward_but_keeps_rotation():
    lin, ang, r = avoid_cmd(0.06, 0.3, front=BLOCK, back=FAR, left=FAR, right=FAR)
    assert lin == 0.0 and ang == 0.3 and r == "front"   # rotation preserved


def test_forward_ignores_back():
    lin, ang, r = avoid_cmd(0.06, 0.0, front=FAR, back=BLOCK, left=FAR, right=FAR)
    assert lin == 0.06 and r == "clear"                 # back irrelevant going forward


def test_back_blocks_reverse():
    lin, ang, r = avoid_cmd(-0.04, 0.0, front=FAR, back=BLOCK, left=FAR, right=FAR)
    assert lin == 0.0 and r == "back"


def test_reverse_ignores_front():
    lin, ang, r = avoid_cmd(-0.04, 0.0, front=BLOCK, back=FAR, left=FAR, right=FAR)
    assert lin == -0.04 and r == "clear"                # front irrelevant reversing


def test_left_wall_steers_right():
    lin, ang, r = avoid_cmd(0.06, 0.0, front=FAR, back=FAR, left=NEAR_SIDE, right=FAR)
    assert ang == -SIDE_DRIFT and lin == 0.06 and r == "avoid"   # left -> steer right


def test_right_wall_steers_left():
    lin, ang, r = avoid_cmd(0.06, 0.0, front=FAR, back=FAR, left=FAR, right=NEAR_SIDE)
    assert ang == SIDE_DRIFT and r == "avoid"                    # right -> steer left


def test_both_sides_avoid_the_closer():
    # both within SIDE_AVOID, LEFT closer -> steer right (away from left)
    lin, ang, r = avoid_cmd(0.06, 0.0, front=FAR, back=FAR, left=0.03, right=0.05)
    assert ang == -SIDE_DRIFT and r == "avoid"


def test_avoid_overrides_turn_toward_wall():
    # rotating left toward a near left wall -> overridden to steer right (away)
    lin, ang, r = avoid_cmd(0.0, 0.3, front=FAR, back=FAR, left=NEAR_SIDE, right=FAR)
    assert ang == -SIDE_DRIFT and r == "avoid"


def test_outside_threshold_no_side_effect():
    # side wall just outside SIDE_AVOID -> command untouched
    lin, ang, r = avoid_cmd(0.06, 0.3, front=FAR, back=FAR, left=SIDE_AVOID + 0.05, right=FAR)
    assert ang == 0.3 and r == "clear"


def test_parked_no_side_avoid():
    # fully idle (0,0) -> never steer, even with a very close side wall
    lin, ang, r = avoid_cmd(0.0, 0.0, front=FAR, back=FAR, left=NEAR_SIDE, right=FAR)
    assert lin == 0.0 and ang == 0.0 and r == "clear"
