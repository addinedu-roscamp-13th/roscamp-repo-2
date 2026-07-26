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

# 상태 어댑터를 **명시적으로** 정리한다.
#
# robot-link.sh 는 더 이상 시그널 트랩으로 자동 정지하지 않는다(2026-07-26, 의도된 변경 —
# 창이 닫혔다고 어댑터가 죽으면 sim 의 로봇 인식까지 함께 죽는다). 그래서 이 스크립트가
# 어댑터 정리의 명시적 주체다.
#
# ⚠️ tmux 세션을 죽인 **뒤에** 부른다. --foreground 워치독을 먼저 없애면
#    pid 파일 삭제와 워치독의 존재 검사 사이 경쟁이 원천적으로 사라진다.
#
# 아래 ros_ws/kill.sh 의 `pkill -f "robot_state_adapter.py"` 는 2차 그물이다 —
# 프로세스는 그것도 죽이지만, **pid 파일 정리와 신원 검증은 여기서만 한다.**
"$REPO_ROOT/scripts/laptop/robot-link.sh" --all --stop || true

# sim 세션·domain_bridge·ROS 고아 노드 정리 (domain_bridge 패턴 포함).
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
