#!/usr/bin/env python3
"""Motor control helper — called via subprocess with sudo from the FastAPI server.

Usage:
  sudo python3 motor_ctrl.py move <left> <right> <duration>   # 단발 명령
  sudo python3 motor_ctrl.py stop
  sudo python3 motor_ctrl.py daemon                           # 상주 모드(stdin 루프)

daemon 모드는 모터를 한 번만 초기화하고 stdin 에서 "<left> <right>" 줄을 읽어
즉시 속도를 갱신한다. 주행 WS 가 명령마다 프로세스를 띄우지 않도록 하여
라즈베리파이 과부하(느려짐/멈춤)를 막는다.
"""
import argparse
import select
import sys
import time

# 워치독: 이 시간(초) 동안 명령이 끊기면 안전을 위해 모터를 정지한다.
WATCHDOG_S = 0.7


def _run_daemon(motor) -> None:
    motor.enable_motor()
    last = (0, 0)
    last_ts = time.time()
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 0.1)
        if ready:
            line = sys.stdin.readline()
            if not line:  # EOF — 컨트롤러(서버 WS) 종료
                break
            cmd = line.strip()
            if cmd in ("quit", "exit"):
                break
            if cmd in ("", "stop"):
                if last != (0, 0):
                    motor.move(0, 0)
                last = (0, 0)
                last_ts = time.time()
                continue
            try:
                parts = cmd.split()
                left = max(-100, min(100, int(parts[0])))
                right = max(-100, min(100, int(parts[1])))
            except (ValueError, IndexError):
                continue
            if (left, right) != last:
                motor.move(left, right)
                last = (left, right)
            last_ts = time.time()
        else:
            # 입력 없음 — 명령이 끊겼고 아직 움직이는 중이면 정지
            if last != (0, 0) and time.time() - last_ts > WATCHDOG_S:
                motor.move(0, 0)
                last = (0, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["move", "stop", "daemon"])
    parser.add_argument("left", type=int, nargs="?", default=0)
    parser.add_argument("right", type=int, nargs="?", default=0)
    parser.add_argument("duration", type=float, nargs="?", default=0.5)
    args = parser.parse_args()

    try:
        from pinkylib import Motor
        motor = Motor()

        if args.command == "daemon":
            try:
                _run_daemon(motor)
            finally:
                motor.stop()
                motor.disable_motor()
                motor.close()
            print("OK: daemon exit")
            return

        motor.enable_motor()
        if args.command == "move":
            motor.move(args.left, args.right)
            time.sleep(max(0.05, args.duration))
        motor.stop()
        motor.disable_motor()
        motor.close()
        print(f"OK: {args.command} left={args.left} right={args.right} duration={args.duration}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
