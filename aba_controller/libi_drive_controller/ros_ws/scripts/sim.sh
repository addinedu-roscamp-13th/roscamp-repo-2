#!/usr/bin/env bash
# Gazebo 시뮬레이션 + nav2 + rviz(gz_nav2_view) + domain_bridge + fleet_link 를
# tmux 창 5개로 나눠서 실행.
# 창 전환: Ctrl+b 0/1/2/3/4/5 (gazebo/nav2/rviz/bridge/fleet-link/fsm), 또는 Ctrl+b n/p
#   ./sim.sh            → 헤드리스(뷰어 없이) + nav2 + rviz + domain_bridge + fleet_link + fsm
#   ./sim.sh viewer     → 가제보 GUI(뷰어) 포함
#   ./sim.sh --no-fsm   → fsm 창 없이 (FSM 은 ./fsm-bt.sh 로 따로 띄울 때)
#
# BT 를 고치면서 반복할 땐 fsm 창에서 Ctrl+C 후 ↑ Enter 로 그 창만 재기동하면 된다
# (gazebo·nav2 는 그대로 살아있다). 아예 따로 굴리려면 --no-fsm + ./fsm-bt.sh.
#
# domain_bridge 창은 sim(현재 셸의 ROS_DOMAIN_ID, 없으면 90) <-> FMS 서버(도메인 86)를 이어준다.
# ros-jazzy-domain-bridge 패키지가 없으면 이 창에 에러만 뜨고 나머지는 정상 진행된다
# (sudo apt install ros-jazzy-domain-bridge 로 설치).
#
# fleet-link 창은 robot_agent(FastAPI) 없이 fleet_link만 단독 실행 — FMS의
# Waypoint "이동" 명령이 fleet_cmd 토픽으로 sim에 도달하려면 반드시 필요하다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
# 한 노트북에서 sim 을 여러 개(다른 이름·도메인) 띄울 수 있게 세션 이름을 바깥에서 줄 수 있다.
# 안 주면 예전과 같은 pinky_sim — kill.sh 는 이 이름을 지운다.
SESSION="${SIM_SESSION:-pinky_sim}"

WORLD_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/worlds/arte2.sdf"
MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte2.yaml"
DOMAIN_BRIDGE_TEMPLATE="$ROS_WS_DIR/../../../aba_fms_service/config/domain_bridge_sim.yaml"
ROBOT_AGENT_DIR="$ROS_WS_DIR/../robot_agent"

# 하드코딩 대신 현재 셸에 이미 설정된 ROS_DOMAIN_ID를 그대로 쓴다(없으면 90 기본값).
SIM_DOMAIN_ID="${ROS_DOMAIN_ID:-90}"

USE_GUI=false
WITH_FSM=true
for arg in "$@"; do
  case "$arg" in
    viewer) USE_GUI=true ;;
    --no-fsm) WITH_FSM=false ;;
  esac
done

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[sim] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."
  exit 1
fi

# domain_bridge_sim.yaml의 from_domain을 현재 SIM_DOMAIN_ID로 바꾼 임시본 생성.
DOMAIN_BRIDGE_CONFIG="$(mktemp /tmp/domain_bridge_sim_XXXX.yaml)"
sed "s/^from_domain: .*/from_domain: $SIM_DOMAIN_ID/" "$DOMAIN_BRIDGE_TEMPLATE" > "$DOMAIN_BRIDGE_CONFIG"

ROS_SETUP="export ROS_DOMAIN_ID=$SIM_DOMAIN_ID && source /opt/ros/jazzy/setup.bash && source '$ROS_WS_DIR/install/setup.bash' && if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; export CYCLONEDDS_URI=file://$ROS_WS_DIR/cyclonedds.xml; fi"

# libi_modes 는 별도 워크스페이스(aba_controller/libi_modes/ros_ws)라 그쪽 install 도 겹쳐 source 한다.
# 도메인은 위와 같다 — FSM 은 로봇과 같은 도메인에서 돌아야 배터리·명령을 그대로 주고받는다.
LIBI_MODES_WS="$ROS_WS_DIR/../../libi_modes/ros_ws"
FSM_ROBOT_ID="${FSM_ROBOT_ID:-pinkySim}"
LIBI_MODES_SETUP="$ROS_SETUP && source '$LIBI_MODES_WS/install/setup.bash'"

