#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=172
export TURTLEBOT3_MODEL="${TURTLEBOT3_MODEL:-burger}"

ros2 run turtlebot3_teleop teleop_keyboard
