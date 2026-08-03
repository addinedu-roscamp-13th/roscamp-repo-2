#!/usr/bin/env bash
# 실물 로봇 하드웨어 + nav2(arte3 맵) + fleet_link(robot_agent/FastAPI 없이 단독)
# + libi_modes 미션 FSM + 상태별 LED(pinky_led/state_led) 를 tmux 창 5~6개로 나눠서 실행.
# pm2(ecosystem.config.js)를 안 쓰고 로컬에서 직접 붙여서 테스트할 때 쓴다.
#
# sim.sh 와 달리 domain_bridge 창이 없다 — 실물에서 브릿지는 로봇이 아니라
# FMS 서버에서 돈다("로봇은 무수정", domain_bridge_pinky*.yaml 주석 참고).
# LED 는 실물 전용 — pinkyled 모듈이 rpi_ws281x 를 직접 잡고(root 필요) sim.sh 엔 없다.
# led_server(pinky_led 의 다른 노드)와 동시 실행 금지 — 둘 다 LED 스트립을 단독 점유한다.
# 창 전환: Ctrl+b 0/1..(hw/nav2/[init-pose]/fleet-link/fsm/led), 또는 Ctrl+b n / Ctrl+b p
#   (init-pose 창은 FSM_ROBOT_ID 가 Pinky-1/2/3 일 때만 뜬다 — 로봇 번호별 시작 waypoint 주입)
#   ./pi.sh
#   ./pi.sh --no-fsm       → fsm 창 없이 (FSM 은 ./fsm-bt.sh 로 따로 띄울 때)
#   ./pi.sh --no-led       → led 창 없이 (LED 상태 표시 코드 안 쓸 때)
#   ./pi.sh --no-battery   → [디버그] 배터리 자동 전이 OFF. 배터리 값을 못 믿을 때
#                            (센서 이상·미배선) 쓴다. 값이 튀면 로봇이 순회 도중 제멋대로
#                            RETURNING 으로 빠져 다른 기능을 아무것도 검증할 수 없다.
#                            끄면 복귀·순회 시작을 관제 UI 에서 직접 눌러 돌린다.
#
# ⚠️ aba_ai_service/follower_perception/pi.sh 와 이름이 같지만 다른 스크립트다. 그쪽은
#    추종용(bringup + 카메라 + cmd_bridge)이고 이건 주행용(bringup + nav2 + FSM + LED)이다.
#    둘 다 같은 bringup 을 띄우므로 동시에 실행할 수 없다.
#
# 도메인은 하드코딩하지 않는다 — 이 로봇에 이미 설정된 ROS_DOMAIN_ID(보통
# ros_source.sh나 셸 환경에서 지정됨)를 그대로 쓴다. sim.sh와 달리 실물은
# 도메인이 로봇마다 고정(87/88/89)이라 여기서 바꾸면 안 된다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_WS_DIR="$(dirname "$SCRIPT_DIR")"
ROBOT_AGENT_DIR="$ROS_WS_DIR/../robot_agent"
SESSION="pinky_pi"
WITH_FSM=true
WITH_LED=true
# 통행 금지 필터(동적 장애물). 기본 꺼짐 — libi_pi.sh 가 --dyn-obstacle 을 받으면 넘겨준다.
WITH_KEEPOUT=false
# [디버그] 배터리로 인한 자동 상태 전이. 기본 켜짐.
BATTERY_AUTO=true
# [2026-07-30] `--panel=IP:PORT`(노트북 libi_gui 의 VNC 화면을 로봇에 전체화면으로 띄우기)는
# 만들었다가 지웠다. **로봇에 띄울 화면이 없다** — 실측하니 이 Pi 의 X `:0` 은 DUMMY0(가상
# 출력)이고 `/dev/fb*` 도 없다. 로봇에 달린 LCD 는 `pinky_lcd_server` 가 모는 240×240 SPI 라
# X 화면이 아니고, 1280×800 짜리 패널을 넣어도 글자를 못 읽는다.
# HDMI 터치스크린을 실제로 달면 그때 되살린다(뷰어 접속·디코딩까지는 검증됐다).
for arg in "$@"; do
  [ "$arg" = "--no-fsm" ] && WITH_FSM=false
  [ "$arg" = "--no-led" ] && WITH_LED=false
  [ "$arg" = "--keepout" ] && WITH_KEEPOUT=true
  [ "$arg" = "--no-battery" ] && BATTERY_AUTO=false
