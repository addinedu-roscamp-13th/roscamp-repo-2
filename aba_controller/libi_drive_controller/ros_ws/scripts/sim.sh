#!/usr/bin/env bash
# Gazebo 시뮬레이션 + nav2 + rviz(gz_nav2_view) + domain_bridge + fleet_link 를
# tmux 창 9개로 나눠서 실행.
# 창 전환: Ctrl+b 0..8 (gazebo/nav2/rviz/bridge/fleet-link/fsm/init-pose/sim-dock/sim-batt)
#   ./sim.sh            → 헤드리스(뷰어 없이) + nav2 + rviz + domain_bridge + fleet_link + fsm
#   ./sim.sh viewer     → 가제보 GUI(뷰어) 포함
#   ./sim.sh --no-fsm   → fsm 창 없이 (FSM 은 ./fsm-bt.sh 로 따로 띄울 때)
#   ./sim.sh --no-rviz  → rviz 창 없이
#
# BT 를 고치면서 반복할 땐 fsm 창만 재기동하면 된다 (gazebo·nav2 는 그대로 살아있다).
# 창 안에서 Ctrl+C 후 **명령을 직접 타이핑**한다:
#
#     ros2 run libi_modes fsm_node --ros-args -p robot_id:=$FSM_ROBOT_ID
#
# ⚠️ ↑(위 화살표)로 재실행하지 말 것. 이 창의 셸은 `exec bash` 라 **공유
#    ~/.bash_history** 를 읽는다 — ↑ 가 부르는 건 이 창에서 돌던 명령이 아니라
#    "내가 터미널에서 마지막으로 친 아무 명령"이다. 실제로 그게 `tmux kill-server`
#    여서 세션 전체가 날아간 적이 있다.
#
# 아예 따로 굴리려면 --no-fsm + ./fsm-bt.sh.
#
# domain_bridge 창은 sim(현재 셸의 ROS_DOMAIN_ID, 없으면 90) <-> FMS 서버(도메인 86)를 이어준다.
# ros-jazzy-domain-bridge 패키지가 없으면 이 창에 에러만 뜨고 나머지는 정상 진행된다
# (sudo apt install ros-jazzy-domain-bridge 로 설치).
#
# fleet-link 창은 robot_agent(FastAPI) 없이 fleet_link만 단독 실행 — FMS의
# Waypoint "이동" 명령이 fleet_cmd 토픽으로 sim에 도달하려면 반드시 필요하다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
# 한 노트북에서 sim 을 여러 개(다른 이름·도메인) 띄울 수 있게 세션 이름을 바깥에서 줄 수 있다.
# 안 주면 예전과 같은 pinky_sim — kill.sh 는 이 이름을 지운다.
SESSION="${SIM_SESSION:-pinky_sim}"

WORLD_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/worlds/world2.sdf"
MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte3.yaml"
DOMAIN_BRIDGE_TEMPLATE="$ROS_WS_DIR/../../../aba_fms_service/config/domain_bridge_sim.yaml"
ROBOT_AGENT_DIR="$ROS_WS_DIR/../robot_agent"

# 하드코딩 대신 현재 셸에 이미 설정된 ROS_DOMAIN_ID를 그대로 쓴다(없으면 90 기본값).
SIM_DOMAIN_ID="${ROS_DOMAIN_ID:-90}"
# bridge topic prefix, FSM, spawn selection must use the same per-instance identity.
# This must be set before rendering the bridge config; otherwise direct ./sim.sh launches
# with an empty bridge key even though the FSM later defaults to pinkySim.
FSM_ROBOT_ID="${FSM_ROBOT_ID:-pinkySim}"

USE_GUI=false
WITH_FSM=true
WITH_RVIZ=true
for arg in "$@"; do
  case "$arg" in
    viewer) USE_GUI=true ;;
    --no-fsm) WITH_FSM=false ;;
    --no-rviz) WITH_RVIZ=false ;;
  esac
