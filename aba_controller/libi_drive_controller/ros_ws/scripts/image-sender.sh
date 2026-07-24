#!/usr/bin/env bash
# 로봇(Pi) 카메라 -> UDP 로 AI 서버에 영상만 전송. 추종 전체(추적/명령)가 아니라
# "영상이 AI 서버까지 도착하는가"만 먼저 검증할 때 쓴다 — 원본은
# aba_ai_service/follower_perception/scripts/camera_sender.py, 여기 scripts/ 는 다른
# Pi 런처들(sim.sh/pi.sh)과 같은 위치에 두려고 얇게 감싼 것.
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
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
FOLLOWER_DIR="$REPO_ROOT/aba_ai_service/follower_perception"

# 로봇 체크아웃에 aba_controller 만 있는 경우가 있다(서버 코드는 안 내려감). 그러면 여기서
# 날것의 cd 에러가 나서 원인이 안 보이므로 먼저 짚어준다.
if [ ! -d "$FOLLOWER_DIR" ]; then
  echo "[image-sender] $FOLLOWER_DIR 가 없습니다."
  echo "  camera_sender.py 는 aba_ai_service 에 있는데, 이 체크아웃엔 안 받아져 있습니다."
  echo "  로봇에서도 그 서브트리가 필요합니다 (follower_perception/pi.sh 도 원래 로봇용입니다):"
  echo "    cd $REPO_ROOT"
  echo "    git sparse-checkout list            # sparse 체크아웃인지 확인"
  echo "    git sparse-checkout add aba_ai_service"
  echo "    git pull"
  exit 1
fi

cd "$FOLLOWER_DIR"
exec python3 scripts/camera_sender.py --host "$AI_IP" --port "$PORT" --fps "$FPS" $CAM_ARGS