done

MAP_PATH="$ROS_WS_DIR/src/pinky_pro/pinky_navigation/map/arte3.yaml"
NAVGRAPH="$ROS_WS_DIR/../../../aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml"
INIT_POSE="$ROS_WS_DIR/../scripts/set_initial_pose.py"

# 로봇 번호별 시작 waypoint — sim(scripts/sim.sh)과 공통 배정. 1=충전소 2=문학서가 3=도서관출입구.
# FSM_ROBOT_ID(예: Pinky-3)의 끝자리 숫자로 뽑는다. 실물은 스폰이 없으므로 **운영자가
# 로봇을 그 waypoint 위치에 실제로 놓아야** AMCL 추정과 실제 위치가 맞는다.
ROBOT_NUM="$(printf '%s' "$FSM_ROBOT_ID" | grep -oE '[0-9]+' | tail -1)"
case "$ROBOT_NUM" in
  # [2026-07-31] `주차장` → `충전소`. **navgraph 에 '주차장' 정점이 아예 없다.**
  #   `arte2.navgraph.yaml` 에 있는 것은 `충전소통로`·`충전소입구`·`충전소` 셋이고
  #   `주차장` 은 0건이다. 그래서 pinky-1 을 띄우면 init-pose 가
  #   "navgraph 에 '주차장' 정점이 없습니다" 로 죽고, **AMCL 초기 자세가 안 잡혀**
  #   관제 좌표가 (0,0) 으로 뜬다(2026-07-31 실측).
  #
  # yaw 도 0 → 3.1415 다. navgraph 의 `- {name: '충전소', yaw: 3.1415}` 를 따른다.
  # ⚠️ 아래 pinky-3 주석과 같은 경고가 여기도 적용된다 — **로봇을 실제로 놓은 방향이
  #    이 값과 다르면** AMCL 이 처음부터 180° 어긋난 채 시작한다. 충전소에 도킹한
  #    방향(벽을 등지는 쪽)이 3.1415 다.
  1) INIT_DOCK="충전소";       INIT_YAW="3.1415" ;;
  2) INIT_DOCK="문학서가";      INIT_YAW="0" ;;
  # [2026-07-28] Pinky-3 은 **도서관출입구**(+0.300,-1.704) 에서 **순회경로-5 를 보게** 놓는다.
  #
  # 왜 1.5708 인가: 순회경로-5 는 (+0.300,-1.385) 라 x 가 같고 y 만 +0.319 다.
  # 즉 방위가 **정확히 +90°** 다 — 근사가 아니다. (예전 주석은 주차장까지의 100.2° 를
  # 기준으로 "10° 차"라고 적었는데, 기준 자체가 사용자 의도와 달랐다.)
  #
  # ⚠️ 여기 값과 **로봇을 실제로 놓은 방향이 다르면** AMCL 이 처음부터 어긋난 채
  #    시작한다. 스캔이 맞아 들어가도 복도가 평행해 한참 뒤에야 드러난다.
  #    2026-07-28 실측: 라이다 스캔이 맵 벽과 45° 가량 어긋난 채 수렴해 맵 밖으로 나갔다.
  #    값이 맞는데 어긋나면 **놓은 방향**을 먼저 의심할 것.
  3) INIT_DOCK="도서관출입구";   INIT_YAW="1.5708" ;;
  *) INIT_DOCK="";             INIT_YAW="0" ;;
esac

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[pi] '$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."
  exit 1
fi

# CycloneDDS 사용(설치돼 있을 때만 — 없으면 FastDDS로 자동 폴백). Pi에서 FastDDS
# SHM 전송 스레드(dds.shm)가 CPU를 크게 먹어 CycloneDDS로 교체. 모든 노드가 같은
# RMW 여야 통신되므로 hw/nav2/fleet_link 세 창 모두 이 ROS_SETUP 을 공유한다.
ROS_SETUP="source /opt/ros/jazzy/setup.bash && source '$ROS_WS_DIR/install/setup.bash' && if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then export RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}; export CYCLONEDDS_URI=\${CYCLONEDDS_URI:-file://$ROS_WS_DIR/cyclonedds.xml}; fi"

