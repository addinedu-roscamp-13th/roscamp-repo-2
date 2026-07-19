#!/usr/bin/env bash
# 실물 로봇 하드웨어 + nav2(arte2 맵) + fleet_link(robot_agent/FastAPI 없이 단독)를
# tmux 창 3개로 나눠서 실행. pm2(ecosystem.config.js)를 안 쓰고 로컬에서 직접
# 붙여서 테스트할 때 쓴다.
# 창 전환: Ctrl+b 0/1/2 (hw/nav2/fleet-link), 또는 Ctrl+b n(다음 창) / Ctrl+b p(이전 창)
#   ./laptop.sh
#
# 도메인은 하드코딩하지 않는다 — 이 로봇에 이미 설정된 ROS_DOMAIN_ID(보통
# ros_source.sh나 셸 환경에서 지정됨)를 그대로 쓴다. sim.sh와 달리 실물은
# 도메인이 로봇마다 고정(87/88/89)이라 여기서 바꾸면 안 된다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
ROBOT_AGENT_DIR="$ROS_WS_DIR/../robot_agent"
SESSION="pinky_laptop"

MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte2.yaml"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[laptop] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./laptop-kill.sh 로 정리하세요."
  exit 1
fi

ROS_SETUP="source /opt/ros/jazzy/setup.bash && source '$ROS_WS_DIR/install/setup.bash'"

tmux new-session -d -s "$SESSION" -n hw \
  bash -c "$ROS_SETUP && ros2 launch pinky_bringup bringup_robot.launch.xml; exec bash"

tmux new-window -t "$SESSION" -n nav2 \
  bash -c "$ROS_SETUP && echo '[nav2] 하드웨어(/scan) 대기 중...' && for i in \$(seq 1 30); do ros2 topic list 2>/dev/null | grep -q '/scan' && break; sleep 1; done && ros2 launch pinky_navigation bringup_launch.xml map:='$MAP_PATH'; exec bash"

tmux new-window -t "$SESSION" -n fleet-link \
  bash -c "$ROS_SETUP && cd '$ROBOT_AGENT_DIR' && echo '[fleet-link] robot_agent 없이 fleet_link 단독 실행...' && python3 scripts/run_fleet_link.py; exec bash"

tmux select-window -t "$SESSION:hw"
tmux attach -t "$SESSION"
