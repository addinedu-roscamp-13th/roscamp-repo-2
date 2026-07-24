#!/usr/bin/env bash
# aba_ai_service/main.py — UDP:9000 영상을 받아 더미 추론 결과를 내보내는 릴레이 stub.
#
#   image_sender ──UDP:9000──▶ main.py ──TCP:9010──▶ aba_fms_service (관제용)
#                                       └─TCP:6000──▶ 로봇 libi_perception (추종 제어용)
#
# ⚠️ 관리자 추종 화면과는 무관하다. 그건 ./ai-server.sh (perception_server) 쪽이다.
#    이 stub 은 아직 YOLO 가 안 붙어 owner 가 항상 None 이라, 실제 추종은 못 한다.
#    "두 갈래 구현" 중 내려놓기로 한 경로라 따로 이름을 뗐다
#    (docs/superpowers/specs/2026-07-20-admin-follow-control-path-design.md 참고).
#
# 환경변수(기본값은 main.py 참고):
#   AI_SERVICE_UDP_PORT   image_sender 로부터 받는 포트 (기본 9000)
#   FMS_TCP_HOST/PORT     관제용 push 대상, aba_fms_service (기본 127.0.0.1:9010)
#   ROBOT_DETECTION_HOST/PORT   추종 제어용 직결 채널, 로봇의 libi_perception
#                               (기본 127.0.0.1:6000 — 로봇과 다른 머신에서 돌리면 반드시
#                               로봇 IP로 오버라이드할 것, 안 그러면 로컬만 보고 못 나간다)
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "$AI_SERVICE_DIR/.." && pwd)"

VENV_PY="$REPO_ROOT/.venv/bin/python"
PY="${PYTHON:-$([ -x "$VENV_PY" ] && echo "$VENV_PY" || echo python3)}"

cd "$AI_SERVICE_DIR"
exec "$PY" main.py
