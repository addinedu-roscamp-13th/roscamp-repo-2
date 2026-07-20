#!/usr/bin/env python3
"""Register a person from ONE photo, then verify persistent ReID on a video.

Run from the follower_perception/ package root:
    python scripts/register_and_track.py register --image v.jpg --profile profiles/v1
    python scripts/register_and_track.py track    --video walk.mp4 --profile profiles/v1
"""
import argparse
import os
import sys

# make the package importable when run as a script from the package root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402


def _build_perception(device=None, hsv_threshold="default"):
    try:
        from follower_perception.detector import Detector
        from follower_perception.reid_engine import ReIDEngine
        from follower_perception.pipeline import FollowerPerception
    except ImportError as e:  # pragma: no cover - env-dependent
        print(f"[error] missing dependency ({e}). Run this in an env with "
              f"ultralytics+torch installed (e.g. your project venv).")
        raise SystemExit(2)
    reid = ReIDEngine(device=device)
    p = FollowerPerception(detector=Detector(device=device), reid=reid)
    if hsv_threshold == "none":
        p.matcher.hsv_threshold = None
    elif hsv_threshold != "default":
        p.matcher.hsv_threshold = float(hsv_threshold)
    return p


def cmd_register(args):
    from follower_perception.detector import is_person_class0
    img = cv2.imread(args.image)
    if img is None:
        print(f"[error] cannot read image: {args.image}"); return 2
    p = _build_perception(device=args.device)
    names = getattr(p.detector.model, "names", None)
    if names is not None and not is_person_class0(names):
        print(f"[warn] class 0 is not 'person' (names={names}); detections may be empty")
    box = p.register_from_image(img)
    if box is None:
        print("[error] no person found in the image"); return 1
    from datetime import datetime
    p.save_profile(args.profile, name=args.name or os.path.basename(args.profile),
                   source_image=os.path.basename(args.image),
                   registered_at=datetime.now().isoformat(timespec="seconds"))
    print(f"[ok] registered track_id={box.track_id}; profile saved to {args.profile}")
    return 0


def _iter_video(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"[error] cannot open video: {path}"); raise SystemExit(2)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
    finally:
        cap.release()


def _tee_annotated(frames, perception, out_path, writer):
    """Yield frames unchanged, but also write an annotated mp4 using the
    perception's latest detection (box drawn is one frame behind — enough for
    a visual sanity check)."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    for frame in frames:
        det = perception.get_latest()
        vis = frame.copy()
        if det is not None and det.is_owner:
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            color = (0, 165, 255) if det.is_predicted else (0, 255, 0)
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
        if writer["w"] is None:
            h, w = vis.shape[:2]
            writer["w"] = cv2.VideoWriter(out_path, fourcc, 20.0, (w, h))
        writer["w"].write(vis)
        yield frame


def cmd_track(args):
    from follower_perception.tracking_report import track_frames
    hsv = "none" if args.no_hsv else (
        str(args.hsv_threshold) if args.hsv_threshold is not None else "default")
    p = _build_perception(device=args.device, hsv_threshold=hsv)
    try:
        p.load_profile(args.profile, strict=args.strict)
    except (FileNotFoundError, ValueError) as e:
        print(f"[error] load profile failed: {e}"); return 2

    writer = {"w": None}
    frames = _iter_video(args.video)
    if args.out:
        frames = _tee_annotated(frames, p, args.out, writer)
    summary = track_frames(frames, p, log=print)
    if writer["w"] is not None:
        writer["w"].release()
    print("---- summary ----")
    print(f"frames={summary['frames']} owner={summary['owner_frames']} "
          f"hold_ratio={summary['owner_hold_ratio']:.2%} "
          f"predicted={summary['predicted_frames']} "
          f"max_miss_streak={summary['max_miss_streak']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Register from photo, verify ReID on video")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register")
    r.add_argument("--image", required=True)
    r.add_argument("--profile", required=True)
    r.add_argument("--name", default=None)
    r.add_argument("--device", default=None)
    r.set_defaults(func=cmd_register)

    t = sub.add_parser("track")
    t.add_argument("--video", required=True)
    t.add_argument("--profile", required=True)
    t.add_argument("--out", default=None)
    t.add_argument("--device", default=None)
    t.add_argument("--strict", action="store_true")
    g = t.add_mutually_exclusive_group()
    g.add_argument("--hsv-threshold", dest="hsv_threshold", type=float, default=None)
    g.add_argument("--no-hsv", dest="no_hsv", action="store_true")
    t.set_defaults(func=cmd_track)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
