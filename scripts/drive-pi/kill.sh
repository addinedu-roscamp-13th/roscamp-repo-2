#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — pi.sh 가 띄운 주행 스택(tmux 세션 pinky_pi + 고아 노드) 정리.
#   1) image-sender.sh / follow-drive.sh 가 띄운 프로세스 (여기서 직접)
#   2) 나머지(pinky_pi·pinky_sim 세션, launch 자식 노드)는 ros_ws/scripts/kill.sh 에 위임
# 이름은 drive-pi/ 지만 sim 세션도 같이 지우므로 노트북에서 sim 정리에도 쓸 수 있다.
#
#   ./kill.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

# ⚠️ 이 둘은 tmux 없이 `exec` 로 포그라운드에 뜬다 — **세션 정리로는 안 잡힌다.**
# Ctrl+C 없이 창만 닫으면 살아남아, camera_sender 는 AI 서버로 영상을 계속 쏘고
# cmd_bridge 는 UDP:6002 주행 명령을 계속 받는다(추종을 껐는데 로봇이 움직인다).
# 위임 대상인 ros_ws/scripts/kill.sh 는 aba_controller 소유라 여기서 덮는다.
#   camera_sender.py : image-sender.sh  (영상 → AI 서버 UDP:6001)
#   cmd_bridge.py    : follow-drive.sh  (추종 cmd_vel 수신 UDP:6002)
kill_patterns "camera_sender.py" "cmd_bridge.py"

# ── 상태를 IDLE 로 되돌린다 ─────────────────────────────────────────────────
#
# LED 소등과 **같은 이유**다: 프로세스를 죽이면 마지막 상태가 그대로 남는다.
# LED 는 스트립이 마지막 프레임을 들고 있고, FSM 은 DB·관제 캐시가 마지막 상태를
# 들고 있다. 그래서 다음에 켰을 때 로봇이 WORKING·INTERACTING 인 채로 부팅한 것처럼
# 보이고, 관제가 "일하는 중" 으로 알고 배차를 피한다.
#
# ⚠️ **위임(ros_ws/scripts/kill.sh)보다 먼저.** 세션이 먼저 죽으면 요청을 받을
#    `fsm_node` 가 사라져 아무 일도 안 일어난다. LED 소등이 위에 있는 이유와 같다.
#
# ⚠️ `force: true` 를 준다. 전이표에 `WORKING -> IDLE` 간선이 없어서, force 없이는
#    일하던 로봇이 그 상태로 남는다(`state_io.validate` — force 가 간선 제약과
#    ERROR 이탈 가드를 둘 다 푼다). 지금은 어차피 스택을 내리는 참이라 안전 규칙을
#    지킬 대상 자체가 사라진다.
#
# ⚠️ 결과를 안 기다린다. `fsm_node` 가 안 떠 있으면 응답이 영영 안 오는데, 정리
#    스크립트가 거기서 멈추면 아무것도 못 죽인다. 못 보내도 그냥 넘어간다 —
#    보내는 것이 **덤**이지 전제가 아니다.
# ⚠️ **DDS 구현을 스택과 맞춘다. 안 맞으면 이 아래 전부가 헛수고다.**
#
#   `libi_pi.sh:79-83` 이 모든 tmux 창에 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` 와
#   `CYCLONEDDS_URI` 를 export 한다. 여기서 그냥 `ros2 topic pub` 을 부르면 기본값
#   (Fast DDS)으로 뜨고, **DDS 는 구현이 다르면 매칭 자체를 안 만든다.**
#   정지 명령도 IDLE 전이도 로봇에 영영 안 닿는다 — 그리고 **에러가 한 줄도 안 난다.**
#   `ros2 topic pub` 은 발행에 성공했다고 하고 끝난다.
#
#   같은 사고가 2026-07-28 에 이미 있었다(`libi_pi.sh:70-77`): twist_mux·모터는
#   CycloneDDS, FSM·follow_node 는 Fast DDS 라 `/cmd_vel_follow` 도 `/libi/motion_lock`
#   도 twist_mux 에 영영 안 닿았다. "화면은 전부 정상이고 에러도 없다 — 바퀴만 안 돈다."
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
  export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
  _KILL_CDDS="$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/cyclonedds.xml"
  [ -f "$_KILL_CDDS" ] && export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://$_KILL_CDDS}"
fi

