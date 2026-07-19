#!/usr/bin/env bash
# Gazebo 시뮬레이션 + nav2 + rviz(gz_nav2_view) 를 tmux 창 3개로 나눠서 실행.
# 창 전환: Ctrl+b 0/1/2 (gazebo/nav2/rviz), 또는 Ctrl+b n(다음 창) / Ctrl+b p(이전 창)
#   ./sim.sh          → 헤드리스(뷰어 없이) + nav2 + rviz
#   ./sim.sh viewer   → 가제보 GUI(뷰어) + nav2 + rviz
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
SESSION="pinky_sim"

WORLD_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/worlds/arte2.sdf"
MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte2.yaml"

USE_GUI=false
if [ "$1" = "viewer" ]; then
  USE_GUI=true
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[sim] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."
  exit 1
fi

ROS_SETUP="source /opt/ros/jazzy/setup.bash && source '$ROS_WS_DIR/install/setup.bash'"

tmux new-session -d -s "$SESSION" -n gazebo \
  bash -c "$ROS_SETUP && ros2 launch pinky_gz_sim launch_sim.launch.xml use_gui:=$USE_GUI world:='$WORLD_PATH'; exec bash"

tmux new-window -t "$SESSION" -n nav2 \
  bash -c "$ROS_SETUP && echo '[nav2] 로봇 스폰 대기 중 (/scan 토픽 확인)...' && for i in \$(seq 1 30); do ros2 topic list 2>/dev/null | grep -q '/scan' && break; sleep 1; done && ros2 launch pinky_navigation gz_bringup_launch.xml map:='$MAP_PATH'; exec bash"

tmux new-window -t "$SESSION" -n rviz \
  bash -c "$ROS_SETUP && ros2 launch pinky_navigation gz_nav2_view.launch.xml; exec bash"

tmux select-window -t "$SESSION:gazebo"
tmux attach -t "$SESSION"
