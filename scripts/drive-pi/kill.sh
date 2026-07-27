#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — pi.sh 가 띄운 주행 스택(tmux 세션 pinky_pi + 고아 노드) 정리.
#   1) image-sender.sh / follow-drive.sh 가 띄운 프로세스 (여기서 직접)
#   2) 나머지(pinky_pi·pinky_sim 세션, launch 자식 노드)는 ros_ws/scripts/kill.sh 에 위임
# 이름은 drive-pi/ 지만 sim 세션도 같이 지우므로 노트북에서 sim 정리에도 쓸 수 있다.
#
#   ./kill.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

# ⚠️ 이 둘은 tmux 없이 `exec` 로 포그라운드에 뜬다 — **세션 정리로는 안 잡힌다.**
# Ctrl+C 없이 창만 닫으면 살아남아, camera_sender 는 AI 서버로 영상을 계속 쏘고
# cmd_bridge 는 UDP:6002 주행 명령을 계속 받는다(추종을 껐는데 로봇이 움직인다).
# 위임 대상인 ros_ws/scripts/kill.sh 는 aba_controller 소유라 여기서 덮는다.
#   camera_sender.py : image-sender.sh  (영상 → AI 서버 UDP:6001)
#   cmd_bridge.py    : follow-drive.sh  (추종 cmd_vel 수신 UDP:6002)
kill_patterns "camera_sender.py" "cmd_bridge.py"

exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
