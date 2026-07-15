#!/bin/bash
# PinkyPro 백엔드 시작 스크립트 (가상환경 자동 생성 포함)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

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
