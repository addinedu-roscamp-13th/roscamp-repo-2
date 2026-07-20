#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — 주행 스택 전체(hw·nav2·fleet-link·fsm·led)를 tmux 로 띄운다.
# 기존 aba_controller/.../ros_ws/scripts/pi.sh 에 위임하되, 두 가지를 자동화한다:
#   - FSM_ROBOT_ID 를 인자로:  ./pi.sh pinky3   (= FSM_ROBOT_ID=pinky3 ...)
#   - 두 ROS 워크스페이스(libi_drive_controller, libi_modes)가 안 빌드돼 있으면 colcon build
# 실행 위치와 무관하다 — 어디서 실행하든 REPO_ROOT 기준으로 동작한다.
#
#   ./pi.sh pinky3                # 전체
#   ./pi.sh pinky3 --no-fsm       # fsm 창 없이 (플래그는 그대로 위임)
#   ./pi.sh pinky3 --no-led       # led 창 없이
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

resolve_pinky "${1:?사용법: ./pi.sh <pinky1|pinky2|pinky3> [--no-fsm] [--no-led]}"

ensure_built "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws"
ensure_built "$REPO_ROOT/aba_controller/libi_modes/ros_ws"

# 기존 pi.sh 는 FSM_ROBOT_ID(env)를 읽고, --no-fsm/--no-led 를 자기 인자에서 파싱한다.
# 그래서 로봇 이름은 env 로 넘기고 나머지 플래그(2번째 인자부터)만 그대로 전달한다.
cd "$REPO_ROOT"
exec env FSM_ROBOT_ID="$ROBOT_ID" \
  "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/pi.sh" "${@:2}"
