#!/usr/bin/env python3
"""야간 감시용 **더미 뷰어** — 붙어 있는 것 자체가 목적이다.

## ⚠️ [2026-08-06] 이제 필요 없다 — 되돌림용으로만 남긴다

`perception_server.serve_loop` 이 `accept_srv` 를 받아 **뷰어 없이도 프레임 루프를
돌린다**(뷰어는 있으면 보내고 없으면 마는 옵셔널 슬롯이다). 그래서 자리를 채워 줄
마네킹이 필요 없어졌고, `libi_laptop.sh` 는 이걸 기본으로 안 띄운다(`--secview` 로만).
아래 설명은 그 변경 **이전** 서버의 이야기다 — 옛 서버로 되돌릴 때만 유효하다.

## 왜 필요했는가

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

## 패널 추종과 자리를 나눠 쓴다 — 낮에는 스스로 비켜준다

뷰어는 한 번에 하나뿐이라, 이게 붙어 있으면 패널의 추종 화면은 검게 나온다.
그래서 `--ops-url` 을 주면 관제의 day/night 모드를 주기로 확인해서, **낮에는 접속을
시도하지 않고 이미 붙어 있었다면 스스로 끊는다** — 패널이 그 자리를 쓰게 비켜준다.
`--ops-url` 을 안 주면(또는 모드 확인이 계속 실패하면) 예전처럼 무조건 붙어 있는다.

    python3 scripts/security_viewer.py --host 192.168.1.10 --port 5027 \
        --ops-url http://192.168.1.10:8000
"""
import argparse
import json
import socket
import threading
import urllib.request


def drain(sock, stop_evt=None, mode_check=None, tick_sec=2.0):
    """EOF 까지 읽어 버린다. 돌아오면 연결이 끝난 것이다.

    `mode_check` 가 있으면 `tick_sec` 마다 불러 "day" 면 스스로 끊는다 — 그러려고
    소켓에 타임아웃을 걸어 `recv()` 가 주기적으로 깨어나게 한다(`socket.timeout` 은
    `OSError` 의 하위클래스라 EOF/끊김과 구분해서 따로 잡는다 — 안 그러면 매 tick 마다
    "연결이 끝났다"로 오판해 불필요하게 재접속한다).
    """
    if mode_check is not None:
        sock.settimeout(tick_sec)
    while stop_evt is None or not stop_evt.is_set():
        if mode_check is not None and mode_check() == "day":
            print("[secview] 낮 모드 — 패널에 자리를 비켜줍니다", flush=True)
            return
        try:
            if not sock.recv(65536):
                return
        except socket.timeout:
            continue
        except OSError:
            return


def run(host, port, *, connect_fn=None, stop_evt=None, retry_sec=1.0, mode_check=None):
    """무한 재접속. 서버가 늦게 떠도, 잠깐 끊겨도 스스로 붙는다.

    `mode_check` 가 "day" 를 돌려주는 동안은 접속 시도 자체를 하지 않는다 — 패널이
    그 자리를 쓸 수 있게. 확인 실패(`None`)는 "day" 가 아니므로 안전하게(=야간 취급)
    계속 접속을 시도한다.
    """
    stop_evt = stop_evt or threading.Event()
    connect_fn = connect_fn or _connect
    while not stop_evt.is_set():
        if mode_check is not None and mode_check() == "day":
            if stop_evt.wait(retry_sec):
                return
            continue
        sock = None
        try:
            sock = connect_fn(host, port)
            print(f"[secview] 붙었습니다 {host}:{port}", flush=True)
            drain(sock, stop_evt, mode_check=mode_check)
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


def make_mode_check(ops_url, *, get_fn=None, timeout=3.0):
    """`GET {ops_url}/api/admin/ops/security/mode` 를 불러 `"day"`/`"night"` 를
    돌려준다. 실패하면 `None`(모른다) — `run()`/`drain()` 은 그걸 "day 아님"으로 다뤄
    안전한 쪽(=계속 접속)으로 넘어간다."""
    def _default_get(url, timeout):
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read())

    get_fn = get_fn or _default_get

    def check():
        try:
            return get_fn(f"{ops_url}/api/admin/ops/security/mode", timeout).get("mode")
        except Exception:                                        # noqa: BLE001
            return None
    return check


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5007)
    ap.add_argument("--ops-url", dest="ops_url", default=None,
                     help="관제 백엔드 URL. 주면 day 모드일 때 스스로 접속을 비운다.")
    a = ap.parse_args()
    print("[secview] ⚠️ 이게 붙어 있는 동안 패널 추종 화면은 검게 나옵니다 "
          "(인지 서버는 뷰어를 한 번에 하나만 받습니다)", flush=True)
    mode_check = make_mode_check(a.ops_url) if a.ops_url else None
    if mode_check is None:
        print("[secview] ⚠️ --ops-url 없음 — 낮에도 계속 자리를 차지합니다", flush=True)
    run(a.host, a.port, mode_check=mode_check)


if __name__ == "__main__":
    main()
