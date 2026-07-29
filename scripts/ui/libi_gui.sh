#!/usr/bin/env bash
# 노트북에서 실행(테스트용) — libi_gui 터치패널을 지정한 로봇 것으로 띄운다.
# 추종 화면(PERCEPTION_URL)·FMS 승인(FMS_URL)·도서 검색(ABA_SERVICE_URL)이 붙을 서버
# 주소를 .env 의 LAPTOP_IP 로 채운다.
#
#   ./libi_gui.sh pinky-3 --domain-id 88
#   FMS_URL=http://192.168.0.9:9001 ./libi_gui.sh pinky-3 --domain-id 88   # 서버가 딴 데면 개별 오버라이드
#
# robot_id 는 pi.sh --robot / sim.sh --robot 에 준 값(= DB rc_robots.name)과 **완전히 같아야**
# 한다 — FMS 승인 요청의 키라서, 다르면 "알 수 없는 로봇"으로 거부된다(gui.sh 주석 참고).
#
# 실물 배포(패널이 로봇에 있음)에서는 LAPTOP_IP 대신 실제 서버 IP 로 .env 를 맞추면 된다.
set -eo pipefail

# --domain-id 를 실제 CLI 인자로 강제한다 — 환경변수(ROS_DOMAIN_ID)로 받으면 .bashrc
# 의 jazzy 기본값·.env 의 ROS_DOMAIN_ID(실물 한 대 기준값) 등 셸에 이미 깔린 값과
# 구분이 안 돼 "안 적었는데 조용히 딴 값으로 붙는" 사고가 반복됐다. 인자는 셸 상태와
# 무관하게 타이핑 여부가 그대로 드러나 이 문제 자체가 안 생긴다.
ROBOT_ID_ARG=""
DOMAIN_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain-id) DOMAIN_ID="${2:?--domain-id 뒤에 값이 필요합니다}"; shift 2 ;;
    --domain-id=*) DOMAIN_ID="${1#*=}"; shift ;;
    *) ROBOT_ID_ARG="$1"; shift ;;
  esac
done

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

[ -n "$ROBOT_ID_ARG" ] || die "사용법: ./libi_gui.sh <robot_id> --domain-id <n>
  robot_id 는 pi.sh --robot / sim.sh --robot 에 준 값과 같아야 합니다 (예: pinky-3, pinky-sim-1)."
[ -n "$DOMAIN_ID" ] || die "--domain-id 가 없습니다 — sim.sh(또는 이 로봇)에 쓴 것과 같은 값을 명시하세요.
  예:  ./libi_gui.sh $ROBOT_ID_ARG --domain-id 90"
export ROS_DOMAIN_ID="$DOMAIN_ID"

[ -n "${LAPTOP_IP:-}" ] || die "LAPTOP_IP 가 .env 에 없습니다 (FMS·AI 서버가 도는 머신 IP)."

# 이미 셸에서 준 값이 있으면 존중하고, 없으면 LAPTOP_IP 로 채운다.
export PERCEPTION_URL="${PERCEPTION_URL:-$LAPTOP_IP:5007}"
export FMS_URL="${FMS_URL:-http://$LAPTOP_IP:9001}"
# 도서 검색/추천 — aba_service backend(.env 의 ABA_SERVICE_PORT, 기본 8000).
export ABA_SERVICE_URL="${ABA_SERVICE_URL:-http://$LAPTOP_IP:${ABA_SERVICE_PORT:-8000}}"

# sim.sh 가 dock 을 못 찾으면 (0,0) 으로 조용히 넘어가지 않고 즉시 죽는 것과 같은 원칙 —
# 여기서 주소가 하나라도 안 열려 있으면 GUI 는 뜨지만 매 클릭마다 "Operation canceled" 만
# 반복하며 원인을 알기 어렵다. 뜨기 전에 한 번 찔러보고, 안 열려 있으면 그 자리에서 죽는다.
# 실물 배포(pi.sh 로 켠 로봇 위 GUI)는 부팅 순서상 서버가 늦게 뜰 수 있어 이 스크립트가
# 아니라 gui.sh 를 직접 쓴다 — 이 체크는 사람이 직접 눈으로 보며 테스트하는 이 스크립트에만 둔다.
check_reachable() {
  local name="$1" url="$2"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 "$url" 2>/dev/null || true)"
  [ "$code" != "000" ] || die "$name 에 연결할 수 없습니다: $url
  이 머신에서 sim 만 로컬로 테스트하는 거면(로봇+서버 분리 배포가 아니면) 이렇게:
    ${name}_URL=http://127.0.0.1:<port> ./libi_gui.sh $ROBOT_ID_ARG --domain-id $DOMAIN_ID
  .env 의 LAPTOP_IP 가 지금 이 머신 IP 와 다르면 매번 이래야 한다 — LAPTOP_IP 를 고치는 게 낫다.
  진짜 그 주소가 맞다면 서버가 떠 있는지 먼저 확인하세요."
}
check_reachable "FMS" "$FMS_URL"
check_reachable "ABA_SERVICE" "$ABA_SERVICE_URL"

cd "$REPO_ROOT"

# VNC 포트는 **로봇 번호에서 계산한다** — AI 수신 포트(6001/6011/6021, VIEWER 5007/5017/5027)와
# 같은 원칙이다. 안 그러면 2대째 패널이 5900 을 못 잡고 그 자리에서 죽는다.
#   pinky-1 → 5901   pinky-2 → 5902   pinky-3 → 5903   (번호를 못 뽑으면 5900)
ROBOT_NUM="$(printf '%s' "$ROBOT_ID_ARG" | grep -oE '[0-9]+' | tail -1)"
export VNC_PORT="${VNC_PORT:-$((5900 + ${ROBOT_NUM:-0}))}"
echo "[libi_gui] VNC :$VNC_PORT — 태블릿/로봇 패널에서 $(hostname -I 2>/dev/null | awk '{print $1}'):$VNC_PORT 로 접속"
echo "[libi_gui]   이 PC 에 창으로 띄우려면: ./aba_controller/libi_gui/gui.sh $ROBOT_ID_ARG --window"

exec "$REPO_ROOT/aba_controller/libi_gui/gui.sh" "$ROBOT_ID_ARG"
