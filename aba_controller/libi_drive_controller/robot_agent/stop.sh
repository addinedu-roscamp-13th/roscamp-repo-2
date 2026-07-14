#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "PID 파일이 없습니다. 실행 중이 아닐 수 있습니다."
    exit 1
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm -f "$PID_FILE"
    echo "robot_agent 종료됨 (PID: $PID)"
else
    echo "프로세스가 이미 종료되어 있습니다."
    rm -f "$PID_FILE"
fi
