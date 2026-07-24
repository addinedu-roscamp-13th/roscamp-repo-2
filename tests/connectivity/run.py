#!/usr/bin/env python3
"""리비 연동 테스트 하네스 — walking-skeleton, read-only 스모크.

인터페이스 정의서의 각 엣지를 대표 케이스 1개로 검증(connectivity/smoke).
실제 로봇 task를 발생시키는 mutating 엣지(이송요청 등)는 다루지 않는다 — 그 흐름은
aba_service/backend/tests/의 pytest가 이미 커버한다. 여기서는 read-only(GET/조회성)
엣지만 찌른다.
산출물: 터미널 pass/fail 매트릭스 + JSON 리포트(report.json).

실행: .venv/bin/python tests/connectivity/run.py
"""
import asyncio
import json
import os
import time
import urllib.error
import urllib.request

SERVER = "http://127.0.0.1:8000"
FMS = "http://127.0.0.1:9001"
REPORT = os.path.join(os.path.dirname(__file__), "report.json")

# 로그인 스모크용 자격증명 — 실제 admin 비밀번호는 SEED_ADMIN_PASSWORD(.env)에서만 알 수 있으므로
# 환경변수로 주입 가능하게 하고, 없으면 401을 FAIL이 아니라 PENDING으로 처리한다.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

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

    # 1 aba_server 헬스체크 (GET, read-only)
    try:
        st, body, ms = _http("GET", f"{SERVER}/api/health")
        ok = st == 200 and body.get("status") == "ok"
        record("1", "aba_server 헬스체크", "harness → aba_server", "HTTP",
               "GET /api/health", "SR-01", "{status: ok}", body, ok, ms)
    except Exception as e:  # noqa: BLE001
        record("1", "aba_server 헬스체크", "harness → aba_server", "HTTP",
               "GET /api/health", "SR-01", "{status: ok}", str(e), False, 0)

    # 2 aba_fms 헬스체크 (GET, read-only)
    try:
        st, body, ms = _http("GET", f"{FMS}/api/health")
        ok = st == 200 and body.get("status") == "ok"
        record("2", "aba_fms 헬스체크", "harness → aba_fms", "HTTP",
               "GET /api/health", "SR-10", "{status: ok}", body, ok, ms)
    except Exception as e:  # noqa: BLE001
        record("2", "aba_fms 헬스체크", "harness → aba_fms", "HTTP",
               "GET /api/health", "SR-10", "{status: ok}", str(e), False, 0)

    # 3 librarian → aba_fms (HTTP, GetRobotState — read-only)
    try:
        st, body, ms = _http("GET", f"{FMS}/api/control/state")
        ok = st == 200 and "robots" in body
        record("3", "librarian→aba_fms", "librarian → aba_fms", "HTTP",
               "GetRobotState §1", "SR-10", "{robots[...]}", body, ok, ms)
    except Exception as e:  # noqa: BLE001
        record("3", "librarian→aba_fms", "librarian → aba_fms", "HTTP", "GetRobotState §1",
               "SR-10", "{robots}", str(e), False, 0)

    # 4 aba_fms → librarian (WS, 상태 스트림 구독 — read-only)
    try:
        t0 = time.time()
        msg = asyncio.run(_ws_recv(f"ws://127.0.0.1:9001/api/control/ws/state"))
        ms = (time.time() - t0) * 1000
        body = json.loads(msg)
        record("4", "aba_fms→librarian", "aba_fms → librarian", "WS",
               "모니터링 §5", "SR-10", "state msg", body, "robot_id" in body, ms)
    except Exception as e:  # noqa: BLE001
        record("4", "aba_fms→librarian", "aba_fms → librarian", "WS", "모니터링 §5",
               "SR-10", "state msg", str(e), False, 0)

    # 5 aba_service admin 로그인 스모크 (POST, 로그인만 — 아무것도 만들지 않음)
    try:
        st, body, ms = _http("POST", f"{SERVER}/api/admin/auth/login",
                              {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        if st == 200 and "access_token" in body:
            ok, note = True, ""
        elif st == 401:
            ok, note = None, "자격증명 미확인(401) — ADMIN_USERNAME/ADMIN_PASSWORD 환경변수로 실제 계정 지정 시 재검증 가능"
        else:
            ok, note = False, ""
        record("5", "librarian→aba_server", "librarian → aba_server", "HTTP",
               "POST /api/admin/auth/login", "SR-01", "{access_token} or 401", body, ok, ms, note)
    except Exception as e:  # noqa: BLE001
        record("5", "librarian→aba_server", "librarian → aba_server", "HTTP",
               "POST /api/admin/auth/login", "SR-01", "{access_token} or 401", str(e), False, 0)

    print_matrix()
    with open(REPORT, "w") as f:
        json.dump({"ts": time.time(), "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n[harness] JSON 리포트: {REPORT}")

    n_fail = sum(r["result"] == "FAIL" for r in results)
    return n_fail


def print_matrix():
    ico = {"PASS": "✅", "FAIL": "❌", "PENDING": "⏳"}
    print("=" * 100)
    print(" 리비 연동시험 결과서 (connectivity / smoke, read-only)")
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
        print(" PENDING: 사유는 각 행의 note 참고")
    print("=" * 100)


if __name__ == "__main__":
    raise SystemExit(run())
