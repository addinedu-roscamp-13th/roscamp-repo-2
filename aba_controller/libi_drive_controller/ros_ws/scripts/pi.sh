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
# ⚠️ **기본값을 두지 않는다.** 예전엔 `pinky1` 이 기본이라 인자 없이 이 스크립트를 직접
# 실행해도 그냥 떴는데, 어느 로봇도 그 이름이 아니라서 조용히 어긋났다:
#   fsm_node 가 robot_id=pinky1 로 발행 → fleet_node 는 DB 이름(Pinky-3)으로 조회 → 안 붙음
#   (관제에 "상태 미상", 배차 가능 0대 / path-driver 는 경로를 전부 무시)
# 이름은 DB `rc_robots.name` 과 정확히 같아야 한다. 보통 scripts/drive-pi/pi.sh 가 넣어 준다.
if [ -z "${FSM_ROBOT_ID:-}" ]; then
  echo "[pi] ❌ FSM_ROBOT_ID 가 없습니다 — 로봇 이름 없이는 띄우지 않습니다." >&2
  echo "     scripts/drive-pi/pi.sh --robot <DB이름>  으로 실행하세요 (예: --robot Pinky-3)." >&2
  echo "     직접 부를 때는:  FSM_ROBOT_ID=Pinky-3 $0" >&2
  exit 1
fi
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

# 주행 경로는 이제 **BT 를 지난다** — path_request_driver 창은 일부러 없다.
#
#   FMS  fleet_node → /robot_path_requests → fleet_dispatch_bridge
#        → /fleet_cmd{navigate} → libi_modes  WorkingBranch ▸ NavigationExec
#        → /fleet_cmd{goal} → robot_agent fleet_link → nav2
#
# 예전엔 path_request_driver.py 가 /robot_path_requests 를 받아 nav2 로 **직접** 몰았다.
# 그래서 로봇 FSM 이 배달 중에도 IDLE/PATROL 로 남았고, 관제가 배달 중인 로봇을
# "배차 가능"으로 표시했다. 둘을 같이 띄우면 같은 주행이 두 갈래로 나간다.
# 되돌려야 하면 서버 .env 의 LIBI_NAV_VIA_BT=0 으로 두고 그 파일을 손으로 띄운다.

# dock-confirm 창은 여기서 뺐다 (scripts/dock_confirm.py 는 그대로 남아 있다).
#
# ⚠️ 되살릴 때 알아야 할 것: 이 창이 위치를 보고 /is_docked 를 내던 자리다. 아무도
#    /is_docked 를 내지 않으면 RETURNING 이 끝나지 않아 로봇이 부팅 상태에서 못
#    나가고 배차 대상(IDLE/PATROL)이 되지 못한다. 그 신호를 다른 곳에서 내고 있는지
#    확인하고 띄울 것:
#      python3 aba_controller/libi_drive_controller/scripts/dock_confirm.py \
#        --navgraph aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml
#    원래 자리는 정밀 주차(테이프 추종) 폐루프였다 — scripts/drive-pi/dock/README.md 미결 1~4.

tmux select-window -t "$SESSION:hw"
# 인터랙티브(TTY)일 때만 붙는다. 백그라운드(nohup/cron/ssh)면 세션은 그대로 두고 안내만
# 한다 — 세션·서비스는 위에서 이미 떠 있어 백그라운드에서도 정상 동작한다.
if [ -t 1 ]; then
  tmux attach -t "$SESSION"
else
  echo "[bg] '$SESSION' 세션이 백그라운드로 떴습니다. 붙으려면: tmux attach -t $SESSION"
fi
