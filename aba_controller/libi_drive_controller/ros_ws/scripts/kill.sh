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

# 패턴에 매칭되는 프로세스를 (기본 SIGTERM) → 대기 → SIGKILL 순으로 종료한다.
# 정상 종료 신호를 먼저 줘야 rclpy 가 DDS 에서 노드를 정상 탈퇴(deregister)하고,
# 그래야 `ros2 node list` 에 유령 노드가 남지 않는다. 곧바로 -9 로 죽이면 유령이 생긴다.
# 두 번째 인자로 첫 신호를 바꿀 수 있다(launch 부모는 INT 로 자식까지 정상 종료).
kill_pattern() {
  local pattern="$1"
  local sig="${2:-TERM}"
  pgrep -f "$pattern" >/dev/null 2>&1 || return 0

  pkill -"$sig" -f "$pattern" 2>/dev/null
  echo "SIG$sig: $pattern"

  # 최대 4초까지 정상 종료를 기다린다.
  for _ in $(seq 1 8); do
    pgrep -f "$pattern" >/dev/null 2>&1 || return 0
    sleep 0.5
  done

  # 아직 살아있으면 강제 종료.
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    pkill -KILL -f "$pattern" 2>/dev/null
    echo "SIGKILL(잔여): $pattern"
  fi
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
for pattern in "${LAUNCH_PATTERNS[@]}"; do
  kill_pattern "$pattern" INT
done

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
  #   path_request_driver : fleet_node 경로 -> nav2   (로봇 쪽)
  #   robot_state_adapter : 로봇 위치 -> fleet_node   (서버 쪽)
  "path_request_driver.py"
  "robot_state_adapter.py"
)
for pattern in "${PATTERNS[@]}"; do
  kill_pattern "$pattern"
done

# 3) DDS 유령 노드 정리: 프로세스를 죽여도 ros2 daemon 이 그래프 캐시를 들고 있어
#    `ros2 node list` 에 죽은 노드가 계속 보일 수 있다. daemon 을 멈춰 캐시를 비운다
#    (다음 ros2 명령 때 자동으로 다시 뜨며 살아있는 노드만으로 그래프를 재구성한다).
if command -v ros2 >/dev/null 2>&1; then
  ros2 daemon stop >/dev/null 2>&1 && echo "ros2 daemon stop (유령 노드 캐시 비움)"
fi

exit 0
