#!/usr/bin/env bash
# 노트북에서 **로봇 한 대분**만 띄운다 — 추종 AI 서버 + 터치패널.
# 로봇 대수만큼 반복해서 부르면 된다(세션 이름이 로봇별이라 안 부딪힌다).
#
#   ./libi_laptop.sh --robot pinky-3 --domain-id 119
#   ./libi_laptop.sh --robot pinky-1 --domain-id 117 --no-gui   # 패널 없이 추종만
#   ./libi_laptop.sh --robot pinky-3 --domain-id 119 --window   # 패널을 이 PC 에 창으로
#   ./libi_laptop.sh --robot pinky-3 --domain-id 119 --secview   # 야간 감시 더미 뷰어까지
#
# ⚠️ `--secview` 는 **야간 보안 전용**이다. 인지 서버는 뷰어를 한 번에 하나만 받으므로,
#    이걸 켜면 **패널의 추종 화면이 검게 나온다.** 관리자 추종을 쓸 때는 켜지 않는다.
#
# `--window` 는 VNC 대신이다 — **둘 다는 안 된다**(Qt 플랫폼 플러그인이 하나뿐).
# 창을 켜면 :590x VNC 는 안 열리므로, 태블릿에서도 봐야 하면 붙이지 마라.
#
# **`--domain-id` 는 필수다.** 기본값을 두지 않는다 — 안 주면 DB 에 등록된 값을 알려주고 멈춘다.
#
# 서버 것(DB·FMS·브릿지·fleet_node·도서관 웹)은 여기 없다 — **./libi_server.sh 를 먼저** 띄운다.
# 로봇이 몇 대든 libi_server.sh 는 한 번, 이 스크립트는 로봇 수만큼:
#
#   ./libi_server.sh
#   ./libi_laptop.sh --robot pinky-1 --domain-id 117
#   ./libi_laptop.sh --robot pinky-2 --domain-id 118
#
# ## 포트는 로봇 번호에서 나온다
#
# perception_server 의 수신 포트는 노트북에 한 벌뿐이라, 로봇 2대를 같이 추종하면
# 두 번째가 bind 에서 죽는다. `robot_ports`(_common.sh)가 로봇마다 10 씩 띄운다:
#
#   pinky-1 → 영상 6001 / 뷰어 5007      pinky-3 → 영상 6021 / 뷰어 5027
#
# 로봇 쪽 libi_pi.sh 도 **같은 함수**로 계산하므로 서로 값을 넘길 필요가 없다.
#
# 정리: ./kill-libi_laptop.sh <이름>  (그 로봇만)  ·  ./kill-libi_laptop.sh  (로봇 전부)
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#: :9001 / :8000 이 열릴 때까지 기다리는 상한(초). vite·uvicorn 첫 기동이 느릴 수 있다.
PORT_WAIT_SEC="${PORT_WAIT_SEC:-90}"

# 값을 받는 플래그가 **다음 플래그를 값으로 먹지 않게** 막는다.
#   `--robot --no-gui`  →  ROBOT="--no-gui" 로 조용히 통과하던 자리 (codex 2차 지적)
need_arg() {   # <플래그> <값>
  case "${2:-}" in
    ''|--*) die "$1 뒤에 값이 필요합니다 (받은 값: '${2:-없음}')" ;;
  esac
  printf '%s' "$2"
}