# libi_modes 는 별도 워크스페이스라 그쪽 install 도 겹쳐 source 한다.
# 도메인은 셸에 이미 설정된 로봇 도메인을 그대로 쓴다 (실기는 로봇별 88/89/…).
LIBI_MODES_WS="$ROS_WS_DIR/../../libi_modes/ros_ws"
# ⚠️ **기본값을 두지 않는다.** 예전엔 `pinky1` 이 기본이라 인자 없이 이 스크립트를 직접
# 실행해도 그냥 떴는데, 어느 로봇도 그 이름이 아니라서 조용히 어긋났다:
#   fsm_node 가 robot_id=pinky1 로 발행 → fleet_node 는 DB 이름(Pinky-3)으로 조회 → 안 붙음
#   (관제에 "상태 미상", 배차 가능 0대 / path-driver 는 경로를 전부 무시)
# 이름은 DB `rc_robots.name` 과 정확히 같아야 한다. 보통 scripts/drive-pi/pi.sh 가 넣어 준다.
if [ -z "${FSM_ROBOT_ID:-}" ]; then
  echo "[pi] ❌ FSM_ROBOT_ID 가 없습니다 — 로봇 이름 없이는 띄우지 않습니다." >&2
  echo "     scripts/drive-pi/pi.sh --robot <DB이름>  으로 실행하세요 (예: --robot Pinky-3)." >&2
  echo "     직접 부를 때는:  FSM_ROBOT_ID=Pinky-3 $0" >&2
  exit 1
fi
LIBI_MODES_SETUP="$ROS_SETUP && source '$LIBI_MODES_WS/install/setup.bash'"

tmux new-session -d -s "$SESSION" -n hw \
  bash -c "$ROS_SETUP && ros2 launch pinky_bringup bringup_robot.launch.xml; exec bash"

# 동적 장애물(통행 금지 필터)을 켤 때만 필터 포함 파라미터를 쓴다. 기본은 예전 그대로다 —
# 필터가 `/costmap_filter_info` 를 기다리므로, 발행 노드 없이 켜 두면 어떻게 구는지에
# 의존하게 된다. 그래서 파일을 갈라 두고 런처가 고른다.
NAV2_PARAMS_ARG=""
if [ "$WITH_KEEPOUT" = true ]; then
  NAV2_PARAMS_ARG="params_file:=$ROS_WS_DIR/src/pinky_pro/pinky_navigation/params/nav2_params_keepout.yaml"
  echo "[pi] 통행 금지 필터 ON — nav2_params_keepout.yaml 사용"
