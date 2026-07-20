#!/usr/bin/env bash
# aba_ai_service 단독 실행 — pm2(ecosystem.config.js 의 aba-ai-stub) 없이
# 로컬에서 직접 붙여서 테스트할 때 쓴다. ROS2 소싱 불필요(순수 stdlib 소켓 릴레이).
#
#   ./ai-server.sh
#
# 환경변수(기본값은 main.py 참고):
#   AI_SERVICE_UDP_PORT   image_sender 로부터 받는 포트 (기본 9000)
#   FMS_TCP_HOST/PORT     관제용 push 대상, aba_fms_service (기본 127.0.0.1:9010)
#   ROBOT_DETECTION_HOST/PORT   추종 제어용 직결 채널, 로봇의 libi_perception
#                               (기본 127.0.0.1:6000 — ai_service 를 로봇과 다른 머신에서
#                               돌리면 반드시 로봇 IP로 오버라이드할 것, 안 그러면 로컬만 보고 못 나간다)
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$AI_SERVICE_DIR/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

if [ -x "$VENV_PY" ]; then
  PY="$VENV_PY"
else
  PY="python3"
fi

cd "$AI_SERVICE_DIR"
exec "$PY" main.py
