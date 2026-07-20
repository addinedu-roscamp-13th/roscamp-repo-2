#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — 카메라 영상을 UDP 로 AI 서버에 보낸다(추종 영상 경로).
# AI 서버 IP 는 인자로 주거나, 안 주면 .env 의 LAPTOP_IP 를 쓴다.
#
#   ./image-sender.sh            # .env 의 LAPTOP_IP 로
#   ./image-sender.sh 172.30.1.10
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

AI_IP="${1:-${LAPTOP_IP:-}}"
[ -n "$AI_IP" ] || die "AI 서버 IP 가 필요합니다 — 인자로 주거나 .env 에 LAPTOP_IP 를 설정하세요."

cd "$REPO_ROOT"
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/image-sender.sh" "$AI_IP"
