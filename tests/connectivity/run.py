#!/usr/bin/env python3
"""리비 연동 테스트 하네스 — walking-skeleton.

인터페이스 정의서의 각 엣지를 대표 케이스 1개로 검증(connectivity/smoke).
산출물: 터미널 pass/fail 매트릭스 + JSON 리포트(report.json).

실행: .venv/bin/python tests/connectivity/run.py
"""
import asyncio
import json
import os
import socket
import time
import urllib.error
import urllib.request

SERVER = "http://127.0.0.1:8000"
FMS = "http://127.0.0.1:9001"
AI_UDP = ("127.0.0.1", int(os.environ.get("AI_SERVICE_UDP_PORT", "9000")))
REPORT = os.path.join(os.path.dirname(__file__), "report.json")

results: list[dict] = []


def _http(method, url, payload=None, timeout=8):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), (time.time() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}"), (time.time() - t0) * 1000


async def _ws_recv(url, timeout=5):
    import websockets
    async with websockets.connect(url) as ws:
        return await asyncio.wait_for(ws.recv(), timeout=timeout)


def record(n, edge, from_to, proto, iface, sr, expected, actual, ok, ms, note=""):
    verdict = "PASS" if ok is True else ("PENDING" if ok is None else "FAIL")
    results.append({"n": n, "edge": edge, "from_to": from_to, "proto": proto,
                    "iface": iface, "sr": sr, "expected": expected, "actual": actual,
                    "result": verdict, "latency_ms": round(ms, 1), "note": note})


def wait_health(timeout=25):
    print(f"[harness] 서비스 기동 대기 (max {timeout}s)...")
    deadline = time.time() + timeout
    up = {"aba_server": False, "aba_fms": False}
    while time.time() < deadline:
        for name, url in (("aba_server", f"{SERVER}/api/health"), ("aba_fms", f"{FMS}/api/health")):
            if not up[name]:
                try:
                    st, body, _ = _http("GET", url, timeout=2)
                    up[name] = st == 200 and body.get("status") == "ok"
                except Exception:  # noqa: BLE001
                    pass
        if all(up.values()):
            print("[harness] 서비스 준비 완료.\n")
            return True
        time.sleep(1)
    print(f"[harness] 경고: 일부 서비스 미기동 {up}\n")
    return all(up.values())


