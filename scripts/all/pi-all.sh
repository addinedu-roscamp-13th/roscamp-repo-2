#!/usr/bin/env bash
# 로봇(Pi) 한 방 기동 — 주행 스택 + 앞/뒤 카메라 송출 + 추종 cmd 브리지.
# 창을 네 개 열어 손으로 치던 걸 하나로 묶는다.
#
#   ./pi-all.sh --robot Pinky-3                       # 앞캠만 (기본)
#   ./pi-all.sh --robot Pinky-3 --back 4              # 뒷캠 /dev/video4 도 함께
#   ./pi-all.sh --robot Pinky-3 --ai 192.168.1.10     # AI 서버 IP 직접 지정
#   ./pi-all.sh --robot Pinky-3 --no-fsm              # 모르는 플래그는 pi.sh 로 그대로 넘어간다
#
# 뒷캠 인덱스는 로봇마다 다르다. 목록:  v4l2-ctl --list-devices
#
# 정리: ./kill-pi.sh (같은 폴더)
#
# ## 무엇을 띄우나 (전부 tmux 세션 `pinky_pi` 안)
#
#   pi.sh 가 만드는 창들   hw · nav2 · fleet-link · fsm · led   (주행 스택)
#   cam-front             picam  → UDP:6001  추종 영상
#   cam-back              USB캠  → UDP:6003  길잡이 감시 영상  (--back 줬을 때만)
#   follow-drive          UDP:6002 → /cmd_vel  (cmd_bridge)
#
# 같은 세션에 얹는 이유: 정리 경로를 하나로 유지한다. 세션을 지우면 전부 같이 죽고,
# 창만 닫아 살아남는 경우까지 drive-pi/kill.sh 가 이름으로 다시 쓸어담는다.
#
# ## 뒷캠은 왜 fps 가 낮나 (BACK_FPS=10, 앞캠은 15)
#
# 앞캠 출력은 cmd_vel 이라 지연이 곧 주행 품질이지만, 뒷캠 출력은 Bool 하나다
# (`/libi/requester_visible` → BT `GuideExec`). "사람이 뒤에 있나"만 보면 되므로
# 낮춰도 되고, Pi 가 JPEG 인코딩을 두 벌 돌리는 부담이 그만큼 준다.
# 더 줄이려면:  BACK_FPS=5 ./pi-all.sh --robot Pinky-3
#
# ## 카메라 인자를 왜 env 로 주나
#
# scripts/drive-pi/image-sender.sh 는 `$AI_IP` 하나만 안쪽으로 넘기고 나머지 인자를
# **버린다**(그 파일 마지막 줄). 안쪽 ros_ws/scripts/image-sender.sh 가 소스를 읽는 곳은
# `CAM_ARGS` env 다. 그래서 `--camera 0` 을 인자로 주면 조용히 무시되고 picam 이 뜬다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="pinky_pi"          # 안쪽 ros_ws/scripts/pi.sh:28 이 만드는 세션 이름
BACK_PORT="${BACK_PORT:-6003}"
BACK_FPS="${BACK_FPS:-10}"

ROBOT=""
AI_IP=""
# 뒷캠은 **명시할 때만** 뜬다(opt-in). 기본값을 두면 안 되는 이유는 아래 장치 검사 주석 참고 —
# 인덱스는 로봇마다 다르고, 틀린 값은 조용히 실패하지 않고 **앞캠을 죽인다.**
# laptop-all.sh 의 --back 과 같은 결이다.
WITH_BACK=false
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot)    ROBOT="${2:-}"; shift 2 ;;
    --ai)       AI_IP="${2:-}"; shift 2 ;;
    --back)     WITH_BACK=true; BACK_CAM="${2:?--back 뒤에 USB 캠 인덱스가 필요합니다 (예: --back 4).  목록: v4l2-ctl --list-devices}"; shift 2 ;;
    *)          ARGS+=("$1"); shift ;;     # --no-fsm 등은 pi.sh 로 그대로
  esac
done

ROBOT="${ROBOT:-${FSM_ROBOT_ID:-}}"
[ -n "$ROBOT" ] || die "로봇 이름이 필요합니다.  예: ./pi-all.sh --robot Pinky-3
  이름은 관제 DB(rc_robots.name)에 등록된 값과 정확히 같아야 합니다 — 다르면 fleet_node 가
  이 로봇을 못 알아보고 배차해도 움직이지 않습니다(scripts/drive-pi/pi.sh 머리말)."

AI_IP="${AI_IP:-${LAPTOP_IP:-}}"
[ -n "$AI_IP" ] || die "AI 서버 IP 가 필요합니다 — --ai 로 주거나 .env 의 LAPTOP_IP 를 채우세요."

