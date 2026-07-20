#!/usr/bin/env bash
# 노트북 쪽 정리 — FMS 세션(libi_fms) + sim/브릿지/ROS 스택을 함께 지운다.
#   1) tmux 세션 libi_fms (fms_service.sh)
#   2) 나머지(sim 세션 pinky_sim, domain_bridge, launch 고아 노드)는 기존 kill.sh 에 위임
#
# 백엔드(:9001)는 데몬이라 여기서 안 건드린다 — 중지: aba_fms_service/backend/stop.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

if tmux has-session -t libi_fms 2>/dev/null; then
  tmux kill-session -t libi_fms
  echo "killed tmux session: libi_fms"
fi

echo "[kill] 백엔드 데몬은 유지됩니다 — 중지하려면: aba_fms_service/backend/stop.sh"
# sim 세션·domain_bridge·ROS 고아 노드 정리 (domain_bridge 패턴 포함).
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
