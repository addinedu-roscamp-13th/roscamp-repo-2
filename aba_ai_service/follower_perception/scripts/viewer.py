#!/usr/bin/env python3
"""Pure-Python viewer for perception_server — same role as qt_demo/build/viewer.

Connects to the server's TCP JPEG stream and shows it in a cv2 window.
Keys: r = register, x = reset, q/ESC = quit.

    python scripts/viewer.py 127.0.0.1 5007

Needs a display + GUI-enabled OpenCV (pip opencv-python has it).
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from scripts.frame_proto import recv_frame


def _connect_retry(host, port):
    """Wait for the server (it opens :port only after loading the model)."""
    while True:
        try:
            return socket.create_connection((host, port), timeout=3)
        except OSError:
            print(f"[..] waiting for server {host}:{port} ...")
            time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description="Python viewer for perception_server")
    ap.add_argument("host", nargs="?", default="127.0.0.1")
    ap.add_argument("port", nargs="?", type=int, default=5007)
    args = ap.parse_args()

    sock = _connect_retry(args.host, args.port)
    print(f"[ok] connected {args.host}:{args.port}   keys: [r]register [x]reset [q]quit")
    win = "perception viewer  [r]register [x]reset [q]quit"
    try:
        while True:
            jpeg = recv_frame(sock)
            if jpeg is None:
                print("[..] stream closed")
                break
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                cv2.imshow(win, img)
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            elif k == ord("r"):
                sock.sendall(b"register\n")
            elif k == ord("x"):
                sock.sendall(b"reset\n")
    finally:
        sock.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
