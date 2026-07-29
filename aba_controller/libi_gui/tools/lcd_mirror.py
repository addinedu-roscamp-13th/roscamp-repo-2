#!/usr/bin/env python3
"""libi_gui 화면을 로봇 LCD(240×240)에 축소해 흘려보내는 실험. **보류.**

## [2026-07-30] 실측 후 안 쓰기로 했다

실기(pinky-3)에서 재 보니 두 가지가 기대에 못 미쳤다:

  1. **CPU 값이 생각보다 크다.** 미러 ON/OFF 를 같은 조건에서 비교(15초 구간, cpu_top.py):
       OFF: 합계 212% (4코어=400% 만점)
       ON : 합계 238%  → **미러 비용 ≈ 코어 1개의 26%(전체의 6.5%)**
     그런데 이 비용 대부분이 이미지 처리가 아니라 `/lcd/image` 가 프레임마다
     `sudo python3 lcd_ctrl.py image <path>` 를 **새 프로세스로** 띄우는 데서 나왔다
     (`polkitd` 만으로 코어 1개의 11%). 무변화 프레임을 건너뛰는 최적화를 넣어도
     패널이 계속 바뀌는 상태(길잡이 등)에서는 그 절감이 별 의미가 없다.
  2. **실시간성이 없다.** 간격을 2초 아래로 낮추면 위 프로세스 생성 비용이 그대로
     배로 늘어난다 — 이미 nav2·camera_sender 로 4코어를 거의 다 쓰고, 이 로봇은
     같은 세션에서 86°C 로 소프트 온도 제한(throttled)에 걸린 이력이 있다.
     그 여유를 화면 미러링에 쓰는 게 맞지 않다고 판단했다.

읽는 것도 애초에 안 됐다 — 1280×800 → 240×240 은 5.3배 축소라 34px 글자가 6px 이 되고
한글은 그 크기에서 획이 사라진다. "화면이 살아 있다"는 것만 보이는 용도였는데, 그 용도조차
CPU 비용을 정당화하지 못한다는 게 이번 결론이다.

## 대안 — 데모엔 화면 녹화가 낫다

실시간 미러 대신 **미리 녹화한 GIF/영상**을 로봇 LCD 에 한 번만 올리는 편이 훨씬 싸다.
`lcd_ctrl.py` 는 GIF 루프 재생을 이미 지원한다(`_loop_gif`, 이 파일 상단 docstring 참고) —
프로세스가 **한 번** 뜨고 나면 그걸로 끝이라, 매 프레임 fork 비용이 없다. 시나리오를 찍은 뒤
GIF 하나 만들어 올리는 쪽으로 다시 검토할 것.

## 아래는 실행되지 않는다 — 참고용으로만 남긴다

RFB(VNC) 클라이언트 직접 구현 + 240 리사이즈 + SSH/HTTP 전송까지는 전부 동작 확인했다
(태블릿 접속을 안 끊으면서 동시 접속 가능한 것도 실측함). 나중에 "그래도 실시간 미러가
필요하다"가 되면, 이 블록의 주석을 풀고 `--interval` 을 훨씬 크게(10초+) 잡는 데서
다시 시작하면 된다.

```python
import argparse
import io
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image

LCD = 240


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("VNC 연결이 끊겼습니다")
        buf += chunk
    return bytes(buf)


class RfbClient:
    '''최소 RFB 클라이언트. Qt 의 QVncServer 는 RFB 3.3 을 쓴다(실측 확인).

    3.3 은 보안 타입을 4바이트 값 하나로 통보하고 곧장 ClientInit 으로 간다 —
    3.7+ 의 타입 목록 협상이 없다. 그걸 3.7 로 읽으면 첫 바이트부터 어긋난다.
    '''

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=10)
        ver = _recv_exact(self.sock, 12)
        self.sock.sendall(ver)
        if ver >= b"RFB 003.007":
            ntypes = _recv_exact(self.sock, 1)[0]
            types = _recv_exact(self.sock, ntypes)
            if 1 not in types:
                raise RuntimeError("서버가 인증 없는 접속을 안 받습니다")
            self.sock.sendall(bytes([1]))
            if struct.unpack(">I", _recv_exact(self.sock, 4))[0] != 0:
                raise RuntimeError("VNC 인증 거절")
        else:
            if struct.unpack(">I", _recv_exact(self.sock, 4))[0] != 1:
                raise RuntimeError("VNC 인증이 필요합니다 — 이 스크립트는 무인증만 지원")

        # shared=1 : 태블릿 접속을 끊지 않는다. 0 이면 서버가 기존 클라이언트를 내보낸다.
        self.sock.sendall(bytes([1]))
        hdr = _recv_exact(self.sock, 24)
        self.w, self.h = struct.unpack(">HH", hdr[:4])
        name_len = struct.unpack(">I", hdr[20:24])[0]
        _recv_exact(self.sock, name_len)

        # 픽셀 형식을 우리가 정한다 — 32bpp BGRX little-endian 하나로 고정.
        self.sock.sendall(struct.pack(
            ">BBBB BBBB HHH BBB BBB",
            0, 0, 0, 0,
            32, 24, 0, 1,
            255, 255, 255,
            16, 8, 0,
            0, 0, 0))
        # Raw(0) 만 쓴다.
        self.sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))

    def frame(self):
        '''전체 화면 한 장을 PIL 이미지로.'''
        self.sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, self.w, self.h))
        canvas = Image.new("RGB", (self.w, self.h), "black")
        while True:
            msg = _recv_exact(self.sock, 1)[0]
            if msg != 0:
                self._skip_other(msg)
                continue
            _recv_exact(self.sock, 1)
            nrects = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
            for _ in range(nrects):
                x, y, w, h, enc = struct.unpack(">HHHHi", _recv_exact(self.sock, 12))
                if enc != 0:
                    raise RuntimeError(f"raw 아닌 인코딩이 왔습니다: {enc}")
                data = _recv_exact(self.sock, w * h * 4)
                tile = Image.frombytes("RGBX", (w, h), data, "raw", "BGRX")
                canvas.paste(tile.convert("RGB"), (x, y))
            return canvas

    def _skip_other(self, msg):
        if msg == 1:
            body = _recv_exact(self.sock, 5)
            n = struct.unpack(">H", body[3:5])[0]
            _recv_exact(self.sock, n * 6)
        elif msg == 2:
            pass
        elif msg == 3:
            body = _recv_exact(self.sock, 7)
            _recv_exact(self.sock, struct.unpack(">I", body[3:7])[0])


def to_lcd(img, fit="letterbox"):
    if fit == "stretch":
        return img.resize((LCD, LCD), Image.LANCZOS)
    if fit == "fill":
        side = min(img.width, img.height)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        return img.crop((left, top, left + side, top + side)).resize((LCD, LCD), Image.LANCZOS)
    fitted = img.copy()
    fitted.thumbnail((LCD, LCD), Image.LANCZOS)
    canvas = Image.new("RGB", (LCD, LCD), "black")
    canvas.paste(fitted, ((LCD - fitted.width) // 2, (LCD - fitted.height) // 2))
    return canvas


LCD_CTRL = ("/home/roscamp-repo-2/aba_controller/libi_drive_controller/"
            "robot_agent/app/hardware/lcd_ctrl.py")


def push_ssh(target, img, remote_tmp="/tmp/libi_lcd_mirror.png"):
    import subprocess
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", target,
         f"cat > {remote_tmp} && sudo -n python3 {LCD_CTRL} image {remote_tmp} >/dev/null 2>&1"],
        input=buf.getvalue(), check=True, timeout=20)


def post_lcd(url, img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="mirror.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        buf.getvalue(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vnc", default="127.0.0.1:5903")
    ap.add_argument("--robot")
    ap.add_argument("--ssh")
    ap.add_argument("--robot-port", type=int, default=9001)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--save")
    ap.add_argument("--fit", choices=["letterbox", "fill", "stretch"], default="letterbox")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    if not args.ssh and not args.robot:
        sys.exit("--ssh 또는 --robot 중 하나는 필요합니다")
    host, _, port = args.vnc.partition(":")
    client = RfbClient(host, int(port or 5900))
    sink = args.ssh if args.ssh else f"http://{args.robot}:{args.robot_port}/lcd/image"
    print(f"[mirror] {args.vnc} ({client.w}x{client.h}) -> {sink}  간격 {args.interval}s")

    sent = skipped = 0
    last_sig = None
    while True:
        t0 = time.time()
        img = to_lcd(client.frame(), args.fit)
        sig = img.tobytes()
        if sig == last_sig:
            skipped += 1
            time.sleep(max(0.0, args.interval - (time.time() - t0)))
            continue
        last_sig = sig
        if args.save:
            img.save(args.save)
        try:
            if args.ssh:
                push_ssh(args.ssh, img)
            else:
                post_lcd(sink, img)
            sent += 1
        except Exception as e:
            print(f"[mirror] 전송 실패({type(e).__name__}: {e}) — 계속 시도합니다", file=sys.stderr)
        if args.once:
            print(f"[mirror] 1장 처리, {time.time() - t0:.2f}s")
            return
        if sent % 20 == 0:
            print(f"[mirror] 전송 {sent} · 생략(무변화) {skipped}", flush=True)
        time.sleep(max(0.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
```
"""