ROBOT=""
DOMAIN_ID=""
WITH_AI=true
WITH_GUI=true
#: 패널을 이 PC 에 **창으로** 띄운다. 기본은 VNC(태블릿/로봇 패널이 붙는 쪽)이고,
#  Qt 플랫폼 플러그인은 한 번에 하나라 **둘 다는 안 된다** — 창을 켜면 VNC :590x 는 안 열린다.
WITH_WINDOW=false
#: 야간 감시 더미 뷰어(security_viewer.py)를 같이 띄운다. 기본은 꺼짐 — 켜면 인지 서버가
#  뷰어를 하나만 받으므로 패널의 추종 화면이 검게 나온다.
WITH_SECVIEW=false
#: AI 서버에 그대로 넘길 추가 인자. `--pose` 같은 스위치용이다.
AI_EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --robot)     ROBOT="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    --domain-id) DOMAIN_ID="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    --no-ai)     WITH_AI=false; shift ;;
    --no-gui)    WITH_GUI=false; shift ;;
    --window)    WITH_WINDOW=true; shift ;;
    --secview)   WITH_SECVIEW=true; shift ;;
    # 자세(pose) 판정을 **켠다**. [2026-07-30] 기본이 꺼짐으로 바뀌었다
    # (perception_server 의 `--pose` 옵트인). 꺼져 있으면 PostureGate 가 주행을 항상
    # 허용한다(posture_gate.py — "판정 소스가 아예 없다" 분기): Calibrating 정지도,
    # Unknown 멈칫도, Lying 정지도 없다. 즉 **쓰러진 사람에게도 다가간다.**
    # 켜면 그 판정이 돌아오는 대신 프레임당 2차 추론이 붙어 추종 주기가 느려진다.
    --pose)      AI_EXTRA="$AI_EXTRA --pose"; shift ;;
    *) die "모르는 인자: $1
  사용법: ./libi_laptop.sh --robot <이름> [--no-ai] [--no-gui] [--window] [--pose] [--secview] [--domain-id <n>]" ;;
  esac
done

# 빠진 값은 물어본다. 비대화형(nohup·cron·파이프)에서는 물어볼 수 없으므로 그냥 죽는다 —
# 조용히 기본값으로 뜨면 "안 적었는데 딴 로봇/딴 도메인으로 붙는" 사고가 난다
# (libi_gui.sh 가 --domain-id 를 env 가 아니라 인자로 강제하는 이유와 같다).
ask() {   # <프롬프트> <설명>
  local ans
  [ -t 0 ] || die "$2"
  read -rp "$1" ans
  printf '%s' "$ans"
}

# ⚠️ --robot 이 없으면 셸의 FSM_ROBOT_ID 를 쓴다. 그건 **다른 로봇 창에서 남은 값**일 수
#    있으므로 어디서 왔는지 밝힌다(비-TTY 에서는 물어볼 수도 없다).
if [ -z "$ROBOT" ] && [ -n "${FSM_ROBOT_ID:-}" ]; then
  ROBOT="$FSM_ROBOT_ID"
  echo "[libi_laptop] --robot 이 없어 셸의 FSM_ROBOT_ID='$ROBOT' 를 씁니다" >&2
fi
if [ -z "$ROBOT" ]; then
  ROBOT="$(ask "로봇 이름 (DB rc_robots.name, 예: pinky-3): " \
    "--robot 이 없습니다. 관제 DB(rc_robots.name)에 등록된 이름을 주세요 — 예: --robot pinky-3")"
fi
[ -n "$ROBOT" ] || die "로봇 이름이 비어 있습니다."

SESSION="libi_laptop_$(robot_key "$ROBOT")"

# ── 도메인 ────────────────────────────────────────────────────────────────
#
# 표가 코드에 박혀 있다(_common.sh `robot_domain`): pinky-1→117 · pinky-2→118 · pinky-3→119.
# IP 는 DHCP 라 자주 바뀌지만 **도메인은 배선처럼 고정**이라 매번 칠 이유가 없다.
#
# ⚠️ 예전에 없앴던 "기본값"과 다르다. 그건 `.env` 의 ROS_DOMAIN_ID(=119) **하나를 모든
#    로봇에 쓰는 것**이라 2대째부터 반드시 틀렸다. 이 표는 로봇마다 다른 값이라 그 실패가 없다.
#
# 우선순위: --domain-id  >  표(robot_domain)  >  DB(rc_robots)
DOMAIN_SRC="--domain-id"
if [ -z "$DOMAIN_ID" ]; then
  DOMAIN_ID="$(robot_domain "$ROBOT")"
  [ -n "$DOMAIN_ID" ] && DOMAIN_SRC="표(_common.sh robot_domain)"
fi
if [ -z "$DOMAIN_ID" ]; then
  # 표 밖 이름(pinky-sim-2 등) — DB 가 유일한 출처다.
  DOMAIN_ID="$(robot_domain_db "$ROBOT")"
  [ -n "$DOMAIN_ID" ] && DOMAIN_SRC="DB rc_robots"
fi
[ -n "$DOMAIN_ID" ] || die "'$ROBOT' 의 도메인을 알 수 없습니다.
  표(pinky-1/2/3)에도 없고 DB(rc_robots.domain_id)에도 없습니다.
  직접 주세요:  ./libi_laptop.sh --robot $ROBOT --domain-id <N>"

