#!/usr/bin/env bash
# 터미널 1 — 로봇 하드웨어 (라이다·오도메트리·TF)
set -e
source /home/robotPrj/controller/drive/ros_ws/ros_source.sh
exec ros2 launch pinky_bringup bringup_robot.launch.xml
