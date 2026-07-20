#!/usr/bin/env bash
# libi_gui(로봇 터치패널)를 "어느 로봇의 패널인지" 지정해서 띄운다.
#
#   ./gui.sh pinky3                       # robot_id 지정
#   FMS_URL=http://192.168.0.9:9001 ./gui.sh pinky3
#
# robot_id 는 FMS 승인 요청의 키다 — libi_modes 가 상태를 발행할 때 쓰는 값과 반드시 같아야
# 한다(laptop.sh 의 FSM_ROBOT_ID). 다르면 FMS 가 "알 수 없는 로봇"으로 거부한다.
#
# ROS_DOMAIN_ID 는 여기서 정하지 않는다 — 실물은 로봇마다 도메인이 고정(87/88/89)이라
# 셸에 이미 설정된 값을 그대로 쓴다(laptop.sh 와 같은 원칙). GUI 가 도메인을 직접 쓰진
# 않지만, 어느 로봇 패널인지 확인할 수 있게 기동 로그에 함께 찍는다.
set -eo pipefail
# -u 는 안 쓴다 — ROS2 setup.bash 계열과 함께 source 될 때 미정의 변수로 죽는다
# (sim.sh/laptop.sh/ros-domain-bridge.sh 도 같은 이유로 -e 만 쓴다).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ROBOT_ID="${1:-${ROBOT_ID:-}}"
export FMS_URL="${FMS_URL:-http://127.0.0.1:9001}"

if [ -z "$ROBOT_ID" ]; then
  echo "[gui] 사용법: ./gui.sh <robot_id>   (예: ./gui.sh pinky3)"
  echo "[gui] robot_id 는 laptop.sh 의 FSM_ROBOT_ID 와 같은 값이어야 합니다."
  exit 1
fi

BIN="$SCRIPT_DIR/build/libi_gui"
if [ ! -x "$BIN" ]; then
  echo "[gui] 빌드가 없습니다. 먼저 빌드하세요:"
  echo "  cmake -S '$SCRIPT_DIR' -B '$SCRIPT_DIR/build' -DCMAKE_BUILD_TYPE=Release && cmake --build '$SCRIPT_DIR/build' -j"
  exit 1
fi

echo "[gui] robot_id=$ROBOT_ID  domain=${ROS_DOMAIN_ID:-(미설정)}  fms=$FMS_URL"
exec "$BIN" "${@:2}"
