#!/usr/bin/env python3
"""Perception socket server (B안): owns the webcam, runs FollowerPerception,
draws boxes, and streams annotated JPEG frames to a Qt viewer over localhost
TCP. Receives newline commands: register / reset.

Run from the follower_perception/ package root:
    python scripts/perception_server.py --camera 0 --port 5007
    python scripts/perception_server.py --test-pattern           # no webcam/torch
"""
import argparse
import os
import select
import socket
import sys
import time

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))       # follower_perception/
sys.path.insert(0, _PKG)
sys.path.insert(0, os.path.join(os.path.dirname(_PKG), "follower_BT"))   # sibling follower_BT/

import cv2
import numpy as np

from scripts.frame_proto import send_frame
from scripts.cmd_preview import compute_cmd_vel


def _status_line(matcher):
    rs, hs = matcher.last_reid_sim, matcher.last_hsv_sim
    parts = [f"reid={rs:.2f}/{matcher.reid_threshold:.2f}" if rs is not None else "reid=-"]
    if matcher.hsv_threshold is None:
        parts.append("hsv=off")
    else:
        parts.append(f"hsv={hs:.2f}/{matcher.hsv_threshold:.2f}"
                     if hs is not None else "hsv=-")
    parts.append("reg" if matcher.is_registered else "noreg")
    if matcher.is_registered:
        parts.append(f"gal={len(matcher.gallery)}")   # online gallery size (grows)
    if matcher.safe_id is not None:
        parts.append(f"trk#{matcher.safe_id}")   # ByteTrack id — changing is normal
    return "  ".join(parts)


def _hud_text(img, text, org, color, scale=0.6, thick=2):
    """Readable HUD text: thick black outline behind, colored text on top (BGR)."""
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0),
                thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thick, cv2.LINE_AA)


