#!/usr/bin/env bash
set -e
BASE=/home/roscamp-repo-2/aba_controller/libi_drive_controller
source "$BASE/ros_ws/ros_source.sh"
exec ros2 launch pinky_navigation bringup_launch.xml map:=/home/pinky/pinky_pro/src/pinky_pro/pinky_navigation/map/arte3.yaml