done

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[sim] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."
  exit 1
fi

# domain_bridge_sim.yaml 을 이 로봇에 맞춰 고친 임시본 생성.
#
# ⚠️ [2026-07-22] 예전엔 `from_domain` 만 바꿨다. 그런데 토픽 접두사(`/pinkySim/`)와
#    브릿지 이름(`bridge_sim`)이 템플릿에 박혀 있어서, sim 을 두 개 띄우면 **둘이 같은
#    토픽을 쓴다.** 결과가 조용히 이상했다 — 관제에 두 로봇 위치가 소수점까지 똑같이
#    찍히고, 한쪽에 준 주행 명령을 **양쪽이 다 실행**해 두 대가 붙어 다녔다.
#    접두사는 FMS 쪽 규칙(bridge_key: 하이픈·공백 제거 + 첫 글자 소문자)과 같아야 한다.
#    다르면 관제가 그 로봇 상태를 영영 못 본다.
#
# ⚠️ [2026-07-26] 위 치환이 **정방향만** 고쳤다. `s|/pinkySim/|...|` 는 선행 슬래시가
#    붙은 것만 바꾸는데, 역방향(reversed) 항목은 YAML **키**라 슬래시가 없다:
#        pinkySim/fleet_cmd:        ← 안 바뀜
#          reversed: True
#          remap: /fleet_cmd
#    그래서 브릿지는 `/pinkySim/fleet_cmd` 를 구독하는데 FMS 는 `/pinkysim1/fleet_cmd`
#    로 발행했다 — **sim 에서 주행 명령이 로봇에 영영 도달하지 못했다.**
#    실측(2026-07-26): `/pinkysim1/fleet_cmd` 구독자 0, 순찰이 GRANT 까지만 가고 멈춤.
#    아래 두 번째 치환이 들여쓰기된 키를 잡는다.
BRIDGE_KEY="$(printf '%s' "$FSM_ROBOT_ID" | tr -d '[:space:]_-' | sed 's/^\(.\)/\L\1/')"
DOMAIN_BRIDGE_CONFIG="$(mktemp /tmp/domain_bridge_${BRIDGE_KEY}_XXXX.yaml)"
sed -e "s|^from_domain: .*|from_domain: $SIM_DOMAIN_ID|" \
    -e "s|^name: .*|name: bridge_${BRIDGE_KEY}_${SIM_DOMAIN_ID}|" \
    -e "s|/pinkySim/|/${BRIDGE_KEY}/|g" \
    -e "s|^\([[:space:]]*\)pinkySim/|\1${BRIDGE_KEY}/|g" \
    "$DOMAIN_BRIDGE_TEMPLATE" > "$DOMAIN_BRIDGE_CONFIG"

# 치환이 빠짐없이 됐는지 확인한다. 하나라도 남으면 그 토픽만 조용히 안 이어져서
# "다 되는데 하나만 안 된다" 는 가장 찾기 어려운 형태가 된다.
if grep -q "pinkySim" "$DOMAIN_BRIDGE_CONFIG"; then
  echo "[sim] ⚠️ 브릿지 설정에 치환되지 않은 pinkySim 항목이 남았습니다:" >&2
  grep -n "pinkySim" "$DOMAIN_BRIDGE_CONFIG" >&2
  echo "[sim]    이 토픽은 중계되지 않습니다. $DOMAIN_BRIDGE_TEMPLATE 의 항목 형태를 확인하세요." >&2
  exit 1
fi
echo "[sim] 브릿지 접두사 /$BRIDGE_KEY  (domain $SIM_DOMAIN_ID -> 86)"

