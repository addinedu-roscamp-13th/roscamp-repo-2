#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PID_FILE="$SCRIPT_DIR/.pid"
LOG_FILE="$SCRIPT_DIR/robot_agent.log"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "이미 실행 중입니다. (PID: $(cat "$PID_FILE"))"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "가상환경 생성 중..."
    if ! python3 -m venv "$VENV_DIR" --system-site-packages 2>/dev/null; then
        echo "python3-venv 설치 중..."
        sudo apt install -y python3.12-venv
        python3 -m venv "$VENV_DIR" --system-site-packages
    fi
    source "$VENV_DIR/bin/activate"
    echo "의존성 설치 중..."
    pip install -q -r "$SCRIPT_DIR/requirements-driving.txt"
else
    source "$VENV_DIR/bin/activate"
fi

echo "robot_agent 시작..."
cd "$SCRIPT_DIR"
export PYTHONUNBUFFERED=1
# ROS2 환경 source (비대화형 셸은 .bashrc 미로드 → rclpy 못 찾음)
source /opt/ros/jazzy/setup.bash
source /home/pinky/pinky_pro/install/setup.bash
nohup python3 main.py > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2

PORT=$(grep -E '^PORT=' "$SCRIPT_DIR/.env" | cut -d= -f2)
PORT=${PORT:-9001}

echo ""
echo "=============================="
echo " robot_agent 실행됨"
echo " PID  : $!"
echo " 로그  : $LOG_FILE"
echo "------------------------------"
hostname -I | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | while read -r IP; do
    echo " http://$IP:$PORT"
done
echo "=============================="
