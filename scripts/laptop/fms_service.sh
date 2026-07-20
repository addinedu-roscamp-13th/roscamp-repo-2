#!/usr/bin/env bash
# 노트북/서버에서 실행 — FMS 백엔드 + 도메인 브릿지를 띄운다.
#   - 백엔드(:9001)는 start.sh 가 데몬으로 띄운다(중지: aba_fms_service/backend/stop.sh).
#   - 도메인 브릿지(로봇 도메인 ↔ 86)는 tmux 창에서 포그라운드로 돈다.
#
#   ./fms_service.sh
#
# tmux 세션 'libi_fms' — 창0=api(로그 tail), 창1=bridge. 분리: Ctrl+b d / 종료: ./kill.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FMS="$REPO_ROOT/aba_fms_service"
FLEET_WS="$FMS/fleet_ws"
NAVGRAPH="$FLEET_WS/maps/library/new_map.navgraph.yaml"   # ⚠️ arte2 정합 필요(#27)
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
    bash -c "source /opt/ros/jazzy/setup.bash && source '$FLEET_WS/install/setup.bash' && export ROS_DOMAIN_ID=86 && echo '[fleet-node] 배차·교통 (domain 86)...' && ros2 run libi_fleet fleet_node --ros-args -p navgraph_file:='$NAVGRAPH'; exec bash"
fi

tmux select-window -t "$SESSION:bridge"
tmux_attach "$SESSION"
