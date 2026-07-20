#!/usr/bin/env bash
# 실물 로봇 하드웨어 + nav2(arte2 맵) + fleet_link(robot_agent/FastAPI 없이 단독)
# + libi_modes 미션 FSM + 상태별 LED(pinky_led/state_led) 를 tmux 창 5개로 나눠서 실행.
# pm2(ecosystem.config.js)를 안 쓰고 로컬에서 직접 붙여서 테스트할 때 쓴다.
#
# sim.sh 와 달리 domain_bridge 창이 없다 — 실물에서 브릿지는 로봇이 아니라
# FMS 서버에서 돈다("로봇은 무수정", domain_bridge_pinky*.yaml 주석 참고).
# LED 는 실물 전용 — pinkyled 모듈이 rpi_ws281x 를 직접 잡고(root 필요) sim.sh 엔 없다.
# led_server(pinky_led 의 다른 노드)와 동시 실행 금지 — 둘 다 LED 스트립을 단독 점유한다.
# 창 전환: Ctrl+b 0/1/2/3/4 (hw/nav2/fleet-link/fsm/led), 또는 Ctrl+b n(다음 창) / Ctrl+b p(이전 창)
#   ./pi.sh
#   ./pi.sh --no-fsm   → fsm 창 없이 (FSM 은 ./fsm-bt.sh 로 따로 띄울 때)
#   ./pi.sh --no-led   → led 창 없이 (LED 상태 표시 코드 안 쓸 때)
#
# ⚠️ aba_ai_service/follower_perception/pi.sh 와 이름이 같지만 다른 스크립트다. 그쪽은
#    추종용(bringup + 카메라 + cmd_bridge)이고 이건 주행용(bringup + nav2 + FSM + LED)이다.
#    둘 다 같은 bringup 을 띄우므로 동시에 실행할 수 없다.
#
# 도메인은 하드코딩하지 않는다 — 이 로봇에 이미 설정된 ROS_DOMAIN_ID(보통
# ros_source.sh나 셸 환경에서 지정됨)를 그대로 쓴다. sim.sh와 달리 실물은
# 도메인이 로봇마다 고정(87/88/89)이라 여기서 바꾸면 안 된다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
ROBOT_AGENT_DIR="$ROS_WS_DIR/../robot_agent"
SESSION="pinky_pi"
WITH_FSM=true
WITH_LED=true
for arg in "$@"; do
  [ "$arg" = "--no-fsm" ] && WITH_FSM=false
  [ "$arg" = "--no-led" ] && WITH_LED=false
done

MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte2.yaml"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[pi] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."
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

if [ "$WITH_LED" = true ]; then
  tmux new-window -t "$SESSION" -n led \
    bash -c "$ROS_SETUP && echo '[led] 상태별 LED (fsm_state 토픽 구독, root 필요 — pinkyled 가 자동 sudo 재실행)...' && ros2 launch pinky_led state_led.launch.xml; exec bash"
fi

tmux select-window -t "$SESSION:hw"
# 인터랙티브(TTY)일 때만 붙는다. 백그라운드(nohup/cron/ssh)면 세션은 그대로 두고 안내만
# 한다 — 세션·서비스는 위에서 이미 떠 있어 백그라운드에서도 정상 동작한다.
if [ -t 1 ]; then
  tmux attach -t "$SESSION"
else
  echo "[bg] '$SESSION' 세션이 백그라운드로 떴습니다. 붙으려면: tmux attach -t $SESSION"
fi
