#!/usr/bin/env bash
# 노트북/AI 서버에서 실행 — 추종 인지 서버(perception_server)를 띄우고, 추종 명령을
# 지정한 로봇으로 내려보낸다(--drive-host). 로봇 IP 는 .env 의 PINKY{N}_IP 에서 찾는다.
#
#   ./ai_follower_service.sh pinky3               # cmd_vel 을 pinky3 로
#   ./ai_follower_service.sh pinky3 --test-pattern  # 로봇 영상 없이 확인
#
# 로봇에서 image-sender.sh(영상) 와 follow-drive.sh(cmd_bridge) 가 함께 떠 있어야 실제로 움직인다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

resolve_pinky "${1:?사용법: ./ai_follower_service.sh <pinky1|pinky2|pinky3> [ai-server 인자...]}"
[ -n "$ROBOT_IP" ] || die "$ROBOT_ID 의 IP 가 .env 에 없습니다 ($(echo "$ROBOT_ID" | tr '[:lower:]' '[:upper:]')_IP=... 를 채우세요)."

cd "$REPO_ROOT"
# ai-server.sh 가 perception_server 기동·의존성 체크·주행 경고를 담당한다.
exec "$REPO_ROOT/aba_ai_service/scripts/ai-server.sh" --drive-host "$ROBOT_IP" "${@:2}"
