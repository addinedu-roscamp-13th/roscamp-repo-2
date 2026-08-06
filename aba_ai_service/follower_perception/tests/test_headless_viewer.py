"""뷰어는 **옵셔널 슬롯**이다 — 없어도 녹화가 돌고, 도중에 붙어도 받는다.

이게 없던 시절엔 프레임 루프가 통째로 `serve_loop(conn, ...)` 안에 있어서 뷰어 TCP
연결이 없으면 서버가 아무 일도 안 했다. 야간엔 아무도 패널을 안 보므로 자리만 채우는
더미 뷰어(`security_viewer.py`)를 붙였고, **그게 붙어 있는 동안 패널의 추종 화면이
검게 나왔다**(뷰어는 한 번에 하나).

여기서 지키는 계약 네 개:

1. `conn=None` + 무장 → 뷰어 없이도 `recorder.feed` 가 불린다 (녹화가 돈다)
2. `conn=None` + 비무장 → 추론을 안 돌린다 (아무도 안 보는 낮에 노트북을 안 태운다)
3. 도중에 뷰어가 붙으면 `accept_srv` 로 받아서 그 뒤 프레임을 보낸다
4. `accept_srv` 없이 부르면 **예전 그대로** — EOF 에 리턴한다 (test_viewer_disconnect)
"""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import pytest  # noqa: E402

perception_server = pytest.importorskip(
    "perception_server", reason="cv2/ultralytics 없는 환경에서는 건너뛴다")


class FakeRecorder:
    """`wants_armed` 와 `feed` 만 흉내낸다 — serve_loop 이 쓰는 전부다."""

    def __init__(self, armed):
        self.wants_armed = armed
        self.jpegs = 0
        self.beats = 0

    def feed(self, jpeg, size, frame=None, cands=None):
        if jpeg is None:
            self.beats += 1
        else:
            self.jpegs += 1


class FakePerception:
    """`perception.run()` 이 불렸는지만 센다. 나머지는 `_status_line`/`_pose_payload`
    가 읽는 최소 표면이다(test_perception_server_security.py 의 `_Perception` 과 같은
    모양 — 거기서 import 하면 pytest rootdir 에 따라 깨져서 여기 다시 적는다)."""

    class _M:
        is_registered = True         # True 면 `_pick_central` 을 안 탄다
        gallery = []
        safe_id = None
        last_reid_sim = None
        last_hsv_sim = None
        reid_threshold = 0.0
        hsv_threshold = None

    def __init__(self):
        self.runs = 0
        self.last_cands = []
        self.matcher = self._M()
        self.pose = None

    def run(self, frame):
        self.runs += 1

    def get_latest(self):
        return None


def _frames(n):
    """`serve_loop` 이 실제로 다루는 모양의 프레임 — shape 만 있으면 된다."""
    import numpy as np
    return iter([np.zeros((48, 64, 3), dtype=np.uint8) for _ in range(n)])


def _run(conn, frames, perception, **kw):
    """공통 호출 — 실패 원인을 시험 쪽 인자 차이로 흐리지 않으려고 한 곳에 모은다."""
    return perception_server.serve_loop(
        conn, frames, perception, poll_cmd=perception_server.make_socket_poller(),
        **kw)


def test_무장이면_뷰어_없이도_녹화가_돈다():
    """이 파일의 존재 이유 — 더미 뷰어를 지울 수 있는 근거다."""
    rec = FakeRecorder(armed=True)
    _run(None, _frames(3), FakePerception(), recorder=rec)
    assert rec.jpegs == 3, "뷰어가 없다고 녹화를 멈추면 야간에 아무것도 안 남는다"


def test_비무장이면_뷰어_없이_추론을_안_돌린다():
    """낮 순찰 내내 아무도 안 보는데 YOLO 를 돌리면 노트북만 탄다."""
    rec = FakeRecorder(armed=False)
    per = FakePerception()
    _run(None, _frames(5), per, recorder=rec)
    assert per.runs == 0
    assert rec.jpegs == 0
    assert rec.beats == 5, "심장박동은 계속 넣어야 모드 전이·클립 마감 시계가 돈다"


def test_도중에_붙은_뷰어가_프레임을_받는다():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())      # 붙어서 대기만 한다
    try:
        rec = FakeRecorder(armed=True)
        _run(None, _frames(2), FakePerception(), recorder=rec, accept_srv=srv)
        cli.settimeout(2.0)
        assert cli.recv(4), "accept 해놓고 아무것도 안 보내면 패널은 검은 채로 남는다"
    finally:
        cli.close()
        srv.close()


def test_프레임_소스가_끝나면_붙어있던_뷰어를_닫는다():
    """안 닫으면 호출자는 이 conn 을 모르므로 소켓이 샌다 — 다음 뷰어가 붙어도
    옛 소켓이 CLOSE-WAIT 로 남는다."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    try:
        _run(None, _frames(2), FakePerception(),
             recorder=FakeRecorder(armed=True), accept_srv=srv)
        cli.settimeout(2.0)
        while cli.recv(65536):                 # 보낸 프레임을 다 비우면 EOF 가 와야 한다
            pass
    finally:
        cli.close()
        srv.close()


def test_진짜_recorder_도_wants_armed_를_노출한다(tmp_path):
    """`FakeRecorder` 가 실제와 어긋나면 이 파일의 초록은 거짓이 된다."""
    from security_recorder import SecurityRecorder
    rec = SecurityRecorder(robot_name="pinky-3", media_dir=str(tmp_path))
    assert rec.wants_armed is False
    rec.arm(True)
    assert rec.wants_armed is True


def test_accept_srv_없으면_옛_동작_그대로_EOF_에_리턴한다():
    """예전 호출자(테스트 포함)를 안 깨뜨린다는 계약."""
    a, b = socket.socketpair()
    b.close()
    try:
        # 무한 프레임 — EOF 로만 끝날 수 있다. 안 끝나면 여기서 멈춰 시험이 실패한다.
        _run(a, iter(lambda: None, "sentinel"), FakePerception(), recorder=None)
    finally:
        a.close()
