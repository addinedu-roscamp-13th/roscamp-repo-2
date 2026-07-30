"""UDP video transport (robot Pi -> AI server).

Sender: resize to 640-wide -> JPEG -> split into UDP-sized chunks -> sendto.
Receiver: a background thread reassembles chunks and keeps ONLY the newest
frame; stale/older frames are dropped so perception always works on the freshest
frame (no latency backlog).

Packet = [frame_id:uint32][chunk_idx:uint16][total_chunks:uint16][jpeg bytes...]
"""
import socket
import struct
import threading

import cv2
import numpy as np

_HDR = struct.Struct(">IHH")     # frame_id, chunk_idx, total_chunks
DEFAULT_CHUNK = 1400             # JPEG payload bytes per packet (under typical MTU)


def resize_to_width(frame, width=640):
    h, w = frame.shape[:2]
    if w == width:
        return frame
    nh = max(1, int(round(h * width / float(w))))
    return cv2.resize(frame, (width, nh))


def encode_jpeg(frame, quality=70):
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    return buf.tobytes() if ok else None


def split_chunks(frame_id, payload, chunk_size=DEFAULT_CHUNK):
    total = (len(payload) + chunk_size - 1) // chunk_size or 1
    return [_HDR.pack(frame_id, i, total) + payload[i * chunk_size:(i + 1) * chunk_size]
            for i in range(total)]


class FrameReassembler:
    """Reassembles chunked frames, keeping only the newest. feed() returns the
    complete JPEG bytes when the newest frame finishes, else None. Older frames
    (already superseded) and their partials are dropped."""

    def __init__(self):
        self._buffers = {}           # frame_id -> {chunk_idx: bytes}
        self._latest_done = -1

    def feed(self, packet):
        if len(packet) < _HDR.size:
            return None
        fid, idx, total = _HDR.unpack(packet[:_HDR.size])
        if fid <= self._latest_done:
            if fid < self._latest_done - 30:     # frame_id jumped back far = sender restart
                self._buffers.clear()            # -> resync instead of dropping everything
                self._latest_done = -1
            else:
                return None                      # stale/reordered -> drop
        buf = self._buffers.setdefault(fid, {})
        buf[idx] = packet[_HDR.size:]
        if len(buf) >= total:
            jpeg = b"".join(buf[i] for i in range(total))
            self._latest_done = fid
            for k in [k for k in self._buffers if k <= fid]:
                del self._buffers[k]             # drop this + all older partials
            return jpeg
        return None


class UdpVideoSender:
    """Robot side. Call send(frame) per camera frame."""

    def __init__(self, host, port, *, width=640, quality=70, chunk_size=DEFAULT_CHUNK):
        self._addr = (host, int(port))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._w, self._q, self._cs = width, quality, chunk_size
        self._fid = 0

    def send(self, frame):
        payload = encode_jpeg(resize_to_width(frame, self._w), self._q)
        if payload is None:
            return
        for pkt in split_chunks(self._fid, payload, self._cs):
            self._sock.sendto(pkt, self._addr)
        self._fid = (self._fid + 1) & 0xFFFFFFFF

    def close(self):
        self._sock.close()


class UdpVideoReceiver:
    """AI-server side. Background thread keeps only the newest decoded frame.
    frames() yields fresh frames (stale ones dropped)."""

    def __init__(self, port, host="0.0.0.0", *, rcvbuf=1 << 22):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        except OSError:
            pass
        self._sock.bind((host, int(port)))
        self._reasm = FrameReassembler()
        self._latest = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while self._run:
            try:
                pkt, _ = self._sock.recvfrom(65535)
            except OSError:
                break
            jpeg = self._reasm.feed(pkt)
            if jpeg is None:
                continue
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                with self._lock:
                    self._latest = img
                self._event.set()

    def frames(self, idle_yield=False, idle_timeout=0.5):
        """수신한 최신 프레임을 흘린다. 중간 프레임은 버린다.

        `idle_yield=True` 면 영상이 안 올 때 `idle_timeout` 마다 **None 을 흘린다.**
        소비자가 프레임 도착에 묶이지 않고 계속 돌 수 있게 하는 심장박동이다.

        왜 필요한가 (실측 2026-07-28): 로봇은 `camera_select` 가 `none` 이면 아무것도
        안 보낸다(추종·길잡이 세션이 없을 때가 그렇다). 그때 이 제너레이터가 아무것도
        안 흘리면 `perception_server.serve_loop` 의 `for frame in frames:` 가 첫
        프레임에서 멈춰 **뷰어 소켓을 한 번도 확인하지 않는다.** 패널이 끊어도 못
        알아채 소켓이 CLOSE-WAIT 로 남고, `listen(1)` + 뷰어 1개 구조라 **다음 패널이
        영영 못 붙는다**("AI 서버에 연결 중…"). 영상을 못 봐서 등록을 못 하고, 등록을
        못 해서 세션이 없고, 세션이 없어서 영상이 안 오는 자기강화 교착이었다.

        기본값은 끔 — `track_frames` 처럼 진짜 프레임만 기대하는 소비자를 안 건드린다.
        """
        while self._run:
            if not self._event.wait(timeout=idle_timeout if idle_yield else 2.0):
                if idle_yield:
                    yield None                    # 심장박동: 소비자가 한 바퀴 돌 기회
                continue                          # no frame yet; keep waiting
            self._event.clear()
            with self._lock:
                f, self._latest = self._latest, None
            if f is not None:
                yield f                           # newest only; intermediates dropped

    def close(self):
        self._run = False
        try:
            self._sock.close()
        except OSError:
            pass
