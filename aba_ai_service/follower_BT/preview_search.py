#!/usr/bin/env python3
"""Dry-run the recovery search timeline (no robot, no perception) to inspect the
yaw-rotation profile and speed. Prints each phase + angular.z + integrated yaw."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # for follower_BT.*

from follower_BT.recovery import (
    search_command, ANGULAR_SEARCH, SCAN_SEC, TURN_SEC, SCAN_ANGLE,
)


def main():
    dt = 0.1
    total = 2 * SCAN_SEC + 2 * TURN_SEC + 0.5
    print(f"ANGULAR_SEARCH = {ANGULAR_SEARCH} rad/s "
          f"= {math.degrees(ANGULAR_SEARCH):.1f} deg/s")
    print(f"45deg sweep ~ {SCAN_ANGLE / ANGULAR_SEARCH:.2f}s   "
          f"180deg turn ~ {TURN_SEC:.2f}s   "
          f"total search ~ {total:.1f}s\n")
    print(f"{'t(s)':>6} {'phase':>9} {'ang.z(rad/s)':>13} {'yaw(deg)':>9}")
    t, yaw, last = 0.0, 0.0, None
    while t < total:
        ang, done, phase = search_command(t)
        yaw += math.degrees(ang) * dt
        if phase != last:                       # print on phase change
            print(f"{t:6.1f} {phase:>9} {ang:+13.2f} {yaw:9.1f}")
            last = phase
        t += dt
    print(f"\n[done] integrated yaw ~ {yaw:.0f} deg "
          f"(scan nets ~0, two 180 turns = full 360 back to start)")


if __name__ == "__main__":
    main()
