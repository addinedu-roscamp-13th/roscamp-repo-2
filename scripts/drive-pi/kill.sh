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
