#!/usr/bin/env bash
# 노트북 한 방 기동 — 관제 ROS + 관제 UI + 도서관 웹 + AI 추종 서버 + 터치패널.
#
#   ./laptop-all.sh --robot Pinky-3 --domain-id 119
#   ./laptop-all.sh                                  # 빠진 값은 물어본다(대화형일 때)
#   ./laptop-all.sh --robot Pinky-3 --domain-id 119 --no-gui   # 패널 빼고
#   ./laptop-all.sh --robot Pinky-3 --domain-id 119 --no-ai    # AI 서버 빼고
#
# 정리: ./kill-laptop.sh (같은 폴더)
#
# ## ⚠️ 순서가 중요하다 — 아무 순서로나 띄우면 안 된다
#
# 근거가 코드에 있다:
#
#   1. MariaDB 가 먼저다.
#      · fms_service.sh 의 ros-domain-bridge.sh 가 DB(rc_robots)를 읽어 로봇별 브릿지를 만든다
#      · library.sh 는 스스로 ensure_mariadb 를 하지만 fms_service.sh 는 **안 한다**
#      그래서 여기서 먼저 살린다. 안 그러면 브릿지가 로봇 0대로 떠서 아무것도 안 넘어온다.
#
#   2. libi_gui.sh 는 **맨 마지막**이다.
#      그 스크립트가 뜨기 전에 FMS_URL·ABA_SERVICE_URL 을 curl 로 찔러보고, 안 열려 있으면
#      **그 자리에서 죽는다**(ui/libi_gui.sh 의 check_reachable). 즉 :9001 과 :8000 이
#      이미 떠 있어야 한다. 그래서 아래에서 두 포트가 열릴 때까지 기다렸다 띄운다.
#      (이 체크는 일부러 있는 것이다 — 없으면 GUI 는 뜨는데 매 클릭마다 "Operation
#       canceled" 만 반복해 원인을 찾기 어렵다.)
#
#   3. :9001 은 ui/fms.sh 가, :8000 은 ui/library.sh 가 띄운다.
#      둘 다 이미 떠 있으면 재사용하므로 두 번 띄울 걱정은 없다.
#
#   4. AI 서버는 GUI 보다 앞이면 된다. libi_gui 는 PERCEPTION_URL 은 **검사하지 않아서**
#      늦게 떠도 GUI 자체는 뜨지만, 그러면 추종 화면만 까맣게 남는다.
#
#   ROS 쪽(fms_service.sh)과 백엔드(:9001)는 서로 순서를 안 탄다 — DDS pub/sub 이라
#   누가 먼저 떠도 붙는다. DB 뒤이기만 하면 된다.
#
# ## 세션 배치
#
#   libi_fms      fms_service.sh  — bridge / fleet-node / adapters   (ROS, 도메인 86)
#   libi_ui_fms   fms.sh          — urls / frontend(:9002) / api(:9001 로그)
#   libi_ui_lib   library.sh      — urls / backend(:8000) / frontend(:3000)
#   libi_laptop   여기서 만듦     — ai(:5007) / gui(터치패널)
#
# ai·gui 를 별도 세션에 두는 이유: 둘 다 포그라운드로 exec 하는 스크립트라
# (ai_follower_service.sh → perception_server, libi_gui.sh → Qt 바이너리) 그냥 부르면
# 거기서 스크립트가 멈춘다. 창에 넣어야 다음 단계로 넘어간다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="libi_laptop"
#: :9001 / :8000 이 열릴 때까지 기다리는 상한(초). vite·uvicorn 첫 기동이 느릴 수 있다.
PORT_WAIT_SEC="${PORT_WAIT_SEC:-90}"

ROBOT=""
DOMAIN_ID=""
WITH_AI=true
WITH_GUI=true
while [ $# -gt 0 ]; do
  case "$1" in
    --robot)     ROBOT="${2:-}"; shift 2 ;;
    --domain-id) DOMAIN_ID="${2:-}"; shift 2 ;;
    --no-ai)     WITH_AI=false; shift ;;
    --no-gui)    WITH_GUI=false; shift ;;
    *) die "모르는 인자: $1
  사용법: ./laptop-all.sh --robot <이름> --domain-id <n> [--no-ai] [--no-gui]" ;;
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

ROBOT="${ROBOT:-${FSM_ROBOT_ID:-}}"
if [ -z "$ROBOT" ]; then
  ROBOT="$(ask "로봇 이름 (DB rc_robots.name, 예: Pinky-3): " \
    "--robot 이 없습니다. 관제 DB(rc_robots.name)에 등록된 이름을 주세요 — 예: --robot Pinky-3")"