fi
# ⚠️ use_composition:=False — nav2 노드를 **각각 별도 프로세스로** 띄운다.
#
# 2026-07-28: nav2 를 1.3.10 → 1.3.12 로 올린 뒤 컴포지션 컨테이너가 기동 중
# 죽었다(exit -6). 로그에 남은 것은 이 한 줄이었다:
#
#     Magick: abort due to signal 11 (SIGSEGV) "Segmentation Fault"
#
# 맵 파일 문제가 아니다 — `ros2 run nav2_map_server map_server` 로는 세 맵 모두
# 정상적으로 열린다. **한 프로세스에 전부 올렸을 때만** 죽는다. 컴포지션에서는
# 컴포넌트 하나가 죽으면 컨테이너가 통째로 사라져 nav2 가 전부 내려간다.
#
# 프로세스를 나누면 안 죽고, lifecycle 도 정상 활성화된다(Managed nodes are active).
# 대가는 프로세스 수와 intra-process 통신을 못 쓰는 것뿐이다.
#
# ── [2026-07-30] 범인을 좁혔다: 컴포지션이 아니라 GraphicsMagick 이다 ──────────
#
# `map_server` 의 libmap_io.so 가 GraphicsMagick 을 링크한다:
#     ldd $(ros2 pkg prefix nav2_map_server)/lib/libmap_io.so | grep -i magick
#       libGraphicsMagick++-Q16.so.12 · libGraphicsMagick-Q16.so.3
# GraphicsMagick 은 전역 초기화와 **자기 시그널 핸들러**를 심는다 — 위 `Magick: abort…` 가
# 정확히 그 핸들러의 출력이다. 즉 "한 프로세스에 전부"가 아니라 **"GraphicsMagick 을 같이
# 올린 것"** 이 문제였다. 그리고 map_server 는 localization_launch.xml 에만 있다.
#
# 그래서 **navigation 절반만** 컴포즈한다(`use_composition_nav:=True`).
#   컨테이너 안 : controller · planner · behavior · bt_navigator · smoother ·
#                 waypoint_follower · velocity_smoother · lifecycle_manager_navigation  (8→1)
#   밖에 남김   : map_server(GraphicsMagick 격리) · amcl · lifecycle_manager_localization
#
# 왜 이득인가: 실측 load 25~28 / idle 7% 인데 **sy 40% · ctxsw 33,608/s** 였다. 계산이 아니라
# 프로세스마다 따로 갖는 DDS participant(수신 스레드+discovery)와 프로세스 경계 통신 비용이다.
# ⚠️ zero-copy 는 아직 아니다 — launch 에 use_intra_process_comms 설정이 없다. participant
#    7개가 사라지는 이득만 본다. 직렬화까지 없애려면 컴포저블 노드에 extra_arg 로
#    use_intra_process_comms=true 를 붙여야 하고, 그건 1단계가 안정된 뒤에 따로 한다.
#
# 되돌리기: 아래 줄에서 `use_composition_nav:=True` 한 토막만 지운다(그러면 예전 동작).
# 첫 기동 확인: `ros2 component list` 로 8개가 붙었는지 + nav2 로그의 "Managed nodes are active".
#   컨테이너가 죽으면 nav2 가 통째로 내려간다 — 그때는 위 한 토막을 지우고 재기동한다.
tmux new-window -t "$SESSION" -n nav2 \
  bash -c "$ROS_SETUP && echo '[nav2] 하드웨어(/scan) 대기 중...' && for i in \$(seq 1 30); do ros2 topic list 2>/dev/null | grep -q '/scan' && break; sleep 1; done && ros2 launch pinky_navigation bringup_launch.xml map:='$MAP_PATH' use_composition:=False use_composition_nav:=True $NAV2_PARAMS_ARG; exec bash"

if [ -f "$INIT_POSE" ] && [ -n "$INIT_DOCK" ]; then
  tmux new-window -t "$SESSION" -n init-pose \
    bash -c "$ROS_SETUP && echo \"[init-pose] ⚠️ 로봇을 '$INIT_DOCK' 위치에 yaw=$INIT_YAW rad 방향으로 놓았는지 확인하세요. AMCL 초기 자세 발행 중...\" && python3 '$INIT_POSE' --dock '$INIT_DOCK' --navgraph '$NAVGRAPH' --yaw '$INIT_YAW'; exec bash"
fi

tmux new-window -t "$SESSION" -n fleet-link \
  bash -c "$ROS_SETUP && cd '$ROBOT_AGENT_DIR' && echo '[fleet-link] robot_agent 없이 fleet_link 단독 실행 (costmap 제외 경량 버전)...' && python3 scripts/run_fleet_link-tunning.py; exec bash"

