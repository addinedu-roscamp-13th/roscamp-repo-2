#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=172
python3 /home/Aiprj/gazebo_fastapi/src/obstacle_avoid.py "$@"
