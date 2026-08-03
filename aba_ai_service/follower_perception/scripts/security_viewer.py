#!/usr/bin/env python3
"""야간 감시용 **더미 뷰어** — 붙어 있는 것 자체가 목적이다.

## 왜 필요한가

`perception_server.main()` 은 `srv.listen(1)` 뒤 `accept()` 에서 블록하고,
프레임 소비·YOLO·로봇 검출 송출·녹화가 **전부 `serve_loop(conn, ...)` 안**에 있다.
즉 **뷰어 TCP 연결이 없으면 인지 서버는 아무 일도 안 한다.**

패널(`libi_gui`)은 「추종」 화면을 열어야만 붙는다(`FollowScreen.qml:106`
`Component.onCompleted: perception.start()`). 야간엔 아무도 패널 앞에 없다.
그래서 관객석에 마네킹을 앉힌다.

## 반드시 읽어서 버려야 한다

안 읽으면 TCP 수신 버퍼가 차서 서버의 `send_frame` 이 **블록**하고, 프레임 루프가
통째로 멈춘다. 명령은 안 보내도 된다 — 서버의 `make_socket_poller` 는 논블로킹
(`select(..., 0)`)이라 이쪽이 조용해도 문제없다.

## ⚠️ 패널 추종과 상호 배타다

뷰어는 한 번에 하나뿐이다. 이게 붙어 있으면 패널의 추종 화면은 검게 나온다.
관리자 추종을 시연하려면 이 프로세스를 먼저 내린다.

    python3 scripts/security_viewer.py --host 192.168.1.10 --port 5027
"""
import argparse
import socket
import threading


def drain(sock, stop_evt=None):
    """EOF 까지 읽어 버린다. 돌아오면 연결이 끝난 것이다."""
    while stop_evt is None or not stop_evt.is_set():
        try:
            if not sock.recv(65536):
                return
        except OSError:
            return


def run(host, port, *, connect_fn=None, stop_evt=None, retry_sec=1.0):
    """무한 재접속. 서버가 늦게 떠도, 잠깐 끊겨도 스스로 붙는다."""
    stop_evt = stop_evt or threading.Event()
    connect_fn = connect_fn or _connect
    while not stop_evt.is_set():
        sock = None
        try:
            sock = connect_fn(host, port)
            print(f"[secview] 붙었습니다 {host}:{port}", flush=True)
            drain(sock, stop_evt)
        except Exception as e:                                  # noqa: BLE001
            print(f"[secview] 접속 실패({e}) — {retry_sec}초 뒤 재시도", flush=True)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if stop_evt.wait(retry_sec):
            return


def _connect(host, port):
    s = socket.create_connection((host, int(port)), timeout=5)
    s.settimeout(None)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5007)
    a = ap.parse_args()
    print("[secview] ⚠️ 이게 붙어 있는 동안 패널 추종 화면은 검게 나옵니다 "
          "(인지 서버는 뷰어를 한 번에 하나만 받습니다)", flush=True)
    run(a.host, a.port)


if __name__ == "__main__":
    main()
