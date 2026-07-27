#!/usr/bin/env bash
# 로봇(Pi) 한 방 기동 — 주행 스택 + 앞/뒤 카메라 송출 + 추종 cmd 브리지.
# 창을 네 개 열어 손으로 치던 걸 하나로 묶는다.
#
#   ./pi-all.sh --robot Pinky-3
#   ./pi-all.sh --robot Pinky-3 --ai 192.168.1.10     # AI 서버 IP 직접 지정
#   ./pi-all.sh --robot Pinky-3 --no-back             # 뒷캠 없이 (앞캠만)
#   ./pi-all.sh --robot Pinky-3 --back-cam 1          # 뒷캠 /dev/video1
#   ./pi-all.sh --robot Pinky-3 --no-fsm              # 모르는 플래그는 pi.sh 로 그대로 넘어간다
#
# 정리: ./kill-pi.sh (같은 폴더)
#
# ## 무엇을 띄우나 (전부 tmux 세션 `pinky_pi` 안)
#
#   pi.sh 가 만드는 창들   hw · nav2 · fleet-link · fsm · led   (주행 스택)
#   cam-front             picam  → UDP:6001  추종 영상
#   cam-back              USB캠  → UDP:6003  길잡이 감시 영상
#   follow-drive          UDP:6002 → /cmd_vel  (cmd_bridge)
#
# 같은 세션에 얹는 이유: 정리 경로를 하나로 유지한다. 세션을 지우면 전부 같이 죽고,
# 창만 닫아 살아남는 경우까지 drive-pi/kill.sh 가 이름으로 다시 쓸어담는다.
#
# ## 뒷캠은 왜 fps 를 낮추나
#
# 앞캠 출력은 cmd_vel 이라 지연이 곧 주행 품질이지만, 뒷캠 출력은 Bool 하나다
# (`/libi/requester_visible` → BT `GuideExec`). "사람이 뒤에 있나"만 보면 되므로
# 앞캠(15fps)보다 낮춰도 되고, Pi 가 JPEG 인코딩을 두 벌 돌리는 부담이 그만큼 준다.
# CPU 가 기준을 넘으면 BACK_FPS 를 먼저 내려라 — 여기가 제일 싸게 버는 자리다.
#
# ## 카메라 인자를 왜 env 로 주나
#
# scripts/drive-pi/image-sender.sh 는 `$AI_IP` 하나만 안쪽으로 넘기고 나머지 인자를
# **버린다**(그 파일 마지막 줄). 안쪽 ros_ws/scripts/image-sender.sh 가 소스를 읽는 곳은
# `CAM_ARGS` env 다. 그래서 `--camera 0` 을 인자로 주면 조용히 무시되고 picam 이 뜬다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

SESSION="pinky_pi"          # 안쪽 ros_ws/scripts/pi.sh:28 이 만드는 세션 이름
BACK_PORT="${BACK_PORT:-6003}"
BACK_FPS="${BACK_FPS:-10}"
BACK_CAM="${BACK_CAM:-0}"
#: CPU 여유 기준. nav2(코스트맵·플래너)가 이미 무거워서, 카메라 두 벌을 얹고도 이 밑이면
#: 진행할 만하다고 본다. 넘으면 막지는 않고 경고만 한다 — 판단은 사람이 한다.
CPU_BUDGET_PCT="${CPU_BUDGET_PCT:-70}"

ROBOT=""
AI_IP=""
WITH_BACK=true
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot)    ROBOT="${2:-}"; shift 2 ;;
    --ai)       AI_IP="${2:-}"; shift 2 ;;
    --back-cam) BACK_CAM="${2:-}"; shift 2 ;;
    --no-back)  WITH_BACK=false; shift ;;
    *)          ARGS+=("$1"); shift ;;     # --no-fsm 등은 pi.sh 로 그대로
  esac
done

ROBOT="${ROBOT:-${FSM_ROBOT_ID:-}}"
[ -n "$ROBOT" ] || die "로봇 이름이 필요합니다.  예: ./pi-all.sh --robot Pinky-3
  이름은 관제 DB(rc_robots.name)에 등록된 값과 정확히 같아야 합니다 — 다르면 fleet_node 가
  이 로봇을 못 알아보고 배차해도 움직이지 않습니다(scripts/drive-pi/pi.sh 머리말)."

AI_IP="${AI_IP:-${LAPTOP_IP:-}}"
[ -n "$AI_IP" ] || die "AI 서버 IP 가 필요합니다 — --ai 로 주거나 .env 의 LAPTOP_IP 를 채우세요."

# 이미 떠 있으면 창이 중복으로 쌓인다. 안쪽 pi.sh 도 같은 이유로 여기서 멈춘다.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 정리하세요:  $(dirname "${BASH_SOURCE[0]}")/kill-pi.sh"
fi

echo "[pi-all] 로봇=$ROBOT  AI=$AI_IP  뒷캠=$([ "$WITH_BACK" = true ] && echo "/dev/video$BACK_CAM → UDP:$BACK_PORT @${BACK_FPS}fps" || echo "없음")"

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

# ── 4) CPU 여유 확인 ────────────────────────────────────────────────────────
# nav2 가 코스트맵을 돌리기 시작한 뒤를 봐야 의미가 있어서 잠깐 기다렸다 잰다.
# /proc/stat 델타라 top/mpstat 같은 외부 도구가 없어도 된다(Pi 최소 설치 대비).
_cpu_sample() { awk '/^cpu /{idle=$5+$6; tot=0; for(i=2;i<=NF;i++) tot+=$i; print tot, idle}' /proc/stat; }

echo "[pi-all] CPU 측정 중 (nav2 가 자리잡을 때까지 12초 대기)..."
sleep 12
read -r t0 i0 < <(_cpu_sample)
sleep 5
read -r t1 i1 < <(_cpu_sample)
BUSY="$(awk -v t0="$t0" -v i0="$i0" -v t1="$t1" -v i1="$i1" \
  'BEGIN{ dt=t1-t0; di=i1-i0; if (dt<=0) print -1; else printf "%.0f", (dt-di)*100/dt }')"

echo "[pi-all] CPU 사용률 ≈ ${BUSY}%  (코어 $(nproc)개, 기준 ${CPU_BUDGET_PCT}%)"
if [ "$BUSY" -ge 0 ] 2>/dev/null && [ "$BUSY" -gt "$CPU_BUDGET_PCT" ]; then
  echo "[pi-all] ⚠ 기준을 넘었습니다. 줄일 수 있는 것들:"
  echo "         · 뒷캠 fps:   BACK_FPS=3 ./pi-all.sh --robot $ROBOT"
  echo "         · 뒷캠 끄기:  ./pi-all.sh --robot $ROBOT --no-back"
  echo "         · 앞캠 fps:   FPS 는 image-sender.sh 가 읽습니다 (기본 15)"
fi

echo "[pi-all] 완료. 붙으려면: tmux attach -t $SESSION"
echo "[pi-all] 정리:          $(dirname "${BASH_SOURCE[0]}")/kill-pi.sh"
