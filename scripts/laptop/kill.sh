#!/usr/bin/env bash
# 노트북 쪽 정리 — 관제(aba_fms_service) 백엔드·프론트엔드는 건드리지 않는다.
#   1) tmux 세션 libi_fms (fms_service.sh — fleet_node · 브릿지 · 어댑터)
#   2) 나머지(sim 세션 pinky_sim*, domain_bridge, launch 고아 노드)는 기존 kill.sh 에 위임
#
#   ./kill.sh                  정리
#
# 관제 백엔드(:9001)/프론트(:9002) 중지는 aba_fms_service/backend/stop.sh 와
# 프론트 쪽에서 따로 한다 — 이 스크립트는 관여하지 않는다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

if tmux has-session -t libi_fms 2>/dev/null; then
  tmux kill-session -t libi_fms
  echo "killed tmux session: libi_fms"
fi

# sim 세션·domain_bridge·ROS 고아 노드 정리 (domain_bridge 패턴 포함).
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
