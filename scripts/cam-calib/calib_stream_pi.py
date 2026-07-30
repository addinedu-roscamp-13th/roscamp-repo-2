#!/usr/bin/env python3
"""로봇(Pi)에서 실행 — 캘리브레이션용 프레임을 MJPEG/HTTP 로 노트북에 흘린다.

    python3 calib_stream_pi.py --source picam            # CSI. rpicam-vid 480x360
    python3 calib_stream_pi.py --source usb --index 1    # USB. V4L2 640x480

노트북에서 화면을 보며 찍는다:
    python3 calib_client.py --host <PI_IP> --source picam --square-m 0.0382 --out ...

왜 robot_agent 스냅샷(/api/robot/camera/snapshot)을 안 쓰는가:
  robot_agent 를 띄우지 않고도 캘리브를 하기 위해서다. 대신 "런타임과 같은 파이프라인"
  조건은 그대로 지켜야 하므로, 여기서 런타임 인자를 그대로 복제한다.

    picam → app/hardware/camera_stream.py:_loop_rpicam() 의 인자와 동일
            (rpicam-vid --codec mjpeg --width 480 --height 360 --framerate 15 -q 80)
            rpicam 이 뱉은 JPEG 을 재인코딩 없이 그대로 넘긴다 = 런타임 픽셀 그대로.
    usb   → perception_server.py:_camera_frames() 가 협상하는 값(640x480)을 명시 고정.
            해상도가 흔들리면 K 가 통째로 무효라서 여기서 못박는다.

⚠️ CSI 는 한 프로세스만 잡는다. image-sender.sh(--picamera) 나 robot_agent 가 떠 있으면
   rpicam-vid 가 못 열고 바로 죽는다. 먼저 끄고 실행할 것.
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_BOUNDARY = "calibframe"


class Latest:
    """최신 프레임 1장만 들고 있는다(밀린 프레임은 버린다 — 배치하며 보는 용도라 지연이 독)."""

    def __init__(self) -> None:
        self._jpeg: bytes | None = None
        self._seq = 0
        self._cv = threading.Condition()
        self.dead: str | None = None

    def put(self, jpeg: bytes) -> None:
        with self._cv:
            self._jpeg, self._seq = jpeg, self._seq + 1
            self._cv.notify_all()

    def kill(self, why: str) -> None:
        with self._cv:
            self.dead = why
            self._cv.notify_all()

    def get(self, seen: int, timeout: float = 5.0) -> tuple[bytes | None, int]:
        with self._cv:
            if self._seq == seen and not self.dead:
                self._cv.wait(timeout)
            return self._jpeg, self._seq


def picam_producer(latest: Latest, flip: str, width: int, height: int, fps: int, quality: int) -> None:
    bin_ = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
    if not bin_:
        latest.kill("rpicam-vid/libcamera-vid 가 없습니다. picamera2 폴백 경로는 런타임과 달라 "
                    "캘리브에 쓰면 안 됩니다.")
        return
    args = [bin_, "-t", "0", "--codec", "mjpeg", "--width", str(width), "--height", str(height),
            "--framerate", str(fps), "--nopreview", "-q", str(quality), "-o", "-"]
    if flip in ("horizontal", "both"):
        args.append("--hflip")
    if flip in ("vertical", "both"):
        args.append("--vflip")
    print(f"[picam] {' '.join(args)}")
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    except Exception as e:
        latest.kill(f"rpicam-vid 실행 실패: {e}")
        return

    buf = b""
    while True:
        chunk = proc.stdout.read(65536)
        if not chunk:
            err = (proc.stderr.read() or b"").decode(errors="replace").strip()
            latest.kill("rpicam-vid 가 종료됐습니다. CSI 를 다른 프로세스가 잡고 있지 않은지 "
                        f"확인하세요 (fuser -v /dev/video0).\n{err[-500:]}")
            return
        buf += chunk
        while True:                                  # MJPEG 바이트스트림에서 SOI/EOI 로 자른다
            s = buf.find(b"\xff\xd8")
            if s < 0:
                buf = b""
                break
            e = buf.find(b"\xff\xd9", s + 2)
            if e < 0:
                buf = buf[s:] if s > 0 else buf
                break
            latest.put(buf[s:e + 2])                 # 재인코딩 없음 = 런타임 픽셀 그대로
            buf = buf[e + 2:]


def usb_producer(latest: Latest, index: int, width: int, height: int, fps: int, quality: int) -> None:
    import cv2

    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        latest.kill(f"USB 카메라를 열 수 없습니다: index {index} "
                    f"(image-sender.sh --camera {index} 가 이미 잡고 있지 않은지 확인)")
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    got = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    print(f"[usb] index {index} → {got[0]}x{got[1]}")
    if got != (width, height):
        latest.kill(f"해상도 고정 실패: 요청 {width}x{height}, 실제 {got[0]}x{got[1]}. "
                    f"이 상태로 캘리브하면 런타임과 달라져 무효입니다.")
        cap.release()
        return
    while True:
        ok, frame = cap.read()
        if not ok:
            latest.kill("USB 카메라 read 실패 — 케이블/전원을 확인하세요.")
            cap.release()
            return
        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            latest.put(enc.tobytes())


def test_producer(latest: Latest, width: int, height: int, quality: int) -> None:
    """카메라를 안 건드리고 링크만 확인하는 합성 소스(체커보드가 움직인다).

    노트북 화면·검출·수집까지 전부 리허설할 수 있다 — 촬영 나가기 전에 이걸로 먼저 확인."""
    import time

    import cv2
    from test_calib import _POSES, _render

    i = 0
    while True:
        gray = _render(_POSES[i % len(_POSES)])
        if gray.shape[::-1] != (width, height):
            gray = cv2.resize(gray, (width, height))
        ok, enc = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            latest.put(enc.tobytes())
        i += 1
        time.sleep(1 / 5)


def make_handler(latest: Latest, meta: dict):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):                    # 접속마다 stderr 로 도배되는 것 방지
            pass

        def _fail(self, why: str) -> None:
            body = why.encode()
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if latest.dead:
                self._fail(latest.dead)
                return
            if self.path.startswith("/snapshot"):
                jpeg, _ = latest.get(-1)
                if jpeg is None:
                    self._fail("아직 프레임이 없습니다.")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
                return
            if self.path.startswith("/info"):
                body = repr(meta).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # 기본: MJPEG 스트림
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            try:
                while True:
                    if latest.dead:
                        return
                    jpeg, seen = latest.get(seen)
                    if jpeg is None:
                        continue
                    self.wfile.write(
                        f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description="캘리브레이션용 카메라 스트리머 (로봇 측)")
    ap.add_argument("--source", choices=["picam", "usb", "test"], required=True,
                    help="test = 카메라 없이 합성 체커보드(링크·화면 리허설용)")
    ap.add_argument("--index", type=int, default=1, help="USB 카메라 인덱스 (기본 1 = /dev/video1)")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--flip", default=os.environ.get("CAMERA_FLIP", "none"),
                    choices=["none", "vertical", "horizontal", "both"],
                    help="picam 전용. robot_agent/.env 의 CAMERA_FLIP 과 반드시 같아야 한다")
    ap.add_argument("--quality", type=int, default=None, help="JPEG 품질 (기본: picam 80, usb 95)")
    ap.add_argument("--res", default=None, metavar="WxH",
                    help="해상도 override (예: 640x480). 기본은 각 런타임 값 — "
                         "**쓸 파이프라인과 같은 해상도로만** 뽑을 것. K 는 해상도 전용이다")
    a = ap.parse_args()

    # 기본은 런타임 값 그대로. --res 는 런타임을 다른 해상도로 새로 짤 때만 쓴다
    # (예: 도킹 코드를 picam 640x480 으로 만드는 경우). 화각이 모드마다 달라질 수 있어
    # 480x360 결과를 4/3 배 스케일하는 것보다 그 해상도로 다시 뽑는 편이 안전하다.
    if a.source in ("picam", "test"):
        width, height, fps = 480, 360, 15          # camera_stream.py:_loop_rpicam
        quality = 80 if a.quality is None else a.quality
    else:
        width, height, fps = 640, 480, 30          # perception_server.py:_camera_frames 협상값
        quality = 95 if a.quality is None else a.quality
    if a.res:
        try:
            width, height = (int(v) for v in a.res.lower().split("x"))
        except ValueError:
            raise SystemExit(f"--res 형식: 640x480 (받은 값: {a.res!r})")

    latest = Latest()
    meta = {"source": a.source, "width": width, "height": height, "fps": fps,
            "flip": a.flip if a.source == "picam" else "none",
            "index": a.index if a.source == "usb" else None}

    if a.source == "test":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        t = threading.Thread(target=test_producer,
                             args=(latest, width, height, quality), daemon=True)
    elif a.source == "picam":
        t = threading.Thread(target=picam_producer,
                             args=(latest, a.flip, width, height, fps, quality), daemon=True)
    else:
        t = threading.Thread(target=usb_producer,
                             args=(latest, a.index, width, height, fps, quality), daemon=True)
    t.start()

    srv = ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(latest, meta))
    srv.daemon_threads = True
    print(f"[ok] {a.source} {width}x{height} → http://0.0.0.0:{a.port}/  (Ctrl-C 종료)")
    if a.source == "picam":
        print(f"     CAMERA_FLIP={a.flip}  ← robot_agent/.env 와 다르면 캘리브가 무효입니다")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        srv.server_close()
        if latest.dead:
            print(f"[error] {latest.dead}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