case "$DOMAIN_ID" in
  ''|*[!0-9]*) die "도메인은 숫자여야 합니다: '$DOMAIN_ID' (출처: $DOMAIN_SRC)" ;;
esac

# 준 값이 DB 와 다르면 **경고만** 한다(막지는 않는다 — 임시로 딴 도메인에 붙여 보는 진단이 있다).
# 브릿지는 DB 값으로 열리므로, 다르면 패널·추종이 안 붙는다. 그때 여기부터 의심하라고 찍는다.
_db_domain="${_db_domain:-$(robot_domain_db "$ROBOT")}"
if [ -n "$_db_domain" ] && [ "$_db_domain" != "$DOMAIN_ID" ]; then
  echo "[libi_laptop] ⚠ 도메인 불일치 — 인자=$DOMAIN_ID / DB(rc_robots)=$_db_domain" >&2
  echo "                도메인 브릿지는 **DB 값**으로 열립니다. 안 붙으면 여기부터 의심하세요." >&2
fi

[ -n "${LAPTOP_IP:-}" ] || die "LAPTOP_IP 가 .env 에 없습니다 (이 머신 IP — 패널이 붙을 주소)."

if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 정리하세요:  $HERE/kill-libi_laptop.sh $ROBOT"
fi

robot_ports "$ROBOT"

echo "[libi_laptop] 로봇=$ROBOT  도메인=$DOMAIN_ID ($DOMAIN_SRC)  세션=$SESSION"
echo "[libi_laptop] 포트: 영상 UDP:$VIDEO_PORT  뷰어 :$VIEWER_PORT"
echo "[libi_laptop] AI=$WITH_AI  GUI=$WITH_GUI"

tmux new-session -d -s "$SESSION" -n urls \
  bash -c "printf '%s\n' '$ROBOT (도메인 $DOMAIN_ID)
  추종 뷰어   $LAPTOP_IP:$VIEWER_PORT   (패널 PERCEPTION_URL)
  영상 수신   UDP:$VIDEO_PORT           (로봇 image-sender 가 같은 규칙으로 계산)
  관제 콘솔   http://localhost:9002/
  도서관 웹   http://localhost:3000/'; exec bash"

# 야간 감시 옵트인 — AI 서버에 --security 를 넘겨 SecurityRecorder/OpsSink 를 태운다.
[ "$WITH_SECVIEW" = true ] && AI_EXTRA="$AI_EXTRA --security --security-robot '$ROBOT'"

# ── 1) AI 추종 서버 ────────────────────────────────────────────────────────
if [ "$WITH_AI" = true ]; then
  echo "[libi_laptop] ── AI 추종 서버 (앞캠 UDP:$VIDEO_PORT → 뷰어 :$VIEWER_PORT → $ROBOT 주행)"
  # ⚠️ **ROS 를 소싱하고 로봇 도메인을 물려준다.** AI 서버는 원래 ROS 를 전혀 모른 채로
  #    돌았는데, 그러면 로봇이 내는 두 가지를 못 듣는다:
  #      · `/libi/camera_select` — 앞↔뒤 전환. 모르면 ByteTrack id 를 시점이 통째로 바뀐
  #        프레임에 이어 붙이고, 전환 직후 재학습 창(`REGISTRATION_LEARN_SEC`)도 안 열려
  #        뒷캠에서 owner 를 못 잡는다(노란 예측 박스만 나온다).
  #      · `/libi/perception_role` — 지금 추종인가 길잡이인가. 모르면 길잡이에서도 자세를
  #        재고 **골격을 JPEG 에 구워** 패널로 보낸다(패널은 그 그림을 못 끈다).
  #    레포 `.venv` 는 `include-system-site-packages = true` 라 소싱만 하면 rclpy 가 잡힌다.
  #    못 붙어도 죽지 않는다 — perception_server 가 경고만 찍고 예전대로 돈다.
  tmux new-window -t "$SESSION" -n ai \
    bash -c "cd '$REPO_ROOT' && [ -f /opt/ros/jazzy/setup.bash ] && . /opt/ros/jazzy/setup.bash; ROS_DOMAIN_ID='$DOMAIN_ID' VIDEO_PORT='$VIDEO_PORT' VIEWER_PORT='$VIEWER_PORT' ./scripts/laptop/ai_follower_service.sh '$ROBOT' --camera-topic /libi/camera_select$AI_EXTRA; exec bash"