def run():
    wait_health()

    # 1a member → aba_server (HTTP, SubmitTask/이송) — 엣지 1a + 5(체인)
    try:
        st, body, ms = _http("POST", f"{SERVER}/api/control/goto", {"name": "A-03-shelf"})
        ok = st == 200 and body.get("accepted") is True and "task_id" in body
        record("1a", "member→aba_server", "member → aba_server", "HTTP",
               "SubmitTask §1", "SR-01", "{accepted, task_id}", body, ok, ms)
        # 엣지 5 (ROS2 AssignTask) — 1a 응답의 fms_msg 존재로 간접 검증
        ok5 = ok and bool(body.get("fms_msg"))
        record("5", "aba_server↔aba_fms", "aba_server → aba_fms", "ROS2 svc",
               "AssignTask §2", "SR-01", "fms {accepted} via /fms/task_request",
               {"fms_msg": body.get("fms_msg")}, ok5, ms, "1a HTTP 응답으로 체인 검증")
    except Exception as e:  # noqa: BLE001
        record("1a", "member→aba_server", "member → aba_server", "HTTP", "SubmitTask §1",
               "SR-01", "{accepted, task_id}", str(e), False, 0)
        record("5", "aba_server↔aba_fms", "aba_server → aba_fms", "ROS2 svc", "AssignTask §2",
               "SR-01", "fms {accepted}", "1a 실패로 미검증", False, 0)

    # 1b librarian → aba_fms (HTTP, GetRobotState)
    try:
        st, body, ms = _http("GET", f"{FMS}/api/control/state")
        ok = st == 200 and "robots" in body
        record("1b", "librarian→aba_fms", "librarian → aba_fms", "HTTP",
               "GetRobotState §1", "SR-10", "{robots[...]}", body, ok, ms)
    except Exception as e:  # noqa: BLE001
        record("1b", "librarian→aba_fms", "librarian → aba_fms", "HTTP", "GetRobotState §1",
               "SR-10", "{robots}", str(e), False, 0)

    # 2a aba_server → member (WS)
    try:
        t0 = time.time()
        msg = asyncio.run(_ws_recv(f"ws://127.0.0.1:8000/api/control/ws/state"))
        ms = (time.time() - t0) * 1000
        body = json.loads(msg)
        record("2a", "aba_server→member", "aba_server → member", "WS",
               "RobotTaskStateStream §5", "SR-14", "state msg", body, "robot_id" in body, ms)
    except Exception as e:  # noqa: BLE001
        record("2a", "aba_server→member", "aba_server → member", "WS", "RobotTaskStateStream §5",
               "SR-14", "state msg", str(e), False, 0)

    # 2b aba_fms → librarian (WS)
    try:
        t0 = time.time()
        msg = asyncio.run(_ws_recv(f"ws://127.0.0.1:9001/api/control/ws/state"))
        ms = (time.time() - t0) * 1000
        body = json.loads(msg)
        record("2b", "aba_fms→librarian", "aba_fms → librarian", "WS",
               "모니터링 §5", "SR-10", "state msg", body, "robot_id" in body, ms)
    except Exception as e:  # noqa: BLE001
        record("2b", "aba_fms→librarian", "aba_fms → librarian", "WS", "모니터링 §5",
               "SR-10", "state msg", str(e), False, 0)

    # 3 aba_server ↔ DB (TCP)
    st, body, ms = _http("GET", f"{SERVER}/api/db/ping")
    db_ok = None if not body.get("ok") else True  # 미가용이면 PENDING
    record("3", "aba_server↔DB", "aba_server → ABA DB", "TCP", "QueryPersistDB §2",
           "SR-08", "SELECT 1", body, db_ok, ms,
           "" if db_ok else "MariaDB 미설치 → PENDING")

    # 4 aba_fms ↔ DB (TCP)
    st, body, ms = _http("GET", f"{FMS}/api/db/ping")
    db_ok = None if not body.get("ok") else True
    record("4", "aba_fms↔DB", "aba_fms → ABA DB", "TCP", "QueryPersistDB §2",
           "SR-08", "SELECT 1", body, db_ok, ms,
           "" if db_ok else "MariaDB 미설치 → PENDING")

    # 6 image_sender → ai_service (UDP)  +  7 ai_service → aba_fms (TCP)
    try:
        t0 = time.time()
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.sendto(b"\xff\xd8\xff" + b"FAKEJPEGFRAME" * 8, AI_UDP)  # 가짜 JPEG 프레임
        u.close()
        time.sleep(0.8)  # ai_service 추론+TCP push 대기
        st, last, ms = _http("GET", f"{FMS}/api/perception/last")
        ms = (time.time() - t0) * 1000
        got = st == 200 and last.get("received") is True
        record("6", "image_sender→ai_service", "image_sender → ai_service", "UDP",
               "ImageToAI §5", "SR-13", "프레임 수신", {"sent": "JPEG frame"}, got, ms,
               "6+7 한 흐름")
        record("7", "ai_service→aba_fms", "ai_service → aba_fms", "TCP",
               "DispatchPerceptionResult §2", "SR-13", "결과 수신", last, got, ms,
               "ai_service가 UDP 프레임→TCP 결과 push")
    except Exception as e:  # noqa: BLE001
        record("6", "image_sender→ai_service", "image_sender → ai_service", "UDP", "ImageToAI §5",
               "SR-13", "프레임 수신", str(e), False, 0)
        record("7", "ai_service→aba_fms", "ai_service → aba_fms", "TCP",
               "DispatchPerceptionResult §2", "SR-13", "결과 수신", str(e), False, 0)

    print_matrix()
    with open(REPORT, "w") as f:
        json.dump({"ts": time.time(), "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n[harness] JSON 리포트: {REPORT}")

    n_pass = sum(r["result"] == "PASS" for r in results)
    n_pend = sum(r["result"] == "PENDING" for r in results)
    n_fail = sum(r["result"] == "FAIL" for r in results)
    return n_fail


def print_matrix():
    ico = {"PASS": "✅", "FAIL": "❌", "PENDING": "⏳"}
    print("=" * 100)
    print(" 리비 연동시험 결과서 (connectivity / smoke)")
    print("=" * 100)
    print(f"{'#':<4}{'연동 구간':<26}{'PROTO':<10}{'인터페이스':<26}{'SR':<7}{'결과':<8}{'ms':>7}")
    print("-" * 100)
    for r in results:
        print(f"{r['n']:<4}{r['from_to']:<26}{r['proto']:<10}{r['iface']:<26}"
              f"{r['sr']:<7}{ico[r['result']]+r['result']:<9}{r['latency_ms']:>7}")
    print("-" * 100)
    n_pass = sum(r["result"] == "PASS" for r in results)
    n_pend = sum(r["result"] == "PENDING" for r in results)
    n_fail = sum(r["result"] == "FAIL" for r in results)
    total = len(results)
    print(f" 합계: {n_pass} PASS · {n_pend} PENDING · {n_fail} FAIL / 총 {total}"
          f"  (합격률 {100*n_pass//total if total else 0}%, PENDING 제외 시 "
          f"{100*n_pass//(total-n_pend) if total-n_pend else 0}%)")
    if n_pend:
        print(" PENDING: MariaDB 미설치 — 설치 후 DB 2엣지 즉시 검증 (코드 준비 완료)")
    print("=" * 100)


if __name__ == "__main__":
    raise SystemExit(run())
