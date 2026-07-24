#!/usr/bin/env bash
# 노트북에서 실행(테스트용) — libi_gui 터치패널을 지정한 로봇 것으로 띄운다.
# 추종 화면(PERCEPTION_URL)과 FMS 승인(FMS_URL)이 붙을 서버 주소를 .env 의 LAPTOP_IP 로 채운다.
#
#   ./libi_gui.sh pinky3
#   FMS_URL=http://192.168.0.9:9001 ./libi_gui.sh pinky3   # 서버가 딴 데면 개별 오버라이드
#
# 실물 배포(패널이 로봇에 있음)에서는 LAPTOP_IP 대신 실제 서버 IP 로 .env 를 맞추면 된다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

ROBOT_ID_ARG="${1:?사용법: ./libi_gui.sh <pinky1|pinky2|pinky3>}"
[ -n "${LAPTOP_IP:-}" ] || die "LAPTOP_IP 가 .env 에 없습니다 (FMS·AI 서버가 도는 머신 IP)."

# 이미 셸에서 준 값이 있으면 존중하고, 없으면 LAPTOP_IP 로 채운다.
export PERCEPTION_URL="${PERCEPTION_URL:-$LAPTOP_IP:5007}"
export FMS_URL="${FMS_URL:-http://$LAPTOP_IP:9001}"

cd "$REPO_ROOT"
exec "$REPO_ROOT/aba_controller/libi_gui/gui.sh" "$ROBOT_ID_ARG"
