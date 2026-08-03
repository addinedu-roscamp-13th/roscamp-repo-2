"""더미 뷰어 — 붙어 있는 것 자체가 목적. 프레임은 읽어서 버린다."""
import threading

from scripts.security_viewer import drain, run


class FakeSock:
    """`recv` 로 정해진 조각을 주고, 다 떨어지면 b"" (EOF)."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.recv_count = 0
        self.closed = False

    def recv(self, _n):
        self.recv_count += 1
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


def test_프레임을_끝까지_읽어_버린다():
    """안 읽으면 TCP 버퍼가 차서 서버의 send_frame 이 블록하고 루프가 멈춘다."""
    s = FakeSock([b"a" * 1000, b"b" * 1000, b"c" * 1000])
    drain(s)
    assert s.recv_count == 4          # 3조각 + EOF


def test_연결이_끊기면_재접속한다():
    attempts = []
    def connect(host, port):
        attempts.append((host, port))
        if len(attempts) >= 3:
            stop.set()
        return FakeSock([b"x"])

    stop = threading.Event()
    run("127.0.0.1", 5027, connect_fn=connect, stop_evt=stop, retry_sec=0)
    assert len(attempts) >= 3


def test_접속이_실패해도_루프가_안_끝난다():
    attempts = []
    def connect(host, port):
        attempts.append(1)
        if len(attempts) >= 3:
            stop.set()
        raise OSError("연결 거부")

    stop = threading.Event()
    run("127.0.0.1", 5027, connect_fn=connect, stop_evt=stop, retry_sec=0)
    assert len(attempts) >= 3


def test_OSError가_아닌_예외도_삼키고_재시도한다():
    """connect_fn 버그(예: ValueError)로 밤새 프로세스가 죽으면 안 된다."""
    attempts = []
    def connect(host, port):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("커스텀 connect_fn 버그")
        stop.set()
        return FakeSock([b"x"])

    stop = threading.Event()
    run("127.0.0.1", 5027, connect_fn=connect, stop_evt=stop, retry_sec=0)
    assert len(attempts) >= 2
