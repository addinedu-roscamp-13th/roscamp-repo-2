#!/bin/bash
# PinkyPro 백엔드 중지 스크립트

PID_FILE="/tmp/pinky_api.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        rm -f "$PID_FILE"
        echo "[stop] 백엔드 서버 종료 완료 (PID: $PID)"
    else
        echo "[info] 서버가 이미 종료되어 있습니다"
        rm -f "$PID_FILE"
    fi
else
    # PID 파일 없으면 프로세스 이름으로 종료 시도
    if pkill -f "uvicorn main:app" 2>/dev/null; then
        echo "[stop] 백엔드 서버 종료 완료"
    else
        echo "[info] 실행 중인 백엔드 서버가 없습니다"
    fi
fi
