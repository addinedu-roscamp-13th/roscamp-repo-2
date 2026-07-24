#!/bin/bash
# PinkyPro 백엔드 시작 스크립트 (가상환경 자동 생성 포함)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 루트 .env 를 실어 준다. 이걸 안 하면 **기능이 조용히 꺼진 채로 뜬다** —
# 특히 LIBI_REAL_DISPATCH 가 없으면 orchestrator 가 fleet_node 로 배차를 넘기지 않아
# 주문은 EXECUTING 인데 로봇이 안 움직인다(로그에도 아무것도 안 남는다).
# scripts/laptop/fms_service.sh 로 띄우면 _common.sh 가 해 주지만, 이 스크립트를
# 직접 실행하는 경우(재기동·디버깅)가 잦아서 여기서도 챙긴다. 규칙은 한 파일에만 있다.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_ROOT/scripts/_load_env.sh"

VENV="$SCRIPT_DIR/.venv"
LOG="/tmp/pinky_api.log"
PID_FILE="/tmp/pinky_api.pid"
PORT=9001

# ── 가상환경 확인 / 생성 ───────────────────────────────────────────────────────
if [ ! -f "$VENV/bin/uvicorn" ]; then
    echo "[setup] 가상환경 생성 중..."
    python3 -m venv "$VENV"
    echo "[setup] 패키지 설치 중..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r requirements.txt
    echo "[setup] 완료"
fi

# ── 이미 실행 중인지 확인 ─────────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[info] 백엔드가 이미 실행 중입니다 (PID: $OLD_PID, 포트: $PORT)"
        echo "[info] 로그: tail -f $LOG"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# ── 백그라운드 시작 ───────────────────────────────────────────────────────────
echo "[start] PinkyPro 백엔드 서버 시작 (포트: $PORT)..."
if [ -f /opt/ros/jazzy/setup.bash ]; then
    source /opt/ros/jazzy/setup.bash
fi
# fleet_ws overlay — 없으면 app/fleet_link.py 가 `libi_fleet_msgs` 를 import 못 해
# "fleet_link 비활성" 으로 떨어지고, 그러면 로봇 관측도 submit_task 도 전부 죽는다
# (/api/fleet/snapshot 이 linked=false, robots=[] 로 보인다).
FLEET_WS_SETUP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/fleet_ws/install/setup.bash"
if [ -f "$FLEET_WS_SETUP" ]; then
    source "$FLEET_WS_SETUP"
else
    echo "[start] ⚠️ fleet_ws 미빌드 — fleet 링크 없이 뜹니다: $FLEET_WS_SETUP"
fi
nohup "$VENV/bin/uvicorn" main:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# 1초 대기 후 정상 기동 확인
sleep 1
if kill -0 "$PID" 2>/dev/null; then
    echo "[ok] 서버 시작됨 (PID: $PID)"
    echo "[ok] API:  http://$(hostname -I | awk '{print $1}'):$PORT"
    echo "[ok] Docs: http://$(hostname -I | awk '{print $1}'):$PORT/docs"
    echo "[log] 로그 확인: tail -f $LOG"
else
    echo "[error] 서버 기동 실패 — 로그 확인: cat $LOG"
    cat "$LOG" | tail -20
    rm -f "$PID_FILE"
    exit 1
fi