# GZ_PARTITION 을 안 주면 gz-transport 기본 파티션이 "호스트명:사용자명" 이라, 이 노트북에서
# 한 사용자가 sim 을 여러 개 띄우면 **전부 같은 gz-transport 버스를 공유한다.** ROS_DOMAIN_ID 는
# DDS(ROS2/nav2)만 분리할 뿐 Gazebo 쪽(world/model pose 등)엔 적용되지 않아서, 서로 다른
# world2 인스턴스의 로봇 위치·포즈가 뒤섞인다 — 로봇들이 "붙어다니는" 증상으로 나타난다.
# 도메인마다 고유한 파티션을 줘서 sim 인스턴스별로 완전히 분리한다.
ROS_SETUP="export ROS_DOMAIN_ID=$SIM_DOMAIN_ID && export GZ_PARTITION=pinky_sim_$SIM_DOMAIN_ID && source /opt/ros/jazzy/setup.bash && source '$ROS_WS_DIR/install/setup.bash' && if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; export CYCLONEDDS_URI=file://$ROS_WS_DIR/cyclonedds.xml; fi"

# libi_modes 는 별도 워크스페이스(aba_controller/libi_modes/ros_ws)라 그쪽 install 도 겹쳐 source 한다.
# 도메인은 위와 같다 — FSM 은 로봇과 같은 도메인에서 돌아야 배터리·명령을 그대로 주고받는다.
LIBI_MODES_WS="$ROS_WS_DIR/../../libi_modes/ros_ws"
LIBI_MODES_SETUP="$ROS_SETUP && source '$LIBI_MODES_WS/install/setup.bash'"

NAVGRAPH="$ROS_WS_DIR/../../../aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml"

# 로봇 번호별 시작 waypoint — sim·실물(pi.sh) 공통 배정. 1=주차장 2=문학서가 3=도서관출입구.
# FSM_ROBOT_ID 에서 끝자리 숫자로 번호를 뽑는다(Pinky-1/pinky1 모두 매치). 번호가 없으면
# (기본 pinkySim 등) 예전처럼 스폰 원점(0,0,0)을 그대로 쓴다.
ROBOT_NUM="$(printf '%s' "$FSM_ROBOT_ID" | grep -oE '[0-9]+' | tail -1)"
case "$ROBOT_NUM" in
  1) INIT_DOCK="주차장" ;;
  2) INIT_DOCK="문학서가" ;;
  3) INIT_DOCK="도서관출입구" ;;
  *) INIT_DOCK="" ;;
esac

if [ -n "$INIT_DOCK" ]; then
  # Resolve the dock exactly once.  Gazebo and AMCL must receive these same values:
  # another name lookup after spawning turns a waypoint rename into a map/odom mismatch.
  # A missing/duplicate/malformed dock is fatal before any tmux window is created; never
  # silently substitute (0, 0), which masked this failure for pinky1.
  SPAWN_POSE="$(NAVGRAPH_PATH="$NAVGRAPH" DOCK_NAME="$INIT_DOCK" python3 - <<'PY'
import os
import sys

import yaml

navgraph = os.environ["NAVGRAPH_PATH"]
dock_name = os.environ["DOCK_NAME"]
try:
    with open(navgraph, encoding="utf-8") as source:
        vertices = yaml.safe_load(source)["levels"]["L1"]["vertices"]
except (OSError, KeyError, TypeError, yaml.YAMLError) as error:
    raise SystemExit(f"[sim] cannot read navgraph {navgraph}: {error}")

matches = []
for vertex in vertices:
    meta = vertex[2] if len(vertex) > 2 and isinstance(vertex[2], dict) else {}
    if str(meta.get("name", "")) == dock_name:
        matches.append((float(vertex[0]), float(vertex[1]), float(meta.get("yaw", 0.0))))

if len(matches) != 1:
    raise SystemExit(
        f"[sim] expected exactly one dock named {dock_name!r} in {navgraph}; found {len(matches)}. "
        "Update the pinky-number-to-dock mapping before starting the simulation.")

