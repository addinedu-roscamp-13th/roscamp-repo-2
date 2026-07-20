#!/usr/bin/env bash
# 로봇 팔 보드(Handy)에서 실행 — libi_handy_controller 노드.
# handy_cmd 구독 / handy_result 발행. Drive 보드와 같은 ROS_DOMAIN_ID(셸 값)에서 통신.
#
#   ./handy.sh
#
# ⚠️ ROS_DOMAIN_ID 는 셸에 이미 설정된 값을 그대로 쓴다 — Drive 보드와 반드시 같아야 한다
#    (한 로봇, 두 보드). 팔 모션은 현재 스텁 — 팔 담당자가 채운다(요청서 참고).
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HANDY_WS="$REPO_ROOT/aba_controller/libi_handy_controller/ros_ws"
ensure_built "$HANDY_WS"

cd "$REPO_ROOT"
source /opt/ros/jazzy/setup.bash
source "$HANDY_WS/install/setup.bash"

echo "[handy] libi_handy_controller  domain=${ROS_DOMAIN_ID:-(미설정)}"
exec ros2 run libi_handy_controller handy_node
