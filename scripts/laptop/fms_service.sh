#!/usr/bin/env bash
# 노트북/서버에서 실행 — 관제(aba_fms_service) 백엔드·프론트엔드는 빼고,
# 로봇 운영에 필요한 ROS 쪽만 띄운다.
#   - 도메인 브릿지  : 로봇 도메인 ↔ 86 (DB rc_robots 기준, 로봇마다 하나)
#   - fleet_node     : 배차·교통 (도메인 86)
#   - 상태 어댑터    : 로봇 위치 → fleet_node (DB 로봇 전부)
#
# 관제 백엔드(:9001)/프론트(:9002)는 aba_fms_service/backend/start.sh · frontend
# 쪽에서 따로 띄운다(이 스크립트는 관여하지 않는다).
#
#   ./fms_service.sh
#
# tmux 세션 'libi_fms' — 창: bridge / fleet-node / adapters
# 분리: Ctrl+b d  ·  종료: ./kill.sh
set -eo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FMS="$REPO_ROOT/aba_fms_service"
FLEET_WS="$FMS/fleet_ws"
# arte2 맵의 waypoint.yaml 에서 생성한 navgraph(정점 41). 정점 이름이 주문의
# pickup/dropoff 와 같아야 fleet_node 가 목적지를 찾는다.
# 재생성: .venv/bin/python scripts/gen_arte2_navgraph.py
# (구 new_map.navgraph.yaml 은 정점 8개짜리 포팅 placeholder 였다 — 실제 지점이 없다)
NAVGRAPH="$FLEET_WS/maps/library/arte2.navgraph.yaml"
# 도착 판정 반경(m) — arte2 는 1.26m × 2.16m 축소 맵이라 기본값 0.35 를 쓰면 안 된다.
# 0.35 는 맵 가로폭의 28% 라, 로봇이 가만히 있어도 다음 정점이 반경 안에 들어와
# fleet_node 가 0.15초마다 "도착" 처리하며 경로를 훑고 나간다. 그러면 path_request_driver
# 가 매번 nav2 목표를 갈아치워 status=6(ABORTED) → **출발하자마자 멈춘다.**
#   하한 nav2 xy_goal_tolerance = 0.05 / 상한 최소 레인 길이 = 0.062 (v4 유아 ↔ v13 복도-5)
ARRIVE_RADIUS="${ARRIVE_RADIUS:-0.05}"
SESSION="libi_fms"

need_cmd tmux "sudo apt install -y tmux"
tmux has-session -t "$SESSION" 2>/dev/null && \
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."

cd "$REPO_ROOT"
tmux new-session -d -s "$SESSION" -n bridge \
  bash -c "cd '$FMS' && echo '[bridge] 로봇 도메인 <-> 86 (DB rc_robots 기준)...' && ./scripts/ros-domain-bridge.sh; exec bash"

# fleet_node(배차·교통) — 백엔드와 같은 도메인 86 에서 돈다(fleet_link 가 같은 도메인 전제).
# fleet_ws 안 빌드면 colcon build. RMW/CycloneDDS 는 ~/.bashrc 설정을 따른다(bridge 와 동일).
if [ -f "$FLEET_WS/install/setup.bash" ] || ensure_built "$FLEET_WS"; then
  tmux new-window -t "$SESSION" -n fleet-node \
    bash -c "source /opt/ros/jazzy/setup.bash && source '$FLEET_WS/install/setup.bash' && export ROS_DOMAIN_ID=86 && echo '[fleet-node] 배차·교통 (domain 86)...' && ros2 run libi_fleet fleet_node --ros-args -p navgraph_file:='$NAVGRAPH' -p arrive_radius:=$ARRIVE_RADIUS; exec bash"
fi

# 로봇 상태 어댑터 — 도메인 86 에서 돈다(fleet_node·브릿지와 같은 자리라 여기 둔다).
#
# fleet_node 는 `/robot_state`(rmf_fleet_msgs/RobotState)로 로봇을 인식하는데, 로봇도 sim 도
# 그 타입을 발행하지 않는다(amcl_pose·battery 만 낸다). 이 어댑터가 브릿지로 넘어온
# `/<key>/amcl_pose` 를 읽어 `/robot_state` 로 재발행한다 — 없으면 **로봇 0대**로 보인다.
#
# DB(rc_robots)에 등록된 로봇마다 하나씩 띄운다. 브릿지와 같은 목록을 쓰므로 따로 맞출 게 없다.
# ⚠️ 반대 방향(fleet_node 경로 → nav2)은 **로봇 쪽**에서 돈다:
#      실물 → drive-pi/pi.sh 가 함께 띄운다
#      sim  → sim.sh 가 함께 띄운다
if [ -f "$FLEET_WS/install/setup.bash" ]; then
  tmux new-window -t "$SESSION" -n adapters \
    bash -c "cd '$REPO_ROOT' && ./scripts/laptop/robot-link.sh --all --foreground; exec bash"
fi

tmux select-window -t "$SESSION:bridge"
tmux_attach "$SESSION"
