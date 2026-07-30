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


#: 대기 중인 캠을 몇 프레임에 한 번 잡을 것인가. 15fps 기준 8 → 약 1.9fps.
#
# **0 이면 게이팅 자체를 끈다**(예전처럼 둘 다 매 프레임).
STANDBY_EVERY = 8


def run(front_frames, back_frames, sender, select, *, orient_front, orient_back,
        fps=15.0, now=time.monotonic, max_frames=None, standby_every=STANDBY_EVERY):
    """캡처 → 탭 기록 → (선택된 캠만) 송출. 테스트가 그대로 부를 수 있게 분리했다.

    ## [2026-07-30] 대기 캠은 저주기로 잡는다 — Pi 부하의 절반이 여기였다

    예전에는 `camera_select` 가 `none` 이어도 **매 프레임 두 캠을 다 잡았다.** 선택은
    캡처 *뒤에* 봤기 때문이다. 순회 중에는 아무것도 송출하지 않는데(`none`) 캡처·회전·
    탭 기록이 두 벌 다 돌았다.

    실측 비용 모델(2026-07-30, 3점 측정):
        비용 ≈ 16.4%(고정) + 2.91%/fps(프레임당) + 1.66%/fps(픽셀분, 640 기준)
    프레임당 항의 절반이 대기 캠 몫이라, 그걸 1/8 로 줄이면 60% → 약 40% 가 된다.

    ## 왜 아예 끄지 않고 저주기인가

    끄면 대기 캠의 V4L2 버퍼가 멈춘다. 그런데 회복 트리가 **사람을 놓쳤을 때 반대 캠으로
    바꿔 찾는다**(`follow_node._peek_people`). 그 순간 워밍업 지연이 생기면 지연이 곧
    실패다. 저주기로 돌리면 장치는 열려 있고 버퍼도 흐른다 — 비용만 1/8 이다.

    `frame_tap` 머리말의 "camera_select 와 무관하게 둘 다 항상 기록" 계약도 **깨지지
    않는다.** 둘 다 계속 기록되고, 대기 쪽만 갱신 주기가 낮아질 뿐이다. (2026-07-30 확인:
    `frame_tap.read` 를 부르는 프로덕션 코드는 아직 없다 — 마커 도킹이 붙으면 그때
    이 주기가 충분한지 다시 봐야 한다.)

    ⚠️ **선택된 캠은 항상 매 프레임이다.** fps 를 낮추는 변경이 아니다 — 추종 검출
       주기는 그대로다.
    """
    delay = 1.0 / fps if fps > 0 else 0.0
    seq = 0
    sent = 0
    tick = 0                    # 건너뛴 프레임도 세는 루프 카운터 (seq 는 실제 캡처만)
    front_alive = front_frames is not None
    back_alive = back_frames is not None
    while max_frames is None or seq < max_frames:
        t = now()
        # ⚠️ 선택을 **캡처 앞으로** 옮겼다. 뒤에서 보면 이미 둘 다 잡은 뒤다.
        choice = select.current(t)
        standby = (standby_every <= 0) or (tick % standby_every == 0)
        tick += 1
        want_front = front_alive and (choice == "front" or standby)
        want_back = back_alive and (choice == "back" or standby)

        front = _next(front_frames) if want_front else None
        back = _next(back_frames) if want_back else None
        # 소진 판정은 **잡으려고 시도했는데 None 인 경우**만이다. 건너뛴 것을 소진으로
        # 치면 대기 캠이 한 번 쉬는 순간 루프가 끝난다.
        if want_front and front is None:
            front_alive = False
        if want_back and back is None:
            back_alive = False
        if not front_alive and not back_alive:
            break                          # 두 소스가 모두 끝났다
        if front is not None or back is not None:
            seq += 1
        if front is not None:
            front = orient_front(front)
            frame_tap.write("front", front, seq, t)
        if back is not None:
            back = orient_back(back)
            frame_tap.write("back", back, seq, t)

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
    ap.add_argument("--width", type=int, default=640,
                    help="앞캠 캡처 폭(높이는 4:3). 뒷캠은 --back-width 로 따로 준다.")
    ap.add_argument("--back-width", dest="back_width", type=int, default=None,
                    help="뒷캠 캡처 폭(높이는 4:3). 안 주면 드라이버 기본값(보통 640x480). "
                         "⚠️ UVC 캠은 지원 안 하는 크기를 조용히 무시하고 화각이 다른 모드로 "
                         "연다 — 이 캠의 4:3 지원은 320/640/800 폭뿐이다(2026-07-30 실측). "
                         "설정 후 실제 값을 찍으니 로그에서 ⚠️ 를 확인할 것.")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--fps", type=float, default=15.0)   # lower fps = less Pi CPU/bandwidth
    ap.add_argument("--standby-every", dest="standby_every", type=int, default=STANDBY_EVERY,
                    help="대기 중인 캠을 몇 프레임에 한 번 잡을지 (기본 %(default)s, "
                         "15fps 면 약 1.9fps). 0 이면 게이팅 끔(둘 다 매 프레임). "
                         "선택된 캠은 항상 매 프레임이라 추종 검출 주기는 안 바뀐다.")
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

    # ⚠️ [2026-07-30] `--width` 는 **두 캠 모두**에 걸린다.
    #
    # 예전엔 picamera(앞캠)에만 전달돼서, 뒷캠은 무엇을 주든 드라이버 기본값(보통
    # 640x480)으로 돌았다. 그런데 캡처 복사·회전·탭 기록은 전부 픽셀 수에 비례하고
    # 뒷캠도 매 프레임 같이 돈다(`camera_select` 가 none 이어도 탭은 계속 쓴다).
    # 즉 앞캠만 줄이면 Pi 부하는 절반만 준다.
    #
    # 480x360 이면 640x480 대비 픽셀이 56% 다. YOLO 는 `imgsz=640` 으로 레터박싱하므로
    # (detector.py — imgsz 를 안 넘겨 ultralytics 기본값) 480 은 서버에서 다시 늘어난다:
    # **연산량은 그대로고 검출 거리만 조금 준다.** 그 대가로 Pi 에서 fps 를 15 로
    # 되돌릴 수 있다는 판단이다(2026-07-30, 10fps 는 추종 반응이 둔했다).
    # ⚠️ 뒷캠은 `--width` 를 **안 따라간다.** 별도 `--back-width` 다.
    #
    # 2026-07-30 실측: 이 USB 캠(/dev/video1)이 지원하는 4:3 모드는
    #   **320x240 · 640x480 · 800x600 뿐**이다. 480x360 을 요청하면 거부하고
    #   **640x360(16:9)** 으로 연다 — 폭은 그대로인데 세로만 잘려 **화각이 바뀐다.**
    #   그 상태로 YOLO·마커를 보면 조용히 다른 그림을 본다. 그래서 두 캠을 갈랐다.
    #   (요청↔실제가 다르면 `_camera_frames` 가 ⚠️ 를 찍는다. 그게 이 사실을 잡아냈다.)
    cap_h = int(args.width * 3 / 4)
    if args.picamera:
        front = _picamera_frames(args.width, cap_h)
    elif args.test_pattern:
        front = test_pattern_frames()
    else:
        front = _camera_frames(args.camera, args.width, cap_h)
    if args.back_camera is None:
        back = None
    elif args.back_width:
        back = _camera_frames(args.back_camera, args.back_width,
                              int(args.back_width * 3 / 4))
    else:
        back = _camera_frames(args.back_camera)   # 드라이버 기본값(보통 640x480)

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
            fps=args.fps, standby_every=args.standby_every)
    finally:
        sender.close()
        # 프로세스가 끝나면 슬롯을 지운다. 남겨두면 소비자가 죽은 프로세스의 마지막
        # 프레임을 신선한 것으로 읽는다 — 정지한 화면을 보고 계속 주행하게 된다.
        # (정리는 루프가 아니라 **프로세스 수명**의 책임이라 run() 밖에 둔다.)
        frame_tap.cleanup()


if __name__ == "__main__":
    main()
