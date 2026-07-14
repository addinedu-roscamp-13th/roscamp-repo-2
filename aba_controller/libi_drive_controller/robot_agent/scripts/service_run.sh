#!/bin/bash
# systemd(robot_agent.service)에서 호출하는 포그라운드 런처.
# ROS2 환경을 소싱한 뒤 venv 의 python 으로 main.py 를 exec 한다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# --- ROS2 환경 (driving 타입에 필요) ---
export ROS_DOMAIN_ID=88
source /opt/ros/jazzy/setup.bash
if [ -f /home/pinky/pinky_pro/install/local_setup.bash ]; then
    source /home/pinky/pinky_pro/install/local_setup.bash
fi

# --- venv 활성화 ---
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

cd "$SCRIPT_DIR"
export PYTHONUNBUFFERED=1

exec python3 main.py
