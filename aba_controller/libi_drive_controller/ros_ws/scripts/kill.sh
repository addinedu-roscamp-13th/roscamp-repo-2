#!/usr/bin/env bash
# sim.sh 로 띄운 tmux 세션(가제보/nav2/rviz/domain_bridge/fleet_link) 및 관련 프로세스를 전부 종료.
set -u

SESSION="pinky_sim"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
  echo "killed tmux session: $SESSION"
fi

PATTERNS=(
  "ros2 launch pinky_gz_sim"
  "ros2 launch pinky_navigation gz_bringup_launch"
  "ros2 launch pinky_navigation gz_nav2_view"
  "gz sim"
  "parameter_bridge"
  "ros_gz_image"
  "ros_gz_sim create"
  "rviz2"
  "component_container_isolated"
  "domain_bridge"
  "run_fleet_link.py"
)

for pattern in "${PATTERNS[@]}"; do
  if pkill -f "$pattern" 2>/dev/null; then
    echo "killed: $pattern"
  fi
done

exit 0