# ── 뒷캠 장치 검사 ──────────────────────────────────────────────────────────
# ⚠️ 실제로 당한 사고(2026-07-27): `--back-cam 0` 으로 띄웠더니 **앞캠이 죽었다.**
#
#     /dev/video0[16:cap]: Unable to set format: Device or resource busy
#     RuntimeError: Failed to configure camera: Device or resource busy
#
# Pi 에서 /dev/video0 은 USB 웹캠이 아니라 **CSI 카메라(picam)의 unicam/ISP 캡처 노드**다.
# 뒷캠이 OpenCV 로 그걸 열면 앞캠의 libcamera 가 장치를 못 잡고 죽는다. 그러면 추종 영상이
# 통째로 안 나가는데, 죽는 건 앞캠 창이라 원인이 뒷캠이라는 게 안 보인다.
#
# 그래서 띄우기 전에 막는다. 장치 이름은 /sys 에서 읽으므로 v4l2-utils 가 없어도 된다.
list_cams() {
  local f n
  for f in /sys/class/video4linux/video*/name; do
    [ -r "$f" ] || continue
    n="$(basename "$(dirname "$f")")"
    echo "    /dev/$n  $(cat "$f")"
  done
}
if [ "$WITH_BACK" = true ]; then
  [ -e "/dev/video$BACK_CAM" ] || die "/dev/video$BACK_CAM 이 없습니다.
  이 로봇의 영상 장치:
$(list_cams)
  USB 캠 인덱스를 골라 주세요:  ./pi-all.sh --robot $ROBOT --back <n>"

  BACK_NAME=""
  [ -r "/sys/class/video4linux/video$BACK_CAM/name" ] \
    && BACK_NAME="$(cat "/sys/class/video4linux/video$BACK_CAM/name")"
  case "$BACK_NAME" in
    *unicam*|*bcm2835*|*rp1-cfe*|*isp*|*pisp*)
      die "/dev/video$BACK_CAM 는 CSI 카메라(앞캠) 장치입니다 — '$BACK_NAME'
  여기에 뒷캠을 물리면 **앞캠(picam)이 'Device or resource busy' 로 죽어** 추종 영상이 끊깁니다.
  이 로봇의 영상 장치:
$(list_cams)
  USB 캠 인덱스를 골라 주세요:  ./pi-all.sh --robot $ROBOT --back <n>" ;;
  esac
  echo "[pi-all] 뒷캠 장치 확인: /dev/video$BACK_CAM  '${BACK_NAME:-이름 미상}'"
fi

# 이미 떠 있으면 창이 중복으로 쌓인다. 안쪽 pi.sh 도 같은 이유로 여기서 멈춘다.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 정리하세요:  $HERE/kill-pi.sh"
fi

echo "[pi-all] 로봇=$ROBOT  AI=$AI_IP  뒷캠=$([ "$WITH_BACK" = true ] && echo "/dev/video$BACK_CAM → UDP:$BACK_PORT @${BACK_FPS}fps" || echo "없음(--back <n> 으로 켬)")"

# ── 1) 주행 스택 ────────────────────────────────────────────────────────────
# ⚠️ 파이프를 태우는 게 핵심이다. 안쪽 pi.sh 는 `[ -t 1 ]` 이면 세션에 **attach 해서
#    돌아오지 않는다**(그 파일 마지막). 그러면 아래 카메라 창들이 영영 안 뜬다.
#    stdout 을 파이프로 만들면 attach 를 건너뛰고 안내만 찍고 끝난다.
"$REPO_ROOT/scripts/drive-pi/pi.sh" --robot "$ROBOT" "${ARGS[@]}" 2>&1 | sed 's/^/[pi] /'

# 세션이 실제로 생겼는지 확인하고 나서 창을 얹는다. ensure_built 가 colcon build 를
# 도는 경우가 있어 시간이 걸릴 수 있다.
for _ in $(seq 1 20); do
  tmux has-session -t "$SESSION" 2>/dev/null && break
  sleep 0.5
done
tmux has-session -t "$SESSION" 2>/dev/null \
  || die "'$SESSION' 세션이 뜨지 않았습니다 — 위 [pi] 출력을 확인하세요."

# ── 2) 카메라 송출 ──────────────────────────────────────────────────────────
tmux new-window -t "$SESSION" -n cam-front \
  bash -c "cd '$REPO_ROOT' && echo '[cam-front] picam → $AI_IP:6001 (추종)' && ./scripts/drive-pi/image-sender.sh '$AI_IP'; exec bash"

if [ "$WITH_BACK" = true ]; then
  tmux new-window -t "$SESSION" -n cam-back \
    bash -c "cd '$REPO_ROOT' && echo '[cam-back] /dev/video$BACK_CAM → $AI_IP:$BACK_PORT @${BACK_FPS}fps (길잡이 감시)' && CAM_ARGS='--camera $BACK_CAM' VIDEO_PORT='$BACK_PORT' FPS='$BACK_FPS' ./scripts/drive-pi/image-sender.sh '$AI_IP'; exec bash"
fi

# ── 3) 추종 명령 수신 ───────────────────────────────────────────────────────
tmux new-window -t "$SESSION" -n follow-drive \
  bash -c "cd '$REPO_ROOT' && echo '[follow-drive] UDP:6002 → /cmd_vel' && ./scripts/drive-pi/follow-drive.sh; exec bash"

echo "[pi-all] 정리: $HERE/kill-pi.sh"

# 세션에 붙여서 끝낸다 — 창을 눈으로 보며 디버깅하는 게 목적이다.
# (비-TTY 면 tmux_attach 가 안내만 찍고 넘어간다 — _common.sh)
#
# 창 이동: Ctrl+b 0..8   ·  분리: Ctrl+b d
# 처음 볼 곳은 fleet-link 다 — ros_bridge 가 죽으면 여기에만 사유가 찍히고,
# 그게 죽으면 모든 goal 이 "ROS 브리지가 활성화되지 않았습니다" 로 실패한다.
tmux select-window -t "$SESSION:fleet-link" 2>/dev/null || true
tmux_attach "$SESSION"
