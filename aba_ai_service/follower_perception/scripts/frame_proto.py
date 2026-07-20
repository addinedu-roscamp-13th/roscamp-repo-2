"""Length-prefixed frame framing over a stream socket. Shared by the
perception server and its test client."""
import struct


def send_frame(sock, payload):
    sock.sendall(struct.pack(">I", len(payload)) + bytes(payload))


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_frame(sock):
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    (n,) = struct.unpack(">I", header)
    return _recv_exact(sock, n)