if [ "$WITH_FSM" = true ]; then
  # --no-battery: 배터리 값이 못 믿을 때 저전력 복귀·자동 순회 시작을 끈다. 임계를
  # 닿지 않는 값으로 바꿀 뿐이라 BT 구조·관제 화면은 그대로다(main.py `_apply_battery_auto`).
  FSM_BATTERY_ARG=""
  [ "$BATTERY_AUTO" = false ] && FSM_BATTERY_ARG=" -p battery_auto:=false"

  # [2026-07-30] BT tick 10 → 5Hz. 코드 기본값은 10.0 이다(libi_modes/main.py:87).
  #
  # 왜: 실측(Pi 4코어) fsm_node 가 CPU **33.9%** 를 썼다. tick 마다 88노드 트리를 순회하고
  # 상태·잠금·LED·타입 토픽 4개를 발행한다. 그 부하에서 nav2 제어 루프가 주기를 놓쳤고
  # (`Control loop missed its desired rate of 10.0000 Hz`) 순회가 20초씩 멈췄다.
  # 이 FSM 이 다루는 시간 상수는 UI 타임아웃 20초·목표 재발행 30초·명령 타임아웃 120초다 —
  # 5Hz(200ms)로도 한참 빠르다.
  #
  # ⚠️ 안전 확인: 모션 잠금(`/libi/motion_lock`)은 twist_mux 에서 `timeout: 0.0`(만료 없음)이라
  #    발행 주기가 안전에 영향을 주지 않는다. 잠금 중 0속도 발행(`cmd_vel_hold`)은 state_io 의
  #    **별도 20Hz 타이머**라 이 값과 무관하다. 대가는 명령 수신→전이 반응이 최대 200ms 로
  #    늘어나는 것뿐이다.
  # 되돌리려면 `FSM_TICK_HZ=10 ./pi.sh …` 또는 아래 기본값을 10 으로.
  FSM_TICK_HZ="${FSM_TICK_HZ:-5}"
  FSM_BATTERY_NOTE=""
  [ "$BATTERY_AUTO" = false ] && FSM_BATTERY_NOTE="  ⚠️ 배터리 자동 전이 OFF (복귀는 관제 UI 에서 직접)"
  tmux new-window -t "$SESSION" -n fsm \
    bash -c "$LIBI_MODES_SETUP && echo '[fsm] libi_modes 미션 FSM (robot_id=$FSM_ROBOT_ID, tick ${FSM_TICK_HZ}Hz)...$FSM_BATTERY_NOTE' && ros2 run libi_modes fsm_node --ros-args -p robot_id:=$FSM_ROBOT_ID -p tick_hz:=$FSM_TICK_HZ$FSM_BATTERY_ARG; exec bash"
fi

if [ "$WITH_LED" = true ]; then
  tmux new-window -t "$SESSION" -n led \
    bash -c "$ROS_SETUP && echo '[led] 상태별 LED (fsm_state 토픽 구독, root 필요 — pinkyled 가 자동 sudo 재실행)...' && ros2 launch pinky_led state_led.launch.xml; exec bash"
fi

# 주행 경로는 이제 **BT 를 지난다** — path_request_driver 창은 일부러 없다.
#
#   FMS  fleet_node → /robot_path_requests → fleet_dispatch_bridge
#        → /fleet_cmd{navigate} → libi_modes  WorkingBranch ▸ NavigationExec
#        → /fleet_cmd{goal} → robot_agent fleet_link → nav2
#
# 예전엔 path_request_driver.py 가 /robot_path_requests 를 받아 nav2 로 **직접** 몰았다.
# 그래서 로봇 FSM 이 배달 중에도 IDLE/PATROL 로 남았고, 관제가 배달 중인 로봇을
# "배차 가능"으로 표시했다. 둘을 같이 띄우면 같은 주행이 두 갈래로 나간다.
# 되돌려야 하면 서버 .env 의 LIBI_NAV_VIA_BT=0 으로 두고 그 파일을 손으로 띄운다.

# dock-confirm 창은 여기서 뺐다 (scripts/dock_confirm.py 는 그대로 남아 있다).
#
# ⚠️ 되살릴 때 알아야 할 것: 이 창이 위치를 보고 /is_docked 를 내던 자리다. 아무도
#    /is_docked 를 내지 않으면 RETURNING 이 끝나지 않아 로봇이 부팅 상태에서 못
#    나가고 배차 대상(IDLE/PATROL)이 되지 못한다. 그 신호를 다른 곳에서 내고 있는지
#    확인하고 띄울 것:
#      python3 aba_controller/libi_drive_controller/scripts/dock_confirm.py \
#        --navgraph aba_fms_service/fleet_ws/maps/library/arte2.navgraph.yaml
#    원래 자리는 정밀 주차(테이프 추종) 폐루프였다 — scripts/drive-pi/dock/README.md 미결 1~4.

tmux select-window -t "$SESSION:hw"
# 인터랙티브(TTY)일 때만 붙는다. 백그라운드(nohup/cron/ssh)면 세션은 그대로 두고 안내만
# 한다 — 세션·서비스는 위에서 이미 떠 있어 백그라운드에서도 정상 동작한다.
if [ -t 1 ]; then
  tmux attach -t "$SESSION"
else
  echo "[bg] '$SESSION' 세션이 백그라운드로 떴습니다. 붙으려면: tmux attach -t $SESSION"
fi
