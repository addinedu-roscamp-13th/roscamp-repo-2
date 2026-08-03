"""더미 뷰어 — 붙어 있는 것 자체가 목적. 프레임은 읽어서 버린다."""
import socket
import threading

from scripts.security_viewer import drain, make_mode_check, run


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

    def settimeout(self, _t):
        pass


class FakeTimeoutSock:
    """진짜 EOF 는 절대 안 오고, recv 가 항상 timeout 만 던진다(패널 미접속 상태 흉내)."""

    def __init__(self):
        self.settimeout_calls = []

    def settimeout(self, t):
        self.settimeout_calls.append(t)

    def recv(self, _n):
        raise socket.timeout()


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


def test_낮_모드면_접속_시도_자체를_안_한다():
    """패널이 그 자리를 쓸 수 있게 — day 인 동안은 connect_fn 을 아예 안 부른다."""
    attempts = []
    def connect(host, port):
        attempts.append(1)
        return FakeSock([b"x"])

    calls = {"n": 0}
    def mode_check():
        calls["n"] += 1
        if calls["n"] >= 3:
            stop.set()
        return "day"

    stop = threading.Event()
    run("127.0.0.1", 5027, connect_fn=connect, stop_evt=stop, retry_sec=0,
        mode_check=mode_check)
    assert attempts == []
    assert calls["n"] >= 3


def test_접속_중_낮으로_바뀌면_스스로_끊는다():
    """EOF 가 안 와도(패널이 안 붙어 서버가 아무것도 안 보내도) 낮이면 빠져나온다."""
    calls = {"n": 0}
    def mode_check():
        calls["n"] += 1
        return "night" if calls["n"] < 3 else "day"

    sock = FakeTimeoutSock()
    drain(sock, mode_check=mode_check, tick_sec=0.001)
    assert calls["n"] == 3
    assert sock.settimeout_calls == [0.001]


def test_모드_확인_실패는_day가_아니므로_계속_접속한다():
    """.env/백엔드 문제로 mode 조회가 실패해도(None) 감시가 조용히 꺼지면 안 된다."""
    attempts = []
    def connect(host, port):
        attempts.append(1)
        if len(attempts) >= 2:
            stop.set()
        return FakeSock([b"x"])

    def mode_check():
        return None  # 확인 실패

    stop = threading.Event()
    run("127.0.0.1", 5027, connect_fn=connect, stop_evt=stop, retry_sec=0,
        mode_check=mode_check)
    assert len(attempts) >= 2


def test_make_mode_check_이_day_night을_그대로_돌려준다():
    def fake_get(url, timeout):
        assert url == "http://ops/api/admin/ops/security/mode"
        return {"mode": "night"}

    check = make_mode_check("http://ops", get_fn=fake_get)
    assert check() == "night"


def test_make_mode_check_이_실패하면_None을_돌려준다():
    def fake_get(url, timeout):
        raise TimeoutError("연결 안 됨")

    check = make_mode_check("http://ops", get_fn=fake_get)
    assert check() is None
