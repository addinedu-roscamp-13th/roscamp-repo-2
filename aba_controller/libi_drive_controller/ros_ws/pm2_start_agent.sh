#!/usr/bin/env bash
# 터미널 3 — robot_agent (온보드 FastAPI :9001, rclpy 노드 포함)
set -e
source /home/robotPrj/controller/drive/ros_ws/ros_source.sh
cd /home/robotPrj/controller/drive/robot_agent
export PYTHONUNBUFFERED=1
exec .venv/bin/python main.py