print(*matches[0])
PY
)"
  read -r SPAWN_X SPAWN_Y SPAWN_YAW <<< "$SPAWN_POSE"
  echo "[sim] 로봇번호 $ROBOT_NUM → '$INIT_DOCK' ($SPAWN_X, $SPAWN_Y, yaw=$SPAWN_YAW) 에서 스폰"
else
  SPAWN_X="0.0"
  SPAWN_Y="0.0"
  SPAWN_YAW="0.0"
fi

tmux new-session -d -s "$SESSION" -n gazebo \
  bash -c "$ROS_SETUP && ros2 launch pinky_gz_sim launch_sim.launch.xml use_gui:=$USE_GUI world:='$WORLD_PATH' spawn_x:=$SPAWN_X spawn_y:=$SPAWN_Y spawn_yaw:=$SPAWN_YAW; exec bash"

tmux new-window -t "$SESSION" -n nav2 \
  bash -c "$ROS_SETUP && echo '[nav2] 로봇 스폰 대기 중 (/scan 토픽 확인)...' && for i in \$(seq 1 30); do ros2 topic list 2>/dev/null | grep -q '/scan' && break; sleep 1; done && ros2 launch pinky_navigation gz_bringup_launch.xml map:='$MAP_PATH'; exec bash"

if [ "$WITH_RVIZ" = true ]; then
  tmux new-window -t "$SESSION" -n rviz \
    bash -c "$ROS_SETUP && ros2 launch pinky_navigation gz_nav2_view.launch.xml; exec bash"
fi

tmux new-window -t "$SESSION" -n bridge \
  bash -c "$ROS_SETUP && echo '[bridge] domain $SIM_DOMAIN_ID <-> 86 연결 (FMS 서버)...' && ros2 run domain_bridge domain_bridge '$DOMAIN_BRIDGE_CONFIG'; exec bash"

tmux new-window -t "$SESSION" -n fleet-link \
  bash -c "$ROS_SETUP && cd '$ROBOT_AGENT_DIR' && echo '[fleet-link] robot_agent 없이 fleet_link 단독 실행 (domain $SIM_DOMAIN_ID)...' && python3 scripts/run_fleet_link.py; exec bash"

if [ "$WITH_FSM" = true ]; then
  tmux new-window -t "$SESSION" -n fsm \
    bash -c "$LIBI_MODES_SETUP && echo '[fsm] libi_modes 미션 FSM (domain $SIM_DOMAIN_ID, robot_id=$FSM_ROBOT_ID)...' && ros2 run libi_modes fsm_node --ros-args -p robot_id:=$FSM_ROBOT_ID; exec bash"
fi

# 주행 경로는 이제 **BT 를 지난다** — path_request_driver 창은 일부러 없다.
#
#   FMS  fleet_node → /robot_path_requests → fleet_dispatch_bridge
#        → /fleet_cmd{navigate} → libi_modes  WorkingBranch ▸ NavigationExec
#        → /fleet_cmd{goal} → fleet_link → nav2
#
# 예전엔 path_request_driver.py 가 /robot_path_requests 를 받아 nav2 로 **직접** 몰았다.
# 그걸 같이 띄우면 같은 주행이 두 갈래로 나가고(BT + 드라이버), 무엇이 로봇을 움직였는지
# 구분할 수 없어 BT 전환을 검증할 수가 없다. 그래서 뺐다.
# 되돌려야 하면 backend .env 의 LIBI_NAV_VIA_BT=0 으로 두고 그 파일을 손으로 띄운다
# (파일 맨 위 주석 참고) — 둘 다 켜는 조합만은 피한다.

