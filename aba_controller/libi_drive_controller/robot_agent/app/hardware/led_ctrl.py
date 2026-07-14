#!/usr/bin/env python3
"""LED 직접 제어 (pinkylib.LED) — root 권한으로 실행.

Usage:
  sudo python3 led_ctrl.py fill <r> <g> <b>
  sudo python3 led_ctrl.py pixel <indices_csv> <r> <g> <b>
  sudo python3 led_ctrl.py clear
  sudo python3 led_ctrl.py brightness <0-255>
"""
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print("ERROR: command required", file=sys.stderr)
        sys.exit(1)

    from pinkylib import LED
    led = LED()
    cmd = sys.argv[1]

    if cmd == "fill":
        r, g, b = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
        led.fill((r, g, b))
        led.show()
        print(f"OK: fill ({r},{g},{b})")

    elif cmd == "pixel":
        indices = [int(i) for i in sys.argv[2].split(",") if i.strip()]
        r, g, b = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
        for idx in indices:
            led.set_pixel(idx, (r, g, b))
        led.show()
        print(f"OK: pixel {indices} ({r},{g},{b})")

    elif cmd == "clear":
        led.clear()
        led.show()
        print("OK: clear")

    elif cmd == "brightness":
        val = int(sys.argv[2])
        led.set_brightness(val)
        led.show()
        print(f"OK: brightness {val}")

    else:
        print(f"ERROR: unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
