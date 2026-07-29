#!/usr/bin/env python3
"""Robot (Pi) side: 앞/뒤 카메라를 **한 프로세스**로 잡고, 선택된 것만 UDP 로 보낸다.

    # 앞캠만 (기존과 같음)
    python3 scripts/camera_sender.py --host <AI_SERVER_IP> --picamera
    # 앞뒤 둘 다 잡고, BT 가 고르게 한다
    python3 scripts/camera_sender.py --host <AI_SERVER_IP> --picamera --back-camera 4

## 왜 한 프로세스인가

Pi 에서 카메라 장치를 두 프로세스가 열면 앞캠이 `Device or resource busy` 로 죽는다
(`scripts/all/libi_pi.sh` 머리말의 실제 사고). 장치를 여는 주체를 하나로 모으면 그 사고가
구조적으로 불가능해지고, JPEG 인코딩도 **선택된 한 벌만** 돌아 Pi 부담이 준다.

## `none` 의 의미 — 반드시 지킨다

    none = JPEG 인코딩·UDP 송출만 중단. **캡처와 생프레임 로컬 탭은 계속 돈다.**

탭까지 멈추면 마커 도킹(복귀 중에 돈다)이 프레임을 못 얻어 조용히 죽는다.
"안 쓰는데 왜 캡처하냐"는 최적화가 나중에 들어오면 이 문장을 근거로 막아야 한다.

ROS 는 **선택 사항**이다. rclpy 가 없으면(개발 노트북) 구독 없이 CLI 인자대로 돈다.
"""
import argparse
import os
import sys
import threading
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import frame_tap                                        # noqa: E402
from scripts.camera_select import CameraSelect                       # noqa: E402
from scripts.udp_video import UdpVideoSender                         # noqa: E402
from scripts.perception_server import test_pattern_frames, _camera_frames  # noqa: E402

CAMERA_SELECT_TOPIC = "/libi/camera_select"
#: 이 시간 갱신이 없으면 스스로 none. follow_node 의 재발행 주기보다 넉넉해야 한다.
DEFAULT_SELECT_EXPIRY_SEC = 3.0

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


def _next(gen):
    """제너레이터에서 한 장. 소진되거나 실패하면 None — 한쪽 캠이 죽어도 다른 쪽은 산다."""
    if gen is None:
        return None
    try:
        return next(gen)
    except (StopIteration, Exception):     # noqa: BLE001 — 캡처 실패로 루프를 죽이지 않는다
        return None


def start_camera_select_subscriber(select, topic=CAMERA_SELECT_TOPIC):
    """`/libi/camera_select` 구독을 별도 스레드에서 돌린다. 실패하면 None 을 돌려준다.

    rclpy 가 없거나 ROS 가 안 sourcing 된 환경에서도 이 스크립트는 돌아야 한다 —
    개발 노트북에서 CLI 로 단독 시험하는 경로가 있다.
    """
    try:
        import rclpy
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String
    except ImportError as e:
        print(f"[camera_select] rclpy 없음({e}) — 구독 없이 CLI 인자대로 돕니다")
        return None

    qos = QoSProfile(depth=1,
                     reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)

    def spin():
        rclpy.init(args=None)
        node = rclpy.create_node("libi_camera_sender")
        node.create_subscription(
            String, topic,
            lambda m: select.set(m.data.strip(), time.monotonic()), qos)
        node.get_logger().info(f"camera_select 구독 시작 — {topic}")
        try:
            rclpy.spin(node)
        finally:
            node.destroy_node()
            rclpy.shutdown()

    t = threading.Thread(target=spin, name="camera_select", daemon=True)
    t.start()
    return t


def run(front_frames, back_frames, sender, select, *, orient_front, orient_back,
        fps=15.0, now=time.monotonic, max_frames=None):
    """캡처 → 탭 기록 → (선택된 캠만) 송출. 테스트가 그대로 부를 수 있게 분리했다."""
    delay = 1.0 / fps if fps > 0 else 0.0
    seq = 0
    sent = 0
    while max_frames is None or seq < max_frames:
        t = now()
        front = _next(front_frames)
        back = _next(back_frames)
        if front is None and back is None:
            break                          # 두 소스가 모두 끝났다
        seq += 1
        if front is not None:
            front = orient_front(front)
            frame_tap.write("front", front, seq, t)
        if back is not None:
            back = orient_back(back)
            frame_tap.write("back", back, seq, t)

        choice = select.current(t)
        if choice != "none":
            # 고른 캠이 없으면 **아무것도 안 보낸다.** 다른 캠으로 대신 보내면
            # 받는 쪽이 뒤를 본다고 믿고 판단하는데 실제로는 앞 영상이다.
            frame = front if choice == "front" else back
            if frame is not None:
                sender.send(frame)
                sent += 1
                if sent % 60 == 0:
                    print(f"[..] sent {sent} frames ({choice})")
        if delay:
            # ⚠️ `time.sleep(delay)` 를 그냥 걸면 안 된다 — 그러면 실제 주기가
            #    **캡처+인코딩 시간 + delay** 가 되어 `--fps` 가 상한일 뿐 달성값이
            #    아니게 된다. picamera2 캡처가 ~33ms 면 15fps 를 시켜도 실제 ~10fps 다.
            #    추종 제어 루프가 20Hz 인데 검출이 10fps 로 들어오면 그만큼 늦게 반응한다.
            #    (2026-07-28 "추종이 잘 안 된다" 신고 — 이 루프가 원인 후보다)
            #    남은 시간만 잔다. 처리가 delay 보다 오래 걸리면 안 자고 바로 다음 장.
            time.sleep(max(0.0, delay - (now() - t)))
    return seq, sent