tmux new-session -d -s "$SESSION" -n gazebo \
  bash -c "$ROS_SETUP && ros2 launch pinky_gz_sim launch_sim.launch.xml use_gui:=$USE_GUI world:='$WORLD_PATH'; exec bash"

tmux new-window -t "$SESSION" -n nav2 \
  bash -c "$ROS_SETUP && echo '[nav2] 로봇 스폰 대기 중 (/scan 토픽 확인)...' && for i in \$(seq 1 30); do ros2 topic list 2>/dev/null | grep -q '/scan' && break; sleep 1; done && ros2 launch pinky_navigation gz_bringup_launch.xml map:='$MAP_PATH'; exec bash"

tmux new-window -t "$SESSION" -n rviz \
  bash -c "$ROS_SETUP && ros2 launch pinky_navigation gz_nav2_view.launch.xml; exec bash"

tmux new-window -t "$SESSION" -n bridge \
  bash -c "$ROS_SETUP && echo '[bridge] domain $SIM_DOMAIN_ID <-> 86 연결 (FMS 서버)...' && ros2 run domain_bridge domain_bridge '$DOMAIN_BRIDGE_CONFIG'; exec bash"

tmux new-window -t "$SESSION" -n fleet-link \
  bash -c "$ROS_SETUP && cd '$ROBOT_AGENT_DIR' && echo '[fleet-link] robot_agent 없이 fleet_link 단독 실행 (domain $SIM_DOMAIN_ID)...' && python3 scripts/run_fleet_link.py; exec bash"

if [ "$WITH_FSM" = true ]; then
  tmux new-window -t "$SESSION" -n fsm \
    bash -c "$LIBI_MODES_SETUP && echo '[fsm] libi_modes 미션 FSM (domain $SIM_DOMAIN_ID, robot_id=$FSM_ROBOT_ID)...' && ros2 run libi_modes fsm_node --ros-args -p robot_id:=$FSM_ROBOT_ID; exec bash"
fi

# 경로 드라이버 — fleet_node 가 정한 경로를 nav2 로 옮긴다. **로봇 쪽 구성요소**라
# 여기(sim)에 둔다(실물은 drive-pi/pi.sh 가 같은 일을 한다).
#
# 이게 없으면 배차는 성공하는데(로봇에 goal_vertex 가 찍힘) 로봇이 안 움직이고,
# fleet_node 가 "무진행 → task 취소"로 판단해 주문이 FAILED 로 떨어진다.
# 반대 방향(로봇 위치 → fleet_node)은 서버의 fms_service.sh 가 띄운다.
PATH_DRIVER="$ROS_WS_DIR/../scripts/path_request_driver.py"
if [ -f "$PATH_DRIVER" ]; then
  FLEET_WS_SETUP="$ROS_WS_DIR/../../../aba_fms_service/fleet_ws/install/setup.bash"
  DRIVER_SETUP="$ROS_SETUP"
  [ -f "$FLEET_WS_SETUP" ] && DRIVER_SETUP="$DRIVER_SETUP && source '$FLEET_WS_SETUP'"
  tmux new-window -t "$SESSION" -n path-driver \
    bash -c "$DRIVER_SETUP && echo '[path-driver] fleet_node 경로 -> nav2 (domain $SIM_DOMAIN_ID, robot=$FSM_ROBOT_ID)...' && python3 '$PATH_DRIVER' --robot '$FSM_ROBOT_ID'; exec bash"
fi

tmux select-window -t "$SESSION:gazebo"
# 인터랙티브(TTY)일 때만 붙는다. 백그라운드(nohup/cron/ssh)면 세션은 그대로 두고 안내만
# 한다 — 세션·서비스는 위에서 이미 떠 있어 백그라운드에서도 정상 동작한다.
if [ -t 1 ]; then
  tmux attach -t "$SESSION"
else
  echo "[bg] '$SESSION' 세션이 백그라운드로 떴습니다. 붙으려면: tmux attach -t $SESSION"
fi
