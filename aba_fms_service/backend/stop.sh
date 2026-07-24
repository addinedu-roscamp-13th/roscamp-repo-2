#!/bin/bash
# FMS 백엔드 중지 스크립트
#
# PID 파일만 믿으면 안 된다: start.sh 를 두 번 부르거나 PID 파일이 덮이면 이전 프로세스가
# 살아남는데, 그건 :9001 을 못 잡아 겉으론 조용하지만 **domain 86 에 ROS 노드를 계속
# 물고 있다**(fleet_link·fsm_link). 실제로 uvicorn 이 4개까지 쌓여 "지금 응답하는 게
# 어느 백엔드인지" 알 수 없는 상태가 됐다. 그래서 PID 파일 대상 + 남은 것 쓸기를 둘 다 한다.

PID_FILE="/tmp/pinky_api.pid"

# 이 저장소의 백엔드만 고른다 — 다른 프로젝트의 uvicorn 을 죽이지 않게 venv 절대경로로 좁힌다.
BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN="$BACKEND_DIR/.venv/bin/uvicorn main:app"

killed=0

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null && killed=$((killed + 1))
    fi
    rm -f "$PID_FILE"
fi

# 남은 것 쓸기. pkill 은 자기 자신을 제외하고, 부모 셸의 명령줄엔 이 패턴이 없다.
if pkill -f "$PATTERN" 2>/dev/null; then
    killed=$((killed + 1))
fi

# rclpy spin 스레드가 SIGTERM 을 씹는 경우가 있어 잠깐 기다렸다 확인한다.
sleep 2
if pgrep -f "$PATTERN" >/dev/null 2>&1; then
    echo "[stop] SIGTERM 에 안 죽은 프로세스가 있어 강제 종료합니다"
    pkill -9 -f "$PATTERN" 2>/dev/null
    sleep 1
fi

if [ "$killed" -gt 0 ]; then
    echo "[stop] 백엔드 서버 종료 완료"
else
    echo "[info] 실행 중인 백엔드 서버가 없습니다"
fi