# AMCL 초기 자세를 넣는다. **로봇이 움직이기 전에** 끝나야 하므로 sim-dock/sim-batt 보다 앞이다
# (그 둘이 /is_docked 와 배터리를 내야 FSM 이 IDLE 을 벗어난다).
#
# 파라미터의 set_initial_pose 만으로는 안 잡혔다 — 실측으로 amcl 이 y 로 0.85m 어긋난 채
# 수렴했다. arte2 는 평행한 긴 복도가 많아 복도를 따라 미끄러진 자세가 제자리와 거의
# 같아 보인다. 자세한 이유는 set_initial_pose.py 머리말.
INIT_POSE="$ROS_WS_DIR/../scripts/set_initial_pose.py"
if [ -f "$INIT_POSE" ]; then
  if [ -n "$INIT_DOCK" ]; then
    tmux new-window -t "$SESSION" -n init-pose \
      bash -c "$ROS_SETUP && echo \"[init-pose] Gazebo 스폰 자세를 AMCL에 적용 ($SPAWN_X, $SPAWN_Y, yaw=$SPAWN_YAW)...\" && python3 '$INIT_POSE' --x '$SPAWN_X' --y '$SPAWN_Y' --yaw '$SPAWN_YAW'; exec bash"
  else
    tmux new-window -t "$SESSION" -n init-pose \
      bash -c "$ROS_SETUP && echo '[init-pose] AMCL 초기 자세 (스폰 0,0,0)...' && python3 '$INIT_POSE' --x 0 --y 0 --yaw 0; exec bash"
  fi
fi

# 주차장 도착을 위치로 확인해 /is_docked 를 낸다. **sim·실물 공통**이다.
# 정밀 주차(테이프 추종)가 아직 복귀 경로에 배선돼 있지 않아서, 지금은 실물도 같은
# 판정을 쓴다 (scripts/drive-pi/dock/README.md 의 미결 1~4 참고).
# 이게 없으면 RETURNING 이 영영 안 끝난다 — 부팅 상태가 RETURNING 이라 로봇이
# 첫 상태에서 한 발짝도 못 나가고, 배차 대상(IDLE/PATROL)이 되지 못한다.
DOCK_CONFIRM="$ROS_WS_DIR/../scripts/dock_confirm.py"
if [ -f "$DOCK_CONFIRM" ]; then
  tmux new-window -t "$SESSION" -n sim-dock \
    bash -c "$ROS_SETUP && echo '[sim-dock] 주차장 도착 → /is_docked (domain $SIM_DOMAIN_ID)...' && python3 '$DOCK_CONFIRM' --navgraph '$NAVGRAPH'; exec bash"
fi

# [sim 전용] 배터리. 실물은 pinky_bringup 의 battery_publisher 가 INA219 를 읽는다.
# 이게 없으면 battery_percent 가 None 이라 CHARGING -> IDLE 이 안 일어나고,
# 로봇이 충전 상태로 굳어 배차 대상(IDLE/PATROL)이 되지 못한다.
#   소모는 기본 0%/분 — 시험이 예고 없이 배터리 저하 복귀로 끊기지 않게 한다.
#   배터리 시나리오를 볼 땐:  SIM_BATTERY_ARGS='--start 30 --drain 5' ./sim.sh ...
SIM_BATTERY="$ROS_WS_DIR/../scripts/sim_battery.py"
if [ -f "$SIM_BATTERY" ]; then
  tmux new-window -t "$SESSION" -n sim-batt \
    bash -c "$ROS_SETUP && echo '[sim-batt] 배터리 흉내 → /battery/percent (domain $SIM_DOMAIN_ID)...' && python3 '$SIM_BATTERY' ${SIM_BATTERY_ARGS:-}; exec bash"
fi

tmux select-window -t "$SESSION:gazebo"
# 인터랙티브(TTY)일 때만 붙는다. 백그라운드(nohup/cron/ssh)면 세션은 그대로 두고 안내만
# 한다 — 세션·서비스는 위에서 이미 떠 있어 백그라운드에서도 정상 동작한다.
if [ -t 1 ]; then
  tmux attach -t "$SESSION"
else
  echo "[bg] '$SESSION' 세션이 백그라운드로 떴습니다. 붙으려면: tmux attach -t $SESSION"
fi