def draw_overlay(frame, det, *, cands=None, pick=None, cmd=None, status_extra=""):
    vis = frame.copy()
    h, w = vis.shape[:2]
    # thirds guide lines: left / center / right direction zones
    for x in (w // 3, 2 * w // 3):
        cv2.line(vis, (x, 0), (x, h), (70, 70, 70), 1)
    # every YOLO person detection (thin gray) — shows detection runs each frame,
    # even before registration.
    for c in (cands or []):
        x1, y1, x2, y2 = (int(v) for v in c.bbox)
        is_pick = pick is not None and c.track_id == pick.track_id
        col = (0, 255, 255) if is_pick else (170, 170, 170)   # pick=yellow
        cv2.rectangle(vis, (x1, y1), (x2, y2), col, 2 if is_pick else 1)
        if is_pick:
            cv2.putText(vis, "register target", (max(0, x1), max(34, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
    # owner box on top (after registration)
    if det is not None and det.is_owner:
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        color = (0, 165, 255) if det.is_predicted else (0, 255, 0)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
        label = "OWNER (predicted)" if det.is_predicted else "OWNER"
        cv2.putText(vis, label, (max(0, x1), max(20, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    if cmd is not None:
        state = cmd.get("state", "")
        scol = {"IDLE": (200, 200, 200), "FOLLOWING": (0, 255, 0),
                "PEEK": (0, 255, 255), "SEARCHING": (0, 165, 255)}.get(state, (0, 0, 255))
        _hud_text(vis, f"STATE: {state}", (12, 40), scol, 0.9, 2)      # state (semantic)
        txt = (f"cmd_vel  lin.x={cmd['linear_x']:+.2f}  ang.z={cmd['angular_z']:+.2f}"
               f"   [{cmd['drive']} | {cmd['turn']}]")
        _hud_text(vis, txt, (12, 72), (255, 0, 0), 0.62, 2)            # blue (BGR)
    if status_extra:
        _hud_text(vis, status_extra, (12, vis.shape[0] - 12), (0, 0, 255), 0.62, 2)  # red (BGR)
    return vis


def _cm(m):
    """metres -> integer centimetres for the viewer; -1 means no reading."""
    return -1 if (m is None or not np.isfinite(m)) else int(round(m * 100.0))


def serve_loop(conn, frames, perception, *, poll_cmd=None, jpeg_quality=80,
               cmd_sink=None, policy=None, lidar_source=None):
    last_t = time.monotonic()
    for frame in frames:
        cmd = poll_cmd(conn) if poll_cmd else None
        if cmd == "register":
            perception.register_from_image(frame)
        elif cmd == "reset":
            perception.reset()
        perception.run(frame)
        det = perception.get_latest()
        cands = perception.last_cands
        # before registration, highlight which candidate the 등록 button would pick
        pick = None if perception.matcher.is_registered \
            else perception._pick_central(cands, frame)
        now = time.monotonic(); dt = now - last_t; last_t = now
        cmd = policy.step(det, frame.shape[1], dt,
                          registered=perception.matcher.is_registered) \
            if policy is not None else compute_cmd_vel(det, frame.shape[1])
        if cmd_sink is not None:                 # optional drive hook (opt-in)
            cmd_sink(cmd)
        vis = draw_overlay(frame, det, cands=cands, pick=pick, cmd=cmd,
                           status_extra=_status_line(perception.matcher))
        ok, buf = cv2.imencode(".jpg", vis,
                               [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if ok:
            try:
                send_frame(conn, buf.tobytes())
                if lidar_source is not None:                 # LiDAR telemetry (display only)
                    s = lidar_source.latest()
                    if s is not None:                        # order: FL F FR L R BL B BR
                        send_frame(conn, b"LIDR %d %d %d %d %d %d %d %d" % (
                            _cm(s["front_left"]), _cm(s["front"]), _cm(s["front_right"]),
                            _cm(s["left"]),                       _cm(s["right"]),
                            _cm(s["back_left"]),  _cm(s["back"]), _cm(s["back_right"])))
            except (BrokenPipeError, ConnectionResetError, OSError):
                return


def make_socket_poller():
    """Return a poll_cmd(conn) that non-blockingly reads newline commands."""
    state = {"buf": b""}

    def poll(conn):
        r, _, _ = select.select([conn], [], [], 0)
        if not r:
            return None
        try:
            data = conn.recv(4096)
        except OSError:
            return None
        if not data:
            return None
        state["buf"] += data
        cmd = None
        while b"\n" in state["buf"]:
            line, state["buf"] = state["buf"].split(b"\n", 1)
            line = line.strip().decode(errors="ignore")
            if line:
                cmd = line   # last command this tick wins
        return cmd

    return poll


class _AlwaysBox:
    """Test-pattern detector: always reports one full-frame track."""
    def detect(self, frame):
        from follower_perception.detection import TrackedBox
        h, w = frame.shape[:2]
        return [TrackedBox(bbox=(w * 0.25, h * 0.2, w * 0.75, h * 0.9),
                           cx=w / 2, cy=h / 2, area=w * h * 0.3,
                           track_id=1, confidence=0.9)]

    def reset(self):
        pass


def test_pattern_frames(n=None):
    """Synthetic BGR frames: a coloured person-ish block on a moving bg.
    Runs with no webcam and no torch."""
    i = 0
    w, h = 640, 480
    while n is None or i < n:
        img = np.full((h, w, 3), 30, dtype=np.uint8)
        cx = int(w * (0.5 + 0.25 * np.sin(i * 0.05)))
        cv2.rectangle(img, (cx - 60, 120), (cx + 60, 400), (60, 40, 200), -1)
        yield img
        i += 1


def _camera_frames(index):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f"[error] cannot open camera {index}"); raise SystemExit(2)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
    finally:
        cap.release()


def _build_perception(args):
    from follower_perception.reid_engine import ReIDEngine
    from follower_perception.pipeline import FollowerPerception
    if args.test_pattern:
        reid = ReIDEngine(backend="colour")
        p = FollowerPerception(detector=_AlwaysBox(), reid=reid)
    else:
        try:
            from follower_perception.detector import Detector
        except ImportError as e:  # pragma: no cover
            print(f"[error] ultralytics/torch not installed ({e}). Use "
                  f"--test-pattern or run in your model venv."); raise SystemExit(2)
        reid = ReIDEngine(device=args.device)
        p = FollowerPerception(detector=Detector(device=args.device), reid=reid)
    if args.no_hsv:
        p.matcher.hsv_threshold = None
    elif args.hsv_threshold is not None:
        p.matcher.hsv_threshold = float(args.hsv_threshold)
    if args.reid_threshold is not None:
        p.matcher.reid_threshold = float(args.reid_threshold)
    return p


def _rotate_frames(frames, deg):
    rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(deg)
    for f in frames:
        yield cv2.rotate(f, rot) if rot is not None else f


def _run_local_show(frames, perception, cmd_sink=None, policy=None):
    """Local cv2 window (no Qt, no socket). Keys: r=register, x=reset, q/ESC=quit."""
    win = "perception  [r]register [x]reset [q]quit"
    last_t = time.monotonic()
    for frame in frames:
        perception.run(frame)
        det = perception.get_latest()
        cands = perception.last_cands
        pick = None if perception.matcher.is_registered \
            else perception._pick_central(cands, frame)
        now = time.monotonic(); dt = now - last_t; last_t = now
        cmd = policy.step(det, frame.shape[1], dt,
                          registered=perception.matcher.is_registered) \
            if policy is not None else compute_cmd_vel(det, frame.shape[1])
        if cmd_sink is not None:                 # optional drive hook (opt-in)
            cmd_sink(cmd)
        vis = draw_overlay(frame, det, cands=cands, pick=pick, cmd=cmd,
                           status_extra=_status_line(perception.matcher))
        cv2.imshow(win, vis)
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("r"):
            perception.register_from_image(frame)
        elif k == ord("x"):
            perception.reset()
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description="Perception socket server (B안)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int, default=0)
    src.add_argument("--test-pattern", dest="test_pattern", action="store_true")
    src.add_argument("--udp", action="store_true", help="receive video over UDP from robot")
    ap.add_argument("--port", type=int, default=5007)          # Qt viewer port
    ap.add_argument("--bind", default="0.0.0.0",
                    help="뷰어 포트를 열 주소. 기본은 모든 인터페이스 — 로봇 터치패널이 "
                         "다른 머신이라 127.0.0.1 이면 못 붙는다. 같은 머신에서만 쓸 땐 "
                         "127.0.0.1 로 좁힌다.")
    ap.add_argument("--udp-port", dest="udp_port", type=int, default=6001)  # robot video in
    ap.add_argument("--device", default=None)
    ap.add_argument("--hsv-threshold", dest="hsv_threshold", type=float, default=None)
    ap.add_argument("--reid-threshold", dest="reid_threshold", type=float, default=None)
    ap.add_argument("--no-hsv", dest="no_hsv", action="store_true")
    ap.add_argument("--show", action="store_true",
                    help="local cv2 window instead of streaming to a viewer")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="rotate incoming frames by N degrees (e.g. 180 for upside-down camera)")
    ap.add_argument("--drive-host", dest="drive_host", default=None,
                    help="robot IP to send cmd_vel values to (ENABLES driving; omit = preview only)")
    ap.add_argument("--drive-port", dest="drive_port", type=int, default=6002)
    ap.add_argument("--lidar-ros", dest="lidar_ros", action="store_true",
                    help="subscribe to /scan via ROS2 and show front/back/left/right in the viewer")
    ap.add_argument("--scan-topic", dest="scan_topic", default="/scan")
    ap.add_argument("--lidar-flip", dest="lidar_flip", action="store_true",
                    help="LiDAR mounted rotated 180 deg (front<->back, left<->right)")
    args = ap.parse_args()

    if args.udp:
        from scripts.udp_video import UdpVideoReceiver
        UdpVideoReceiver  # imported lazily so non-UDP runs need no extra deps
        _recv = UdpVideoReceiver(args.udp_port)
        frames = _recv.frames()
        print(f"[ok] receiving UDP video on :{args.udp_port} (from robot)")
    elif args.test_pattern:
        frames = test_pattern_frames()
    else:
        frames = _camera_frames(args.camera)
    if args.rotate:
        frames = _rotate_frames(frames, args.rotate)
    perception = _build_perception(args)

    cmd_sink = None
    if args.drive_host:
        from scripts.cmd_channel import CmdSender
        _cmd_sender = CmdSender(args.drive_host, args.drive_port)
        cmd_sink = lambda c: _cmd_sender.send(c["linear_x"], c["angular_z"])
        print(f"[ok] DRIVE ON -> cmd_vel to {args.drive_host}:{args.drive_port} "
              f"(robot must run cmd_bridge)")

    from follower_BT.recovery import DrivePolicy   # IDLE/FOLLOWING/SEARCHING state machine
    policy = DrivePolicy(compute_cmd_vel)

    lidar_source = None
    if args.lidar_ros:
        try:
            from scripts.scan_ros_source import ScanRosSource
            lidar_source = ScanRosSource(topic=args.scan_topic, flip_180=args.lidar_flip)
            print(f"[ok] LiDAR view ON -> {args.scan_topic} via ROS2 "
                  f"(needs ROS sourced + matching ROS_DOMAIN_ID)")
        except Exception as e:
            print(f"[warn] --lidar-ros failed ({e}); continuing WITHOUT LiDAR display")

    if args.show:
        _run_local_show(frames, perception, cmd_sink=cmd_sink, policy=policy)
        return

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 127.0.0.1 로 묶으면 같은 머신의 뷰어만 붙을 수 있다. 로봇 터치패널(libi_gui)은 다른
    # 머신이라 그러면 아예 연결이 안 된다. 필요하면 --bind 로 좁힐 수 있게 열어둔다.
    srv.bind((args.bind, args.port))
    srv.listen(1)
    print(f"[ok] perception server on {args.bind}:{args.port} "
          f"({'test-pattern' if args.test_pattern else f'camera {args.camera}'}); "
          f"waiting for viewer…")
    while True:
        conn, addr = srv.accept()
        print(f"[ok] viewer connected: {addr}")
        try:
            serve_loop(conn, frames, perception, poll_cmd=make_socket_poller(),
                       cmd_sink=cmd_sink, policy=policy, lidar_source=lidar_source)
        finally:
            conn.close()
            print("[..] viewer disconnected; waiting again")
        if not args.test_pattern:
            # real camera generator is exhausted once closed; rebuild for next viewer
            frames = _camera_frames(args.camera)


if __name__ == "__main__":
    main()