def main():
    ap = argparse.ArgumentParser(description="UDP camera sender (robot side)")
    ap.add_argument("--host", required=True, help="AI server IP")
    ap.add_argument("--port", type=int, default=6001)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int, default=0)
    src.add_argument("--picamera", action="store_true", help="Pi CSI camera (libcamera/picamera2)")
    src.add_argument("--test-pattern", dest="test_pattern", action="store_true")
    ap.add_argument("--back-camera", dest="back_camera", type=int, default=None,
                    help="뒷캠(USB) 인덱스. 주면 앞뒤를 함께 잡고 BT 가 고른다. "
                         "목록: v4l2-ctl --list-devices")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--fps", type=float, default=15.0)   # lower fps = less Pi CPU/bandwidth
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="rotate at capture. Default: 180 for --picamera (this Pi CSI "
                         "cam is mounted upside-down), 0 otherwise. Pass --rotate 0 to disable.")
    ap.add_argument("--back-rotate", dest="back_rotate", type=int, default=0,
                    choices=[0, 90, 180, 270], help="뒷캠 회전(기본 0)")
    ap.add_argument("--hflip", action="store_true")
    ap.add_argument("--vflip", action="store_true")
    ap.add_argument("--select", default=None, choices=["front", "back", "none"],
                    help="camera_select 초기값. ROS 구독이 없을 때 이 값으로 고정된다. "
                         "기본은 구독이 되면 none(BT 가 켠다), 안 되면 front.")
    ap.add_argument("--select-expiry", dest="select_expiry", type=float,
                    default=DEFAULT_SELECT_EXPIRY_SEC,
                    help="이 시간 갱신이 없으면 스스로 none 으로 떨어진다. 0 이면 만료 없음.")
    ap.add_argument("--no-ros", dest="no_ros", action="store_true",
                    help="camera_select 구독을 시도하지 않는다(단독 시험용)")
    args = ap.parse_args()

    # This Pi's CSI camera is physically mounted upside-down, so picamera frames
    # need a 180 rotation by default (matches pinkylib's hardcoded correction).
    # Still overridable: `--rotate 0` disables it, `--rotate 90/270` picks another angle.
    if args.rotate is None:
        args.rotate = 180 if args.picamera else 0

    if args.picamera:
        front = _picamera_frames(args.width, int(args.width * 3 / 4))
    elif args.test_pattern:
        front = test_pattern_frames()
    else:
        front = _camera_frames(args.camera)
    back = _camera_frames(args.back_camera) if args.back_camera is not None else None

    select = CameraSelect(expiry_sec=args.select_expiry)
    sub = None if args.no_ros else start_camera_select_subscriber(select)
    initial = args.select if args.select else ("none" if sub else "front")
    # 구독이 도는 경우에도 초기값을 넣어 둔다. 만료가 있으므로, 발행자가 없으면
    # select_expiry 뒤에 스스로 none 으로 떨어진다.
    select.set(initial, time.monotonic())

    sender = UdpVideoSender(args.host, args.port, width=args.width, quality=args.quality)
    print(f"[ok] sending video -> {args.host}:{args.port} ({args.width}w, q{args.quality}) "
          f"| 뒷캠={'/dev/video%d' % args.back_camera if back else '없음'} "
          f"| 초기 선택={initial} | 탭={frame_tap.tap_dir()}")
    try:
        run(front, back, sender, select,
            orient_front=lambda f: _orient(f, args.rotate, args.hflip, args.vflip),
            orient_back=lambda f: _orient(f, args.back_rotate, False, False),
            fps=args.fps)
    finally:
        sender.close()
        # 프로세스가 끝나면 슬롯을 지운다. 남겨두면 소비자가 죽은 프로세스의 마지막
        # 프레임을 신선한 것으로 읽는다 — 정지한 화면을 보고 계속 주행하게 된다.
        # (정리는 루프가 아니라 **프로세스 수명**의 책임이라 run() 밖에 둔다.)
        frame_tap.cleanup()


if __name__ == "__main__":
    main()
