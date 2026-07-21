#!/usr/bin/env bash
# 노트북/서버에서 실행 — FMS 한 벌을 통째로 띄운다.
#   - 백엔드(:9001)  : start.sh 가 데몬으로 (중지: aba_fms_service/backend/stop.sh)
#   - 도메인 브릿지  : 로봇 도메인 ↔ 86 (DB rc_robots 기준, 로봇마다 하나)
#   - fleet_node     : 배차·교통 (도메인 86)
#   - 상태 어댑터    : 로봇 위치 → fleet_node (DB 로봇 전부)
#   - 관제 웹(:9002) : 배차·교통 화면
#
#   ./fms_service.sh              # 전부
#   ./fms_service.sh --no-web     # 웹 없이 (로봇 운영만)
#
# tmux 세션 'libi_fms' — 창: api / bridge / fleet-node / adapters / frontend
# 분리: Ctrl+b d  ·  종료: ./kill.sh
set -eo pipefail

# --no-web 은 _common.sh 로드 전에 소비한다(뒤에서 NO_WEB 로 읽는다).
NO_WEB=0
for _a in "$@"; do [ "$_a" = "--no-web" ] && NO_WEB=1; done

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

# 백엔드는 데몬(:9001, 단일 PID). ui/fms.sh 와 같은 백엔드를 공유하므로, 이미 떠 있으면
# 재사용한다 — 무조건 start.sh 를 부르면 두 번째 기동이 포트 충돌로 죽는다.
if port_open 9001; then
  echo "[api] :9001 이미 떠 있음 — 재사용"
else
  "$FMS/backend/start.sh"
fi

cd "$REPO_ROOT"
tmux new-session -d -s "$SESSION" -n api \
  bash -c "echo '[api] 백엔드는 데몬입니다 (중지: aba_fms_service/backend/stop.sh). 로그:'; tail -n +1 -f /tmp/pinky_api.log; exec bash"
tmux new-window -t "$SESSION" -n bridge \
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

# 관제 웹(:9002) — 백엔드/로봇만 띄우고 화면을 따로 켜는 게 번거로워 여기 함께 둔다.
# ui/fms.sh 와 같은 프론트라 이미 :9002 가 떠 있으면 재사용한다(중복 기동 방지).
#   끄고 싶으면: ./fms_service.sh --no-web
if [ "${NO_WEB:-0}" != "1" ]; then
  if port_open 9002; then
    echo "[frontend] :9002 이미 떠 있음 — 재사용"
  else
    ensure_npm "$FMS/frontend"
    # bun 이 있으면 bun 으로 — 이 저장소 프론트는 bun 기준이고 npm 은 느리다.
    if command -v bun >/dev/null 2>&1; then
      WEB_CMD="bun --bun run dev"
    else
      WEB_CMD="npm run dev"
    fi
    tmux new-window -t "$SESSION" -n frontend \
      bash -c "cd '$FMS/frontend' && echo '[frontend] 관제 콘솔 http://localhost:9002/admin ...' && $WEB_CMD; exec bash"
  fi
fi

tmux select-window -t "$SESSION:bridge"
tmux_attach "$SESSION"