fi
[ -n "$ROBOT" ] || die "로봇 이름이 비어 있습니다."

if [ -z "$DOMAIN_ID" ]; then
  DOMAIN_ID="$(ask "ROS_DOMAIN_ID (이 로봇의 도메인, 예: 119): " \
    "--domain-id 가 없습니다. pi.sh 를 띄운 셸의 ROS_DOMAIN_ID 와 같은 값을 주세요 — 예: --domain-id 119")"
fi
case "$DOMAIN_ID" in
  ''|*[!0-9]*) die "--domain-id 는 숫자여야 합니다: '$DOMAIN_ID'" ;;
esac

[ -n "${LAPTOP_IP:-}" ] || die "LAPTOP_IP 가 .env 에 없습니다 (이 머신 IP — 패널이 붙을 주소)."

if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 정리하세요:  $HERE/kill-laptop.sh"
fi

echo "[laptop-all] 로봇=$ROBOT  도메인=$DOMAIN_ID  LAPTOP_IP=$LAPTOP_IP"
echo "[laptop-all] AI=$WITH_AI  GUI=$WITH_GUI"

# ── 0) DB — 나머지 전부의 전제 ──────────────────────────────────────────────
ensure_mariadb

# ── 1) 관제 ROS (bridge / fleet_node / adapters) ────────────────────────────
# ⚠️ 파이프를 태운다. 이 스크립트들은 끝에서 tmux_attach 를 부르는데, TTY 면 세션에
#    붙어서 **돌아오지 않는다.** stdout 이 파이프면 안내만 찍고 반환한다(_common.sh).
echo "[laptop-all] ── 관제 ROS (libi_fms)"
"$REPO_ROOT/scripts/laptop/fms_service.sh" 2>&1 | sed 's/^/[fms-ros] /'

# ── 2) 관제 UI — 백엔드(:9001) 포함 ────────────────────────────────────────
echo "[laptop-all] ── 관제 UI (libi_ui_fms, :9001 + :9002)"
"$REPO_ROOT/scripts/ui/fms.sh" 2>&1 | sed 's/^/[fms-ui] /'

# ── 3) 도서관 웹 — 백엔드(:8000) 포함 ──────────────────────────────────────
echo "[laptop-all] ── 도서관 웹 (libi_ui_lib, :8000 + :3000)"
"$REPO_ROOT/scripts/ui/library.sh" 2>&1 | sed 's/^/[library] /'

# ── 4) AI 추종 서버 ────────────────────────────────────────────────────────
tmux new-session -d -s "$SESSION" -n urls \
  bash -c "printf '%s\n' '노트북 스택
  관제 콘솔   http://localhost:9002/
  도서관 웹   http://localhost:3000/        사서 /admin
  FMS API     http://localhost:9001/
  추종 뷰어   $LAPTOP_IP:5007  (패널 PERCEPTION_URL)'; exec bash"

if [ "$WITH_AI" = true ]; then
  echo "[laptop-all] ── AI 추종 서버 (:5007 → $ROBOT)"
  tmux new-window -t "$SESSION" -n ai \
    bash -c "cd '$REPO_ROOT' && ./scripts/laptop/ai_follower_service.sh '$ROBOT'; exec bash"
fi

# ── 5) 터치패널 — 맨 마지막 ────────────────────────────────────────────────
if [ "$WITH_GUI" = true ]; then
  # libi_gui.sh 는 두 포트가 안 열려 있으면 죽는다. 열릴 때까지 기다린다.
  echo "[laptop-all] :9001 / :8000 열릴 때까지 대기 (최대 ${PORT_WAIT_SEC}s)..."
  waited=0
  while :; do
    port_open 9001 && port_open 8000 && break
    [ "$waited" -ge "$PORT_WAIT_SEC" ] && die "포트가 안 열렸습니다 — :9001=$(port_open 9001 && echo ok || echo 닫힘) :8000=$(port_open 8000 && echo ok || echo 닫힘)
  로그: tmux attach -t libi_ui_fms  /  tmux attach -t libi_ui_lib"
    sleep 2; waited=$((waited + 2))
  done
  echo "[laptop-all] ── 터치패널 ($ROBOT, domain $DOMAIN_ID)"
  tmux new-window -t "$SESSION" -n gui \
    bash -c "cd '$REPO_ROOT' && ./scripts/ui/libi_gui.sh '$ROBOT' --domain-id '$DOMAIN_ID'; exec bash"
fi

echo "[laptop-all] 완료."
echo "  세션:  libi_fms · libi_ui_fms · libi_ui_lib · $SESSION"
echo "  붙기:  tmux attach -t $SESSION"
echo "  정리:  $HERE/kill-laptop.sh"
