#!/usr/bin/env bash
# 노트북에서 실행 — Gazebo 시뮬레이션 스택(gazebo·nav2·rviz·bridge·fleet-link·fsm)을
# tmux 로 띄운다. 로봇 없이 전체 파이프라인을 검증할 때 쓴다. 기존 ros_ws/scripts/sim.sh
# 에 위임하되, 두 ROS 워크스페이스가 안 빌드돼 있으면 colcon build 한다.
#
#   ./sim.sh              # 헤드리스
#   ./sim.sh viewer       # 가제보 GUI 포함
#   ./sim.sh --no-fsm     # fsm 창 없이 (FSM 은 fsm-bt.sh 로 따로)
#
# 정리: ./kill.sh (sim 세션 pinky_sim 까지 함께 지운다)
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

ensure_built "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws"
ensure_built "$REPO_ROOT/aba_controller/libi_modes/ros_ws"

cd "$REPO_ROOT"
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/sim.sh" "$@"
