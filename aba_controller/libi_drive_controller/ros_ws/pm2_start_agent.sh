#!/usr/bin/env bash
set -e
BASE=/home/roscamp-repo-2/aba_controller/libi_drive_controller
AGENT="$BASE/robot_agent"
source "$BASE/ros_ws/ros_source.sh"
cd "$AGENT"
exec .venv/bin/python main.py
