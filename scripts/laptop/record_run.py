#!/usr/bin/env python3
"""주문 하나가 도는 동안 **세 계층을 같은 시계로** 기록한다. 노트북/서버에서 실행.

## 왜 필요한가

시연이나 발표에서 "UI 에서 주문 → FMS 가 받음 → 로봇이 움직이며 상태가 바뀜"을 보여줘야
하는데, 그 셋이 서로 다른 곳에 흩어져 있다:

    주문·사건   FMS  GET /api/fleet/events      seq + ts
    로봇 상태   FMS  GET /api/fleet/snapshot    state + x,y
    FMS 내부    파일 /tmp/pinky_api.log         HH:MM:SS

화면 녹화가 안 되는 환경(Wayland + 포털 권한)에서도, **하나의 시각 축에 정렬된 표**가
있으면 무슨 일이 언제 일어났는지 그대로 보여줄 수 있다. 궤적 그림(render_run.py)도
여기서 나온 위치 기록을 쓴다.

## 기록하는 것

한 줄에 하나씩 JSONL 로 남긴다. 시각은 전부 **이 프로세스의 벽시계**로 통일한다 —
FMS 이벤트의 `ts` 와 로봇 ROS 시각이 서로 다른 기준이라, 그대로 섞으면 순서가 어긋난다.

    {"t": 3.42, "kind": "pose",  "robot": "Pinkysim", "state": "WORKING", "x": .., "y": ..}
    {"t": 3.51, "kind": "event", "seq": 7, "text": "복도-5 도착", ...}

## 실행

    .venv/bin/python scripts/laptop/record_run.py --out /tmp/run1.jsonl
    .venv/bin/python scripts/laptop/record_run.py --out /tmp/run1.jsonl --seconds 240
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

#: 위치를 얼마나 자주 찍을지(초). 로봇이 0.07 m/s 로 가므로 이 정도면 궤적이 매끄럽다.
POLL_SEC = 1.0
#: 한 번의 HTTP 호출 상한(초). 넘으면 그 회차만 건너뛴다 — 기록이 멈추면 안 된다.
HTTP_TIMEOUT = 4.0


def _req(url: str, token: str = "", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as res:
        return json.loads(res.read().decode())


def login(base: str, user: str, password: str) -> str:
    return _req(f"{base}/api/auth/login", body={"username": user, "password": password})["access_token"]


def record(base: str, token: str, out_path: str, seconds: float, stop_on_done: bool) -> str:
    """끝날 때까지 폴링하며 JSONL 을 쓴다. 반환값은 파일 경로."""
    started = time.time()
    seen_seq = 0
    last_state: dict[str, str] = {}
    done = False

    with open(out_path, "w") as f:
        def emit(kind: str, **fields):
            f.write(json.dumps({"t": round(time.time() - started, 2), "kind": kind, **fields},
                               ensure_ascii=False) + "\n")
            f.flush()          # 중간에 끊겨도 거기까지는 남는다

        emit("start", wall=time.strftime("%H:%M:%S"))
        while time.time() - started < seconds and not done:
            # ── 로봇 위치·상태 ────────────────────────────────────────────
            try:
                snap = _req(f"{base}/api/fleet/snapshot", token)["snapshot"]
                for r in snap.get("robots", []):
                    name = r.get("name", "?")
                    emit("pose", robot=name, state=r.get("state"),
                         x=round(float(r.get("x", 0.0)), 4), y=round(float(r.get("y", 0.0)), 4))
                    # 상태가 바뀐 순간을 따로 남긴다 — 표에서 이 줄만 뽑으면 전이 이력이 된다.
                    if last_state.get(name) != r.get("state"):
                        if name in last_state:
                            emit("state", robot=name, frm=last_state[name], to=r.get("state"))
                        last_state[name] = r.get("state")
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
                pass

            # ── 주문 사건 (도착 알림) ─────────────────────────────────────
            try:
                data = _req(f"{base}/api/fleet/events?since={seen_seq}", token)
                for e in data.get("events", []):
                    emit("event", **{k: e[k] for k in
                                     ("seq", "kind", "text", "task_id", "leg_idx", "leg_count")
                                     if k in e})
                    seen_seq = max(seen_seq, int(e.get("seq", 0)))
                    if stop_on_done and e.get("kind") in ("task_done", "task_failed"):
                        done = True
            except (urllib.error.URLError, TimeoutError, ValueError):
                pass

            time.sleep(POLL_SEC)

        emit("end", wall=time.strftime("%H:%M:%S"))
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:9001")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin1234")
    ap.add_argument("--out", default="/tmp/run.jsonl")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--until-done", action="store_true",
                    help="task_done/task_failed 사건이 오면 즉시 종료")
    args = ap.parse_args()

    token = login(args.base, args.user, args.password)
    print(f"[record] 기록 시작 → {args.out} (최대 {args.seconds:.0f}초)", flush=True)
    path = record(args.base, token, args.out, args.seconds, args.until_done)
    lines = sum(1 for _ in open(path))
    print(f"[record] 끝 — {lines}줄")


if __name__ == "__main__":
    main()
