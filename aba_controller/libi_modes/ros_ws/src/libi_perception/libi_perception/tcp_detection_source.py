import json
import socket
import threading


class TcpDetectionSource:
    """TCP server that receives newline-delimited Detection JSON from the AI
    server and buffers payloads for ControlLoop. Non-owner frames arrive as
    the JSON literal `null`. `.poll()` drains the buffer."""

    def __init__(self, host, port):
        self._buf = []
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            conn, _ = self._sock.accept()
            with conn:
                buffer = b''
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        if not line.strip():
                            continue
                        payload = json.loads(line.decode('utf-8'))
                        with self._lock:
                            self._buf.append(payload)

    def poll(self):
        with self._lock:
            out, self._buf = self._buf, []
        return out