if pgrep -f "[f]sm_node" >/dev/null 2>&1; then
  # ⚠️ **robot_id 를 도는 프로세스에서 읽는다. hostname 을 쓰면 안 된다.**
  #    실측 2026-08-02 (pinky-3): hostname 은 `raspi` 인데 fsm_node 는
  #    `-p robot_id:=pinky-3` 로 떠 있다. `_on_request_topic` 은 robot_id 가
  #    **정확히** 같을 때만 받으므로(남의 요청 무시), 틀리면 요청이 조용히 버려진다 —
  #    에러도 안 나고 상태도 안 바뀐다.
  #    같은 이유가 `drive-pi/pi.sh:16` 주석에도 이미 적혀 있다.
  ROBOT_ID="${ROBOT_ID:-$(pgrep -af "[f]sm_node" 2>/dev/null \
      | grep -oE 'robot_id:=[A-Za-z0-9_-]+' | head -1 | cut -d= -f2 || true)}"
  ROBOT_ID="${ROBOT_ID:-$(hostname)}"
  # 도메인은 로봇마다 다르다. .env/셸 값이 있으면 그걸 쓰고, 없으면 이름에서 표로 뽑는다
  # (_common.sh `robot_domain` — 116 + 번호). 둘 다 없으면 조회를 건너뛴다.
  KILL_DOMAIN="${ROS_DOMAIN_ID:-$(robot_domain "$ROBOT_ID" || true)}"
  if [ -n "$KILL_DOMAIN" ] && [ -f /opt/ros/jazzy/setup.bash ]; then
    echo "정지 + IDLE 전이 (robot_id=$ROBOT_ID, domain=$KILL_DOMAIN)"
    # ⚠️ **YAML 을 셸에 인라인으로 쓰지 않는다.** `ros2 topic pub` 은 인자를 YAML 로
    #    파싱하는데, `bash -c "…"` 안에 JSON 을 넣으면 따옴표가 두 겹으로 깨져
    #    `The passed value needs to be in YAML string or a dictionary` 로 죽는다
    #    (실측 2026-08-02 — 이것 때문에 정지 명령이 통째로 안 나갔다).
    #    파일에 써서 `--file`... 은 없으므로, **작은따옴표 없는 형태**로 만든다:
    #    `-p` 대신 `{data: "…"}` 를 홑따옴표로 감싸고 JSON 안에는 \" 만 쓴다.
    # ⚠️ [2026-08-04] IDLE 전이 한 방으로 줄였다. 예전엔 mission_stop(nav 취소) +
    #    cmd_vel_stop(속도 override) 을 IDLE 전이 앞뒤로 따로 쐈다 — `ros2 topic pub`
    #    콜드스타트가 이 Pi 에서 콜당 ~4.3초(실측)라 그거 4번이 kill 시간 대부분이었다.
    #    이제 안 그래도 된다: `state_io.py` `MOTION_LOCKED_STATES` 에 IDLE 이 들면
    #    `_locked=True` 가 되어 `/cmd_vel_hold` 를 20Hz 로 계속 낸다(priority 160,
    #    잠금 150 위·비상정지 255 아래 — nav2/follow 보다 우선). 그래서 **IDLE 전이
    #    자체가 실제로 바퀴를 세운다**(실측 2026-08-04, pinky-3, 풀스택 기동 중).
    #    ⚠️ 이 메커니즘 자체는 2026-07-30 부터 있었는데 옛 주석(2026-08-02 실측)은
    #    "IDLE 만으론 안 멈춘다" 였다 — 그 사이 어딘가 고쳐진 걸로 보인다. 다시 이
    #    증상이 보이면(전이 보냈는데 계속 구르면) mission_stop/cmd_vel_stop 을
    #    되살리는 게 맞다(git log 이 커밋 이전 버전 참고).
    timeout 12 env ROS_DOMAIN_ID="$KILL_DOMAIN" \
      bash -c 'source /opt/ros/jazzy/setup.bash; exec ros2 topic pub --once "$0" "$1" "$2"' \
      /libi/fsm_transition_request std_msgs/msg/String \
      "{data: '{\"robot_id\":\"$ROBOT_ID\",\"id\":\"kill-idle\",\"target_state\":\"IDLE\",\"force\":true}'}" \
      >/dev/null 2>&1 \
      || echo "[kill] ⚠ IDLE 전이를 못 보냈다"

    sleep 0.3   # fsm_node 가 tick 에서 락을 걸고 cmd_vel_hold 를 시작할 틈
  else
    echo "[kill] ⚠ 도메인을 못 정해 IDLE 전이를 건너뛴다 (ROS_DOMAIN_ID 를 주면 보낸다)"
  fi
fi

# ── LED 소등 ────────────────────────────────────────────────────────────────
# 스택을 내려도 LED 는 켜진 채 남는다. 스트립이 마지막 프레임을 그대로 들고 있기 때문이다.
#
# 끄는 코드는 이미 있다 — state_led_node.py 의 `finally: node.led.clear()`.
# 안 돌던 이유 두 가지:
#   ① **SIGTERM 은 finally 를 안 탄다.** 파이썬 기본 SIGTERM 은 즉시 종료라 clear() 가
#      호출되지 않는다. SIGINT(=Ctrl+C) 여야 KeyboardInterrupt → finally 로 간다.
#   ② **그 노드는 root 다.** pinkyled.py 가 rpi_ws281x 때문에 sudo 로 자기를 재실행한다
#      → 일반 사용자 pkill 은 권한이 없어 조용히 아무 일도 안 일어난다.
# 게다가 위임 대상(ros_ws/scripts/kill.sh)의 패턴 목록에 pinky_led 가 아예 없다.
#
# ⚠️ 반드시 **위임보다 먼저** — 세션이 먼저 죽으면 신호 받을 프로세스가 사라져 켜진 채 남는다.
# ⚠️ sudo 는 `-n`(비대화) 으로 준다. 비밀번호를 물으면 kill 이 거기서 멈춰 버린다.
if pgrep -f "[s]tate_led" >/dev/null 2>&1; then
  sudo -n pkill -INT -f "[s]tate_led" 2>/dev/null || pkill -INT -f "[s]tate_led" 2>/dev/null || true
  echo "LED 소등 요청 (SIGINT → led.clear())"
  for _ in $(seq 1 6); do
    pgrep -f "[s]tate_led" >/dev/null 2>&1 || break
    sleep 0.5
  done
  if pgrep -f "[s]tate_led" >/dev/null 2>&1; then
    sudo -n pkill -KILL -f "[s]tate_led" 2>/dev/null || pkill -KILL -f "[s]tate_led" 2>/dev/null || true
    echo "[kill] ⚠ state_led 가 SIGINT 로 안 죽어 강제 종료 — LED 가 켜진 채 남을 수 있다"
  fi
fi

exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
