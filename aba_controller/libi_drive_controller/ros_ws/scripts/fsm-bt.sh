#!/usr/bin/env bash
# libi_modes 미션 FSM(py_trees BT) 단독 실행.
#
#   ./fsm-bt.sh                 → robot_id=pinkySim, 현재 셸의 ROS_DOMAIN_ID (없으면 90)
#   ./fsm-bt.sh pinky1          → robot_id 지정
#   ROS_DOMAIN_ID=88 ./fsm-bt.sh pinky1
#   TICK_HZ=2 ./fsm-bt.sh       → 느리게 돌려 로그 읽기 좋게
#   ./fsm-bt.sh --disable RETURNING,CHARGING  → [디버그] 그 상태 브랜치(BT)를 잠가 실행 안 함
#                                               (그 상태에선 로봇 정지=안전 프리즈. 세션 동안 고정)
#
# sim.sh / pi.sh 가 이미 fsm 창을 띄우므로 평소엔 이 스크립트가 필요 없다.
# 따로 두는 이유는 BT 를 고치면서 반복할 때다 — gazebo·nav2 를 살려둔 채
# FSM 만 죽였다 살릴 수 있고(재기동 30초+ 절약), 이미 떠 있는 실기 로봇에
# FSM 만 붙이거나 다른 머신에서 돌릴 때도 쓴다.
#
# FSM 은 반드시 로봇과 같은 도메인에서 돌아야 한다 — 배터리·명령을 그대로 주고받고,
# 중앙(86)으로는 domain_bridge 가 올려준다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
LIBI_MODES_WS="$ROS_WS_DIR/../../libi_modes/ros_ws"

# --disable 는 robot_id 로 오인되면 안 되므로 따로 파싱한다. 그 외 첫 인자 = robot_id.
ROBOT_ID=""
DISABLE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --disable) DISABLE="$2"; shift 2 ;;
    --disable=*) DISABLE="${1#*=}"; shift ;;
    *) [ -z "$ROBOT_ID" ] && ROBOT_ID="$1"; shift ;;
  esac
done
ROBOT_ID="${ROBOT_ID:-${FSM_ROBOT_ID:-pinkySim}}"
TICK_HZ="${TICK_HZ:-10.0}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-90}"
# main.py 가 LIBI_DISABLED_BRANCHES(콤마구분) 를 읽어 그 상태 브랜치를 잠근다.
[ -n "$DISABLE" ] && export LIBI_DISABLED_BRANCHES="$DISABLE"

if [ ! -f "$LIBI_MODES_WS/install/setup.bash" ]; then
  echo "[fsm-bt] libi_modes 가 아직 빌드되지 않았습니다."
  echo "         cd $LIBI_MODES_WS && colcon build --symlink-install"
  exit 1
fi

source /opt/ros/jazzy/setup.bash
source "$ROS_WS_DIR/install/setup.bash" 2>/dev/null || true
source "$LIBI_MODES_WS/install/setup.bash"

# sim.sh / pi.sh 와 같은 DDS 설정을 쓴다 — 안 맞추면 같은 도메인이어도 서로 못 본다.
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
  export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
  export CYCLONEDDS_URI="file://$ROS_WS_DIR/cyclonedds.xml"
fi

echo "[fsm-bt] domain=$ROS_DOMAIN_ID robot_id=$ROBOT_ID tick=${TICK_HZ}Hz${DISABLE:+ disabled=$DISABLE}"
exec ros2 run libi_modes fsm_node --ros-args \
  -p robot_id:="$ROBOT_ID" \
  -p tick_hz:="$TICK_HZ"
