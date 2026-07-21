#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — 주행 스택 전체(hw·nav2·fleet-link·fsm·path-driver·led)를 tmux 로 띄운다.
# aba_controller/.../ros_ws/scripts/pi.sh 에 위임하되, 두 워크스페이스가 안 빌드돼 있으면
# colcon build 까지 해 준다. 실행 위치와 무관하다(REPO_ROOT 기준).
#
#   ./pi.sh --robot Pinky-3              # 전체
#   ./pi.sh --robot Pinky-3 --no-fsm     # fsm 창 없이 (플래그는 그대로 위임)
#   ./pi.sh --robot Pinky-3 --no-led
#
# ## 이름을 왜 꼭 받나 (기본값을 없앤 이유)
# fleet_node·FSM·경로 드라이버가 쓰는 **정본 이름은 DB `rc_robots.name`** 이다(`Pinky-3`).
# 예전에는
#   - 인자 없이 `./pi.sh` 로 안쪽 스크립트를 직접 부르면 기본값 `pinky1` 로 떴고
#   - 래퍼는 IP 키(`pinky3`)를 그대로 이름으로 썼다
# 둘 다 DB 이름과 달라서 이런 일이 벌어졌다:
#   · fsm_node 가 robot_id=pinky1 로 발행 → fleet_node 는 Pinky-3 로 조회 → 영영 안 붙음
#     (관제에 "상태 미상", 배차 가능 0대)
#   · path-driver 가 PathRequest.robot_name="Pinky-3" 를 전부 걸러 배차해도 안 움직임
# 조용히 어긋나느니 **멈추고 묻는 편이 낫다** — sim.sh 와 같은 원칙이다.
#
# IP 키는 이름에서 만든다: `Pinky-3` → `PINKY3_IP` (영숫자만 남기고 대문자).
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

ROBOT=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot) ROBOT="${2:-}"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
# 환경변수로 준 값도 인정한다(자동화 스크립트용). 인자가 있으면 인자가 이긴다.
ROBOT="${ROBOT:-${FSM_ROBOT_ID:-}}"

if [ -z "$ROBOT" ]; then
  echo "[pi] ❌ 로봇 이름을 반드시 지정해야 합니다." >&2
  echo >&2
  echo "  사용법:  ./pi.sh --robot <이름> [--no-fsm] [--no-led]" >&2
  echo >&2
  echo "  이름은 관제 DB(rc_robots.name)에 등록된 값과 **정확히** 같아야 합니다." >&2
  echo "  다르면 fleet_node 가 이 로봇을 못 알아보고, 배차해도 움직이지 않습니다." >&2
  echo "  관제 「로봇 관리」 화면에서 확인하세요.  예:  ./pi.sh --robot Pinky-3" >&2
  exit 1
fi

# IP 키는 이름에서 유도한다: Pinky-3 -> PINKY3_IP
IP_KEY="$(printf '%s' "$ROBOT" | tr -cd '[:alnum:]' | tr '[:lower:]' '[:upper:]')_IP"
ROBOT_IP="${!IP_KEY:-}"
if [ -z "$ROBOT_IP" ]; then
  echo "[pi] ⚠️ .env 에 $IP_KEY 가 없습니다 — IP 가 필요한 기능만 영향을 받습니다(주행은 진행)."
fi

ensure_built "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws"
ensure_built "$REPO_ROOT/aba_controller/libi_modes/ros_ws"

echo "[pi] 로봇=$ROBOT  IP=${ROBOT_IP:-미설정}  도메인=${ROS_DOMAIN_ID:-셸 값}"

# 안쪽 pi.sh 는 FSM_ROBOT_ID(env)를 읽고 --no-fsm/--no-led 를 자기 인자에서 파싱한다.
cd "$REPO_ROOT"
exec env FSM_ROBOT_ID="$ROBOT" \
  "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/pi.sh" "${ARGS[@]}"
