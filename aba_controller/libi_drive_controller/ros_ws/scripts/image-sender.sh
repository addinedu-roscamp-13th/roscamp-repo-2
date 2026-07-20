#!/usr/bin/env bash
# 로봇(Pi) 카메라 -> UDP 로 AI 서버에 영상만 전송. 추종 전체(추적/명령)가 아니라
# "영상이 AI 서버까지 도착하는가"만 먼저 검증할 때 쓴다 — 원본은
# aba_ai_service/follower_perception/scripts/camera_sender.py, 여기 scripts/ 는 다른
# Pi 런처들(sim.sh/laptop.sh)과 같은 위치에 두려고 얇게 감싼 것.
#
#   ./image-sender.sh <AI_SERVER_IP>
#
# picamera2 는 시스템 파이썬 패키지라 반드시 시스템 python3 로 실행한다(venv 아님).
set -eo pipefail

AI_IP="${1:?사용법: ./image-sender.sh <AI_SERVER_IP>}"
PORT="${VIDEO_PORT:-6001}"
FPS="${FPS:-15}"
CAM_ARGS="${CAM_ARGS:---picamera}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts -> ros_ws -> libi_drive_controller -> aba_controller -> (레포 루트) -> aba_ai_service
FOLLOWER_DIR="$(cd "$SCRIPT_DIR/../../../../aba_ai_service/follower_perception" && pwd)"

cd "$FOLLOWER_DIR"
exec python3 scripts/camera_sender.py --host "$AI_IP" --port "$PORT" --fps "$FPS" $CAM_ARGS