fi

if [ "$WITH_SECVIEW" = true ]; then
  echo "[libi_laptop] ── 야간 감시 더미 뷰어 (뷰어 :$VIEWER_PORT)"
  echo "[libi_laptop]    ⚠️ 이게 붙어 있는 동안 패널 추종 화면은 검게 나옵니다"
  # 셸 루프로 감싼다 — 프로세스가 죽어도 되살아난다. 파이썬 안의 재접속 루프는
  # 프로세스가 살아 있을 때만 돌기 때문이다.
  tmux new-window -t "$SESSION" -n secview \
    bash -c "cd '$REPO_ROOT/aba_ai_service/follower_perception' && \
             while true; do \
               '$REPO_ROOT/.venv/bin/python' scripts/security_viewer.py \
                 --host '$LAPTOP_IP' --port '$VIEWER_PORT'; \
               sleep 1; \
             done; exec bash"
fi

# [2026-07-29] 뒷캠 **수신 창을 없앴다.** 받을 게 없었다.
#
# 로봇의 camera_sender 는 앞뒤를 **한 프로세스가 한 포트로** 보낸다(`--port` 하나뿐,
# camera_sender.py:170). 어느 캠을 내보낼지는 BT 가 `/libi/camera_select` 로 정한다.
# 즉 뒷캠 영상도 같은 UDP:$VIDEO_PORT 로 들어오고, 별도 포트(6003)로 쏘는 건
# demo/dual-cam-bench/bench.sh 뿐이다. 그런데 이 창은 6003 을 듣고 있었다 —
# **켜 봐야 검은 화면**이고, 원인은 화면에 안 드러난다. 그래서 없앤다.

# ── 2) 터치패널 — 맨 마지막 ────────────────────────────────────────────────
# libi_gui.sh 는 :9001 / :8000 이 안 열려 있으면 그 자리에서 죽는다(check_reachable).
# 그 둘은 libi_server.sh 가 띄운다 — 여기서는 열릴 때까지 기다리기만 한다.
if [ "$WITH_GUI" = true ]; then
  echo "[libi_laptop] :9001 / :8000 열릴 때까지 대기 (최대 ${PORT_WAIT_SEC}s)..."
  waited=0
  while :; do
    port_open 9001 && port_open 8000 && break
    [ "$waited" -ge "$PORT_WAIT_SEC" ] && die "포트가 안 열렸습니다 — :9001=$(port_open 9001 && echo ok || echo 닫힘) :8000=$(port_open 8000 && echo ok || echo 닫힘)
  서버 스택을 먼저 띄우세요:  $HERE/libi_server.sh
  로그: tmux attach -t libi_ui_fms  /  tmux attach -t libi_ui_lib"
    sleep 2; waited=$((waited + 2))
  done
  echo "[libi_laptop] ── 터치패널 ($ROBOT, domain $DOMAIN_ID, 뷰어 :$VIEWER_PORT, $([ "$WITH_WINDOW" = true ] && echo '이 PC 에 창' || echo 'VNC'))"
  # PERCEPTION_URL 을 여기서 준다 — libi_gui.sh 기본값은 5007 고정이라
  # 2대째 로봇의 패널이 1대째 화면을 보게 된다.
  GUI_FLAGS=""
  [ "$WITH_WINDOW" = true ] && GUI_FLAGS=" --window"
  tmux new-window -t "$SESSION" -n gui \
    bash -c "cd '$REPO_ROOT' && PERCEPTION_URL='$LAPTOP_IP:$VIEWER_PORT' ./scripts/ui/libi_gui.sh '$ROBOT' --domain-id '$DOMAIN_ID'$GUI_FLAGS; exec bash"
fi

echo "[libi_laptop] 세션: $SESSION"
echo "[libi_laptop] 정리: $HERE/kill-libi_laptop.sh $ROBOT"

tmux select-window -t "$SESSION:$([ "$WITH_GUI" = true ] && echo gui || echo urls)" 2>/dev/null || true
tmux_attach "$SESSION"
