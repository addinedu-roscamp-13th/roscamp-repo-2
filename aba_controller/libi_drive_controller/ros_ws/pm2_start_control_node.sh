#!/usr/bin/env bash
set -e
BASE=/home/roscamp-repo-2/aba_controller/libi_drive_controller
source $BASE/ros_ws/ros_source.sh
cd $BASE/robot_control_node
exec $BASE/robot_agent/.venv/bin/python main.py
