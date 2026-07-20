#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — AI 서버가 UDP 로 내려보내는 (linear_x, angular_z) 를 받아
# /cmd_vel 로 발행하는 브릿지(cmd_bridge). 추종 주행의 유일한 ROS 조각이다.
#
#   ./follow-drive.sh                  # 기본: --port 6002 --flip-180
#   ./follow-drive.sh --no-flip        # 이 로봇 LiDAR 가 정방향이면
#   CMD_PORT=6002 ./follow-drive.sh
#
# ⚠️ --flip-180 은 로봇마다 다르다(LiDAR 180도 장착일 때만). 회피가 반대로 돌면 빼야 한다.
# ⚠️ ROS_DOMAIN_ID 는 이 셸에 이미 설정된 로봇 도메인을 그대로 쓴다(여기서 정하지 않는다).
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FOLLOWER="$REPO_ROOT/aba_ai_service/follower_perception"
if [ ! -d "$FOLLOWER" ]; then
  die "$FOLLOWER 가 없습니다 — 로봇 체크아웃에 aba_ai_service 가 필요합니다 (image-sender.sh 안내 참고)."
fi

# cmd_bridge 는 geometry_msgs/Twist(표준 메시지)만 쓰므로 jazzy 만 source 하면 된다.
[ -f /opt/ros/jazzy/setup.bash ] || die "ROS2 jazzy 가 없습니다(/opt/ros/jazzy/setup.bash). ROS2 를 먼저 설치하세요."
source /opt/ros/jazzy/setup.bash
need_py_module rclpy "ROS2 설치·소싱을 확인하세요 (rclpy 가 안 잡힙니다)."

# 인자를 안 주면 이 로봇에서 검증된 기본값. --no-flip 이면 flip 을 뺀다.
ARGS=(--port "${CMD_PORT:-6002}")
case " $* " in
  *" --no-flip "*) : ;;                          # flip 없이
  *)               ARGS+=(--flip-180) ;;
esac
# --no-flip 은 우리가 소비했으므로 나머지만 넘긴다.
for a in "$@"; do [ "$a" = "--no-flip" ] || ARGS+=("$a"); done

cd "$FOLLOWER"
exec python3 scripts/cmd_bridge.py "${ARGS[@]}"
