#!/usr/bin/env bash
# sim.sh / pi.sh 로 띄운 tmux 세션과 관련 프로세스를 전부 종료한다.
#   - sim  : 가제보/nav2/rviz/domain_bridge/fleet_link  (세션 pinky_sim)
#   - 실물 : hw(bringup)/nav2/fleet_link/fsm/led        (세션 pinky_pi)
set -u

# ── tmux 세션 정리 ────────────────────────────────────────────────
# 세션을 지우면 창(pane)은 사라지지만, launch 가 띄운 자식 노드는 고아로 남을 수
# 있으므로 아래 프로세스 종료 단계가 반드시 뒤따라야 한다.
# pinky_laptop 은 pi.sh 의 옛 이름(laptop.sh)이 쓰던 세션이다. 이름을 바꾸기 전에 띄워둔
# 세션이 정리되지 않고 남는 걸 막으려고 당분간 같이 지운다.
# pinky_sim* : sim 을 여러 개 띄우면 세션이 pinky_sim_<로봇> 으로 갈라진다(SIM_SESSION).
for session in $(tmux ls -F "#{session_name}" 2>/dev/null | grep -E "^pinky_sim" ) pinky_sim pinky_pi pinky_laptop; do
  if tmux has-session -t "$session" 2>/dev/null; then
    tmux kill-session -t "$session"
    echo "killed tmux session: $session"
  fi
done

# 매칭되는 패턴 여러 개를 한 신호로, 정규식 `|` 로 합쳐 **pgrep/pkill 호출 자체를
# 패턴 개수와 무관하게 tick당 1번**으로 종료한다.
# 실측(pinky-3, 2026-08-04): `pgrep -f` 한 번이 ~0.27초 걸린다(약한 CPU). 패턴마다
# 따로 pgrep 하면 8 tick × N패턴이 전부 그 값을 물어 N=13이면 대기만 수 초가 샌다 —
# 신호를 한 번에 보내는 것만으론 부족했다. 합쳐서 호출 수 자체를 줄인다.
# 정상 종료 신호를 먼저 줘야 rclpy 가 DDS 에서 노드를 정상 탈퇴(deregister)하고,
# 그래야 `ros2 node list` 에 유령 노드가 남지 않는다. 곧바로 -9 로 죽이면 유령이 생긴다.
# 첫 인자가 신호(launch 부모는 INT 로 자식까지 정상 종료, 나머지는 TERM).
kill_patterns_batch() {
  local sig="$1"; shift
  local combined
  combined="$(IFS='|'; echo "$*")"
  pgrep -f "$combined" >/dev/null 2>&1 || return 0   # 아무것도 안 살아있으면 바로 끝

  pkill -"$sig" -f "$combined" 2>/dev/null
  printf 'SIG%s: %s\n' "$sig" "$*"

  # 최대 4초까지 정상 종료를 기다린다 — 합친 패턴 하나로 매 tick 확인한다.
  local _tick
  for _tick in $(seq 1 8); do
    pgrep -f "$combined" >/dev/null 2>&1 || return 0
    sleep 0.5
  done

  # 4초 뒤에도 살아있으면 강제 종료.
  pkill -KILL -f "$combined" 2>/dev/null
  echo "SIGKILL(잔여): $combined"
}

# 1) launch 부모: SIGINT(=Ctrl+C)를 주면 ros2 launch 가 자기 자식 노드들을
#    정상 종료시킨다. 실물 hw / nav2 를 여기서 내린다.
LAUNCH_PATTERNS=(
  "ros2 launch pinky_bringup bringup_robot.launch"   # 실물 hw
  "ros2 launch pinky_navigation bringup_launch"      # 실물 nav2
  "ros2 launch pinky_gz_sim"
  "ros2 launch pinky_navigation gz_bringup_launch"
  "ros2 launch pinky_navigation gz_nav2_view"
)
kill_patterns_batch INT "${LAUNCH_PATTERNS[@]}"

# 2) 개별 프로세스 및 launch 가 남긴 고아 노드 정리(SIGTERM → SIGKILL).
#    경로 패턴으로 실물 hw/nav2 의 자식 노드(amcl·controller·lidar·battery 등)까지
#    빠짐없이 쓸어담는다.
PATTERNS=(
  # sim 관련
  "gz sim"
  "parameter_bridge"
  "ros_gz_image"
  "ros_gz_sim create"
  "rviz2"
  "component_container_isolated"
  "domain_bridge"
  # fleet_link (robot_agent 없이 단독 실행분) — run_fleet_link.py 및 -tunning 변형 모두
  "run_fleet_link"
  # 실물 hw/nav2 고아 노드(고아로 남았을 때의 안전망)
  "install/pinky_bringup/lib"
  "install/pinky_navigation/lib"
  "opt/ros/jazzy/lib/nav2"
  "joint_state_publisher"
  "robot_state_publisher"
  "sllidar"
  # fleet_node <-> 로봇을 잇는 어댑터 두 개. tmux 창으로 뜨지만 세션이 죽어도 살아남는
  # 경우가 있어(오늘 실제로 남았다) 이름으로도 확실히 쓸어담는다.
  #   path_request_driver : fleet_node 경로 -> nav2   (로봇 쪽, 지금은 BT 폴백)
  #   robot_state_adapter : 로봇 위치 -> fleet_node   (서버 쪽)
  #   dock_confirm        : 주차장 도착 -> /is_docked (sim·실물 공통)
  #   sim_battery         : 배터리 흉내 -> /battery/percent (sim 전용)
  "path_request_driver.py"
  "robot_state_adapter.py"
  "dock_confirm.py"
  "set_initial_pose.py"
  "sim_battery.py"
  # fleet_node(libi_fleet, 배차·교통) 본체 — scripts/laptop/kill.sh 가 세션 이름으로도
  # 지우지만(libi_fms), 위 어댑터들과 같은 이유로 고아 생존 안전망을 여기에도 둔다.
  "libi_fleet/lib/libi_fleet/fleet_node"
)

# 도킹은 모터를 직접 물고 있다. 스크립트로 시작했으면 반드시 멈춰 둔다 —
# 안 그러면 다음 시험에서 로봇이 혼자 돌고 있다.
# 노트북에서는 :9001 이 FMS 백엔드라 404 가 오고 아무 일도 안 일어난다(무해).
if command -v curl >/dev/null 2>&1; then
  curl -s -m 3 -X POST "http://127.0.0.1:9001/api/park/stop" -d '{}' \
    -H 'Content-Type: application/json' >/dev/null 2>&1 && echo "park/stop 요청함"
fi
kill_patterns_batch TERM "${PATTERNS[@]}"

# 3) DDS 유령 노드 정리: 프로세스를 죽여도 ros2 daemon 이 그래프 캐시를 들고 있어
#    `ros2 node list` 에 죽은 노드가 계속 보일 수 있다. daemon 을 멈춰 캐시를 비운다
#    (다음 ros2 명령 때 자동으로 다시 뜨며 살아있는 노드만으로 그래프를 재구성한다).
if command -v ros2 >/dev/null 2>&1; then
  ros2 daemon stop >/dev/null 2>&1 && echo "ros2 daemon stop (유령 노드 캐시 비움)"
fi

exit 0
