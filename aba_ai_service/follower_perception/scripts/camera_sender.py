#!/usr/bin/env python3
"""Robot (Pi) side: capture camera -> UDP-send 640-wide JPEG to the AI server.

    python scripts/camera_sender.py --host <AI_SERVER_IP> --port 6001 --camera 0
    python scripts/camera_sender.py --host 127.0.0.1 --port 6001 --test-pattern

Independent of ROS — runs alongside the robot bringup, reads the camera directly.
"""
import argparse
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.udp_video import UdpVideoSender
from scripts.perception_server import test_pattern_frames, _camera_frames

_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _orient(frame, rotate, hflip, vflip):
    if rotate in _ROTATE:
        frame = cv2.rotate(frame, _ROTATE[rotate])
    if hflip:
        frame = cv2.flip(frame, 1)
    if vflip:
        frame = cv2.flip(frame, 0)
    return frame


def _picamera_frames(width=640, height=480):
    """Raspberry Pi CSI camera via libcamera (picamera2). Yields BGR frames.
    picamera2 'RGB888' returns a BGR-ordered array (OpenCV-compatible), used as-is.
    Run with the SYSTEM python3 (picamera2 is a system package)."""
    try:
        from picamera2 import Picamera2
    except ImportError:
        print("[error] picamera2 not found. Install: sudo apt install -y python3-picamera2\n"
              "        and run this with the system python3 (not a venv).")
        raise SystemExit(2)
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(
        main={"size": (width, height), "format": "RGB888"}))
    picam2.start()
    time.sleep(0.5)                       # sensor warmup / auto-exposure settle
    print("[ok] picamera2 started (libcamera)")
    try:
        while True:
            yield picam2.capture_array()  # already BGR-ordered
    finally:
        picam2.stop()


def main():
    ap = argparse.ArgumentParser(description="UDP camera sender (robot side)")
    ap.add_argument("--host", required=True, help="AI server IP")
    ap.add_argument("--port", type=int, default=6001)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int, default=0)
    src.add_argument("--picamera", action="store_true", help="Pi CSI camera (libcamera/picamera2)")
    src.add_argument("--test-pattern", dest="test_pattern", action="store_true")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--fps", type=float, default=15.0)   # lower fps = less Pi CPU/bandwidth
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="rotate at capture. Default: 180 for --picamera (this Pi CSI "
                         "cam is mounted upside-down), 0 otherwise. Pass --rotate 0 to disable.")
    ap.add_argument("--hflip", action="store_true")
    ap.add_argument("--vflip", action="store_true")
    args = ap.parse_args()

    # This Pi's CSI camera is physically mounted upside-down, so picamera frames
    # need a 180 rotation by default (matches pinkylib's hardcoded correction).
    # Still overridable: `--rotate 0` disables it, `--rotate 90/270` picks another angle.
    if args.rotate is None:
        args.rotate = 180 if args.picamera else 0

    if args.picamera:
        frames = _picamera_frames(args.width, int(args.width * 3 / 4))
    elif args.test_pattern:
        frames = test_pattern_frames()
    else:
        frames = _camera_frames(args.camera)
    sender = UdpVideoSender(args.host, args.port, width=args.width, quality=args.quality)
    print(f"[ok] sending video -> {args.host}:{args.port} ({args.width}w, q{args.quality})")
    delay = 1.0 / args.fps if args.fps > 0 else 0.0
    n = 0
    try:
        for frame in frames:
            sender.send(_orient(frame, args.rotate, args.hflip, args.vflip))
            n += 1
            if n % 60 == 0:
                print(f"[..] sent {n} frames")
            if delay:
                time.sleep(delay)
    finally:
        sender.close()


if __name__ == "__main__":
    main()
