import socket
from scripts.frame_proto import send_frame, recv_frame


def test_round_trip():
    a, b = socket.socketpair()
    send_frame(a, b"hello-jpeg")
    assert recv_frame(b) == b"hello-jpeg"
    a.close(); b.close()


def test_multiple_frames_in_order():
    a, b = socket.socketpair()
    send_frame(a, b"one"); send_frame(a, b"two")
    assert recv_frame(b) == b"one"
    assert recv_frame(b) == b"two"
    a.close(); b.close()


def test_eof_returns_none():
    a, b = socket.socketpair()
    a.close()
    assert recv_frame(b) is None
    b.close()
