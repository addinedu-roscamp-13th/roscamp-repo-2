#!/usr/bin/env bash
# 실물 로봇 하드웨어 + nav2(arte2 맵) + fleet_link(robot_agent/FastAPI 없이 단독)
# + libi_modes 미션 FSM 을 tmux 창 4개로 나눠서 실행. pm2(ecosystem.config.js)를
# 안 쓰고 로컬에서 직접 붙여서 테스트할 때 쓴다.
#
# sim.sh 와 달리 domain_bridge 창이 없다 — 실물에서 브릿지는 로봇이 아니라
# FMS 서버에서 돈다("로봇은 무수정", domain_bridge_pinky*.yaml 주석 참고).
# 창 전환: Ctrl+b 0/1/2/3 (hw/nav2/fleet-link/fsm), 또는 Ctrl+b n(다음 창) / Ctrl+b p(이전 창)
#   ./laptop.sh
#   ./laptop.sh --no-fsm   → fsm 창 없이 (FSM 은 ./fsm-bt.sh 로 따로 띄울 때)
#
# 도메인은 하드코딩하지 않는다 — 이 로봇에 이미 설정된 ROS_DOMAIN_ID(보통
# ros_source.sh나 셸 환경에서 지정됨)를 그대로 쓴다. sim.sh와 달리 실물은
# 도메인이 로봇마다 고정(87/88/89)이라 여기서 바꾸면 안 된다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
ROBOT_AGENT_DIR="$ROS_WS_DIR/../robot_agent"
SESSION="pinky_laptop"
WITH_FSM=true
for arg in "$@"; do
  [ "$arg" = "--no-fsm" ] && WITH_FSM=false
done

MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte2.yaml"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[laptop] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./laptop-kill.sh 로 정리하세요."
  exit 1
fi

# CycloneDDS 사용(설치돼 있을 때만 — 없으면 FastDDS로 자동 폴백). Pi에서 FastDDS
# SHM 전송 스레드(dds.shm)가 CPU를 크게 먹어 CycloneDDS로 교체. 모든 노드가 같은
# RMW 여야 통신되므로 hw/nav2/fleet_link 세 창 모두 이 ROS_SETUP 을 공유한다.
ROS_SETUP="source /opt/ros/jazzy/setup.bash && source '$ROS_WS_DIR/install/setup.bash' && if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; export CYCLONEDDS_URI=file://$ROS_WS_DIR/cyclonedds.xml; fi"

# libi_modes 는 별도 워크스페이스라 그쪽 install 도 겹쳐 source 한다.
# 도메인은 셸에 이미 설정된 로봇 도메인을 그대로 쓴다 (실기는 로봇별 88/89/…).
LIBI_MODES_WS="$ROS_WS_DIR/../../libi_modes/ros_ws"
FSM_ROBOT_ID="${FSM_ROBOT_ID:-pinky1}"
LIBI_MODES_SETUP="$ROS_SETUP && source '$LIBI_MODES_WS/install/setup.bash'"

tmux new-session -d -s "$SESSION" -n hw \
  bash -c "$ROS_SETUP && ros2 launch pinky_bringup bringup_robot.launch.xml; exec bash"

tmux new-window -t "$SESSION" -n nav2 \
  bash -c "$ROS_SETUP && echo '[nav2] 하드웨어(/scan) 대기 중...' && for i in \$(seq 1 30); do ros2 topic list 2>/dev/null | grep -q '/scan' && break; sleep 1; done && ros2 launch pinky_navigation bringup_launch.xml map:='$MAP_PATH'; exec bash"

tmux new-window -t "$SESSION" -n fleet-link \
  bash -c "$ROS_SETUP && cd '$ROBOT_AGENT_DIR' && echo '[fleet-link] robot_agent 없이 fleet_link 단독 실행 (costmap 제외 경량 버전)...' && python3 scripts/run_fleet_link-tunning.py; exec bash"

if [ "$WITH_FSM" = true ]; then
  tmux new-window -t "$SESSION" -n fsm \
    bash -c "$LIBI_MODES_SETUP && echo '[fsm] libi_modes 미션 FSM (robot_id=$FSM_ROBOT_ID)...' && ros2 run libi_modes fsm_node --ros-args -p robot_id:=$FSM_ROBOT_ID; exec bash"
fi

tmux select-window -t "$SESSION:hw"
tmux attach -t "$SESSION"
