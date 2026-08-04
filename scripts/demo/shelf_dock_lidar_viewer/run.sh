#!/usr/bin/env bash
# 노트북에서 로봇 한 대의 서가 도킹 판단(PGM)과 실제 라이다(/scan)를 함께 본다.
#
#   ./scripts/demo/shelf_dock_lidar_viewer.sh --robot pinky-3
#   ./scripts/demo/shelf_dock_lidar_viewer.sh --robot pinky-3 --domain-id 119 --range-m 1.5
#
# IP는 DHCP로 바뀌지만 ROS_DOMAIN_ID는 로봇별로 고정이다. `--robot`으로 도메인을
# 정해야 다른 로봇의 /scan을 조용히 구독하는 사고를 막을 수 있다.
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/../../_common.sh"

usage() {
  cat <<'EOF'
사용:
  ./scripts/demo/shelf_dock_lidar_viewer.sh --robot pinky-3 [--domain-id 119] [뷰어 옵션]

필수:
  --robot <이름>       관제 DB의 로봇 이름. 예: pinky-3

선택:
  --domain-id <번호>   ROS 도메인 직접 지정. 생략 시 pinky-1/2/3 표 또는 DB를 사용
  --help               이 도움말

나머지 인자는 Python 뷰어로 그대로 전달한다. 예: --range-m 1.5 --scan /scan
EOF
}

need_arg() {
  case "${2:-}" in
    ''|--*) die "$1 뒤에 값이 필요합니다" ;;
  esac
  printf '%s' "$2"
}

ROBOT=""
DOMAIN_ID=""
VIEWER_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot) ROBOT="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    --domain-id) DOMAIN_ID="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) VIEWER_ARGS+=("$1"); shift ;;
  esac
done

[ -n "$ROBOT" ] || die "--robot 이 필요합니다. 예: --robot pinky-3"
if [ -z "$DOMAIN_ID" ]; then
  DOMAIN_ID="$(robot_domain "$ROBOT")"
fi
if [ -z "$DOMAIN_ID" ]; then
  DOMAIN_ID="$(robot_domain_db "$ROBOT")"
fi
[ -n "$DOMAIN_ID" ] || die "'$ROBOT'의 ROS 도메인을 모릅니다. --domain-id <번호>를 지정하세요."
case "$DOMAIN_ID" in
  *[!0-9]*) die "도메인은 숫자여야 합니다: '$DOMAIN_ID'" ;;
esac

need_cmd python3
need_py_module cv2 "OpenCV가 필요합니다."
[ -f /opt/ros/jazzy/setup.bash ] || die "/opt/ros/jazzy/setup.bash가 없습니다."
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID="$DOMAIN_ID"

echo "[dock-viewer] robot=$ROBOT ROS_DOMAIN_ID=$ROS_DOMAIN_ID"
exec python3 "$HERE/shelf_dock_lidar_viewer.py" --robot "$ROBOT" "${VIEWER_ARGS[@]}"
