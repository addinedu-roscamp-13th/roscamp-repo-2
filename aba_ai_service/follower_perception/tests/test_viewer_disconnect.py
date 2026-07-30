"""뷰어가 끊기면 `serve_loop` 이 **빠져나와야** 한다.

실측 사고(2026-07-28): 패널을 껐다 켰더니 새 패널이 "AI 서버에 연결 중…"에서 멈췄다.

    ss -tnp
      CLOSE-WAIT  5007 → 45830   python fd=40      ← 서버가 죽은 소켓을 붙들고 있다
      SYN-SENT    libi_gui → 5007                  ← 새 패널이 못 붙는다

원인: `poll` 이 EOF(`recv() == b""`)를 `None`(명령 없음)과 같게 취급했다. `select` 는
EOF 를 계속 readable 로 보고하므로 루프가 영원히 돌고, `listen(1)` + 뷰어 1개 구조라
`accept()` 에 도달하지 못한다. `send_frame` 도 안 터진다 — half-close(FIN)만 오고
RST 가 없으면 송신은 한동안 성공하기 때문이다.
"""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import pytest  # noqa: E402

perception_server = pytest.importorskip(
    "perception_server", reason="cv2/ultralytics 없는 환경에서는 건너뛴다")

EOF = perception_server.EOF
make_socket_poller = perception_server.make_socket_poller


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    yield a, b
    for s in (a, b):
        try:
            s.close()
        except OSError:
            pass


def test_no_data_is_none_not_eof(pair):
    """조용한 tick 을 끊김으로 오인하면 멀쩡한 뷰어를 걷어찬다."""
    srv, _cli = pair
    assert make_socket_poller()(srv) is None


def test_peer_close_reports_eof(pair):
    srv, cli = pair
    cli.close()
    assert make_socket_poller()(srv) is EOF


def test_peer_half_close_reports_eof(pair):
    """FIN 만 오고 RST 는 안 오는 경우 — 실제로 당한 모양이다."""
    srv, cli = pair
    cli.shutdown(socket.SHUT_WR)
    assert make_socket_poller()(srv) is EOF


def test_eof_is_repeatable_not_a_command(pair):
    """EOF 뒤에도 계속 EOF 여야 한다. 한 번만 알리고 None 으로 돌아가면 다시 갇힌다."""
    srv, cli = pair
    cli.close()
    poll = make_socket_poller()
    assert poll(srv) is EOF
    assert poll(srv) is EOF


def test_command_still_parsed(pair):
    srv, cli = pair
    cli.sendall(b"register\n")
    assert make_socket_poller()(srv) == "register"


def test_last_command_wins(pair):
    srv, cli = pair
    cli.sendall(b"register\nreset\n")
    assert make_socket_poller()(srv) == "reset"


def test_serve_loop_returns_on_eof(pair):
    """이 파일의 존재 이유 — 루프가 실제로 **끝나야** 다음 뷰어가 붙는다."""
    srv, cli = pair
    cli.close()

    frames = iter(lambda: object(), None)          # 무한 — EOF 로만 끝날 수 있다
    done = threading.Event()

    def run():
        perception_server.serve_loop(
            srv, frames, perception=None, poll_cmd=make_socket_poller())
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert done.wait(timeout=5.0), "serve_loop 이 EOF 에서 안 빠져나왔다 (예전 버그)"


# ── 심장박동 (2026-07-28) ────────────────────────────────────────────────────
# 영상이 없어도 루프가 돌아야 뷰어 끊김을 알아챈다. 이게 없으면 EOF 수정도 무용지물 —
# poll_cmd 가 애초에 안 불린다.

def test_serve_loop_notices_eof_with_no_video(pair):
    """프레임이 **하나도 안 오는** 동안 패널이 끊겨도 빠져나와야 한다."""
    srv, cli = pair
    cli.close()

    frames = iter(lambda: None, "sentinel")     # 무한 None = 영상 없음
    done = threading.Event()

    def run():
        perception_server.serve_loop(
            srv, frames, perception=None, poll_cmd=make_socket_poller())
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=5.0), "영상 없이 끊기면 못 빠져나온다 (CLOSE-WAIT 사고)"


def test_heartbeat_does_not_touch_perception(pair):
    """None 프레임에 추론을 돌리면 그 자리에서 터진다."""
    srv, _cli = pair                             # 살아 있는 연결 = EOF 아님
    frames = iter([None, None, None])            # 유한 — 끝나면 루프도 끝난다
    perception_server.serve_loop(
        srv, frames, perception=None, poll_cmd=make_socket_poller())
