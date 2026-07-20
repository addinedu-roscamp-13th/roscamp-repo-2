#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — pi.sh 가 띄운 주행 스택(tmux 세션 pinky_pi + 고아 노드) 정리.
# 기존 ros_ws/scripts/kill.sh 에 위임한다(그게 pinky_pi·pinky_sim 세션과 launch 자식
# 노드까지 전담). 이름은 pi/ 지만 sim 세션도 같이 지우므로 노트북에서 sim 정리에도 쓸 수 있다.
#
#   ./kill.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
