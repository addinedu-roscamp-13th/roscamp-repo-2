#!/usr/bin/env bash
# 서버(노트북) 한 방 기동 — **로봇과 무관한 것만.** 로봇이 몇 대든 이건 한 번만 뜬다.
#
#   ./libi_server.sh --domain-id 111
#   ./libi_server.sh --domain-id 111 --no-web   # 도서관 웹(:8000/:3000) 빼고
#
# **`--domain-id` 는 필수다.** 기본값을 두지 않는다 — 이유는 아래 파서 주석.
#
# ## 왜 로봇별 스크립트와 갈랐나
#
# 예전 `laptop-all.sh`(지금은 없다)는 서버 것과 로봇별 것을 한 세션(`libi_laptop`)에 섞어 놨다.
# 그래서 **두 번째 로봇을 띄우면 "세션이 이미 떠 있습니다" 로 죽었다** — 다중 제어가
# 구조적으로 불가능했다. 여기 있는 것들은 전부 DB(rc_robots) 기준이라 대수와 무관하다:
#
#   · 도메인 브릿지 / 상태 어댑터 — 등록된 로봇 **전부**를 훑는다(robot-link.sh --all)
#   · fleet_node                  — 서버 도메인(--domain-id) 하나에서 전 로봇 배차
#   · FMS 백엔드(:9001)           — fleet_telemetry 가 전 로봇 상태를 캐시
#
# 로봇별(추종 AI·터치패널)은 ./libi_laptop.sh --robot <이름> 으로 따로, 로봇 수만큼 띄운다.
#
# ## 순서가 중요하다
#
#   1. MariaDB 가 먼저다 — fms_service.sh 의 ros-domain-bridge.sh 가 DB 를 읽어 브릿지를
#      만든다. 없으면 **로봇 0대**로 떠서 아무것도 안 넘어온다.
#   2. 나머지는 서로 순서를 안 탄다(ROS pub/sub 이라 누가 먼저 떠도 붙는다).
#
# ## 세션 배치 (이 스크립트는 자기 세션을 안 만든다)
#
#   libi_fms      fms_service.sh  — bridge / fleet-node / adapters   (ROS, --domain-id 값)
#   libi_ui_fms   ui/fms.sh       — urls / frontend(:9002) / api(:9001)
#   libi_ui_lib   ui/library.sh   — urls / backend(:8000) / frontend(:3000)
#
# 정리: ./kill-libi_server.sh (여기서 띄운 것만)  ·  ./kill-libi_laptop.sh (로봇 쪽)
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WITH_WEB=true

# ── 서버(관제) 도메인 ────────────────────────────────────────────────────────
# [2026-07-30] **인자로만 받는다. 기본값 없음.**
#
# 왜: 예전엔 아무도 서버 도메인을 명시하지 않았고, 이 PC 의 모든 프로세스가 루트
# `.env:67` 의 `ROS_DOMAIN_ID=119` — **실물 로봇 pinky-3 한 대의 값** — 를 물려받았다.
# FMS 백엔드의 ros_bridge 가 도메인을 안 정하고 `rclpy.init()` 을 불러 그 119 를 그대로
# 탔고, 결과적으로 **서버 노드가 로봇 도메인에 올라가 `/cmd_vel` 을 직접 발행**했다.
# 로봇 twist_mux 중재를 우회하는 두 번째 발행자였고, 로봇 쪽에서는 `/cmd_vel` 발행자가
# 2개로 보였다(pinky_bringup 이 이제 30초마다 GID 와 함께 ERROR 로 찍는다).
#
# 값 하나를 여기서 정해 아래 전부에 흘려보낸다:
#   · FMS 백엔드      app/ros_domains.py (LIBI_SERVER_DOMAIN_ID)
#   · fleet_node      scripts/laptop/fms_service.sh
#   · 상태 어댑터     scripts/laptop/robot-link.sh
#   · domain_bridge   aba_fms_service/config/**.yaml 의 `to_domain` ← ⚠️ 여기는 파일이라
#                     인자로 안 따라온다. 도메인을 바꾸면 그 yaml 도 같이 고쳐야 한다.
#
# ⚠️ 로봇 도메인(117~119)과 겹치면 안 된다 — 겹치는 순간 위 사고가 그대로 재현된다.
#
# ## `--domain-id` 는 **필수**다 — 기본값도, 환경변수 폴백도 없다
#
# 로봇 런처(libi_laptop.sh:8)가 이미 같은 규칙이다. 이번 사고가 조용했던 이유가 정확히
# "아무도 도메인을 말하지 않아도 뜨는 것"이었다 — 안 적으면 셸 값(로봇 것)이 대신 들어왔고,
# 그게 틀렸다는 신호가 어디에도 없었다. **말하지 않으면 안 뜨게** 만든다.
#
# ⚠️ `LIBI_SERVER_DOMAIN_ID` 가 환경에 있어도 **안 쓴다.** 폴백을 허용하면 이 스크립트를
#    같은 셸에서 두 번째로 돌릴 때(첫 실행이 export 해 둔다) 다시 조용히 통과한다 —
#    없애려던 실패 방식이 그대로 돌아온다. 값은 아래에서 export 해 하위로만 흘려보낸다.
SERVER_DOMAIN=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-web) WITH_WEB=false; shift ;;
    # need_arg 는 libi_pi.sh 로컬 헬퍼라 여기선 못 쓴다(_common.sh 에 없다).
    --domain-id) [ -n "${2:-}" ] || die "--domain-id 뒤에 값이 필요합니다 (예: --domain-id 111)"
                 SERVER_DOMAIN="$2"; shift 2 ;;
    *) die "모르는 인자: $1
  사용법: ./libi_server.sh --domain-id N [--no-web]   (--domain-id 는 필수)" ;;
  esac
done

[ -n "$SERVER_DOMAIN" ] || die "--domain-id 가 필요합니다.

  ./libi_server.sh --domain-id 111        ← 지금 쓰는 관제 도메인

  기본값을 일부러 두지 않았습니다. 2026-07-30 에 서버가 도메인을 말하지 않고 떠서
  셸의 ROS_DOMAIN_ID(=119, 실물 로봇 pinky-3 값)를 물려받았고, 관제 노드가 로봇
  도메인에 올라가 /cmd_vel 을 직접 발행했습니다(twist_mux 중재 우회).
  로봇 런처(libi_laptop.sh)도 같은 이유로 --domain-id 가 필수입니다."
case "$SERVER_DOMAIN" in
  *[!0-9]*) die "--domain-id 는 숫자여야 합니다: $SERVER_DOMAIN" ;;
esac
if [ "$SERVER_DOMAIN" -ge 117 ] && [ "$SERVER_DOMAIN" -le 119 ]; then
  die "--domain-id $SERVER_DOMAIN 은 로봇 도메인대(117~119)입니다.
  서버가 로봇 도메인에 올라가면 관제 노드가 로봇 /cmd_vel 에 직접 끼어듭니다
  (2026-07-30 실제 사고 — aba_fms_service/backend/app/ros_domains.py 참고)."
fi
export LIBI_SERVER_DOMAIN_ID="$SERVER_DOMAIN"

cd "$REPO_ROOT"

# LAPTOP_IP 는 **여기서** 막는다. 없으면 이 스크립트는 끝까지 잘 뜨고, 한참 뒤에
# libi_laptop.sh 가 죽는다 — 원인이 서버 쪽 .env 라는 게 그때는 안 보인다.
[ -n "${LAPTOP_IP:-}" ] || die "LAPTOP_IP 가 .env 에 없습니다 (이 머신 IP — 패널·로봇이 붙을 주소).
  로봇 런처(libi_laptop.sh)가 이 값을 쓰므로 지금 막습니다."

echo "[libi_server] LAPTOP_IP=$LAPTOP_IP  도서관웹=$WITH_WEB"
# --no-web 이면 :8000 이 안 뜬다. 로봇 런처의 터치패널은 그 포트를 90초 기다렸다 죽으므로
# 짝이 되는 플래그를 여기서 알려 준다(그때 가서 알면 90초를 버린 뒤다).
[ "$WITH_WEB" = true ] || echo "[libi_server] ⚠ --no-web: :8000 이 안 뜹니다 → 로봇 런처는 ./libi_laptop.sh ... --no-gui 로 띄우세요"

# ── 0) DB — 나머지 전부의 전제 ──────────────────────────────────────────────
ensure_mariadb

# ── 1) 관제 ROS (bridge / fleet_node / adapters) ────────────────────────────
# ⚠️ 파이프를 태운다. 이 스크립트들은 끝에서 tmux_attach 를 부르는데, TTY 면 세션에
#    붙어서 **돌아오지 않는다.** stdout 이 파이프면 안내만 찍고 반환한다(_common.sh).
#
# 여기서 .env 의 PINKY{N}_IP 가 rc_robots 에 반영되고(gen_domain_bridges.py), 그 DB 를
# 기준으로 로봇 수만큼 브릿지·어댑터가 뜬다. 로봇을 늘려도 이 줄은 그대로다.
echo "[libi_server] ── 관제 ROS (libi_fms)"
"$REPO_ROOT/scripts/laptop/fms_service.sh" 2>&1 | sed 's/^/[fms-ros] /'

# ── 2) 관제 UI — 백엔드(:9001) 포함 ────────────────────────────────────────
echo "[libi_server] ── 관제 UI (libi_ui_fms, :9001 + :9002)"
"$REPO_ROOT/scripts/ui/fms.sh" 2>&1 | sed 's/^/[fms-ui] /'

# ── 3) 도서관 웹 — 백엔드(:8000) 포함 ──────────────────────────────────────
if [ "$WITH_WEB" = true ]; then
  echo "[libi_server] ── 도서관 웹 (libi_ui_lib, :8000 + :3000)"
  "$REPO_ROOT/scripts/ui/library.sh" 2>&1 | sed 's/^/[library] /'
fi

# ── 4) 각 tmux 창이 실제로 무엇을 찍었는지 보여준다 ─────────────────────────
#
# ⚠️ 이 스크립트는 하위 스크립트를 `| sed` 파이프에 태운다(위 주석). 그래서
#    `tmux_attach` 가 stdout 을 TTY 로 안 보고 **안 붙는다** — 안내 한 줄만 찍는다.
#    결과적으로 세션 안에서 벌어진 일이 통째로 안 보인다.
#
#    실측 2026-08-02: `fleet_node` 가 `[WARN] 로봇 상태 끊김(10s 이상): pinky-3` 을
#    수십 번 찍고 있었는데 화면에는 "기동 완료" 만 떴다. 기동이 성공한 것과 그 안이
#    멀쩡한 것은 다르다. 붙지 않아도 **마지막 몇 줄은 보여야** 한다.
#
# 이 요약은 **붙기 전에** 찍는다. 아래 5) 가 `exec tmux attach` 로 세션에 붙어 버리면
# 그 뒤 줄은 화면에 안 남으므로, 순서가 뒤집히면 안 된다.
# ⚠️ **`|| true` 가 전부 필요하다. 이 스크립트는 `set -eo pipefail` 이다.**
#
#   실측 2026-08-02: `grep -vE '^\s*$'` 가 **빈 창**(방금 떠서 아직 아무것도 안 찍은
#   창)에서 매칭 0건이라 **종료코드 1** 을 낸다. `pipefail` 이 그걸 파이프라인 실패로
#   올리고 `set -e` 가 스크립트를 거기서 죽인다. 결과: 첫 창 몇 줄만 찍히고
#   **"기동 완료" 도 tmux attach 도 영영 실행되지 않았다.** 에러 메시지 한 줄 없이.
#   (사용자 실측: 요약 두 줄 뒤 곧바로 프롬프트로 돌아왔다.)
#
#   `[ "$X" -gt 0 ]` 도 X 가 숫자가 아니면 실패하므로 같이 막는다.
SERVER_TAIL="${SERVER_TAIL:-8}"
if { [ "$SERVER_TAIL" -gt 0 ] 2>/dev/null || false; } && command -v tmux >/dev/null 2>&1; then
  echo
  echo "[libi_server] ── 각 창의 마지막 ${SERVER_TAIL}줄"
  for w in $(tmux list-windows -a -F '#{session_name}:#{window_index} #{session_name}' 2>/dev/null \
             | awk '$2 ~ /^libi_/ {print $1}' || true); do
    body="$(tmux capture-pane -p -t "$w" 2>/dev/null | grep -vE '^\s*$' | tail -n "$SERVER_TAIL" || true)"
    [ -n "$body" ] || continue
    echo "  ┌─ $w"
    printf '%s\n' "$body" | sed 's/^/  │ /' || true
  done
  echo "  └─"
  echo "  (이 요약을 끄려면 SERVER_TAIL=0 ./libi_server.sh)"
fi

cat <<EOF

[libi_server] 서버 스택 기동 완료
  관제 콘솔   http://localhost:9002/
  FMS API     http://localhost:9001/
$([ "$WITH_WEB" = true ] && echo "  도서관 웹   http://localhost:3000/        사서 /admin")

  로봇별(추종 AI·패널):  $HERE/libi_laptop.sh --robot <이름>
  정리:                  $HERE/kill-libi_server.sh   (로봇 쪽은 $HERE/kill-libi_laptop.sh)
EOF

# ── 5) tmux 에 **실제로 붙는다** ────────────────────────────────────────────
#
# ⚠️ 하위 스크립트들의 `tmux_attach` 는 여기서 절대 안 붙는다 — 이 스크립트가 그들을
#    `| sed` 파이프에 태워 stdout 이 TTY 가 아니기 때문이다(위 1)~3) 주석). 그래서
#    "[bg] … 붙으려면 tmux attach -t …" 안내만 찍히고 끝났다.
#
#    그 안내를 읽고 사람이 손으로 붙어야 했는데, 실제로는 아무도 안 붙어서 창 안의
#    경고가 통째로 안 보였다(실측 2026-08-02: fleet_node 가 `로봇 상태 끊김` 을
#    수십 번 찍는 동안 화면엔 "기동 완료" 만 떴다).
#
# 그래서 마지막에 이 스크립트가 직접 붙는다. 여기 stdout 은 파이프가 아니라 사용자의
# 터미널이므로 `[ -t 1 ]` 이 참이다.
#
# ⚠️ 여러 세션을 하나로 합치지 않는다. `tmux link-window` 로 모으는 방법이 있지만,
#    링크는 **원본과 같은 창을 가리키므로** 합친 세션을 정리할 때 실수로 원본까지
#    닫을 위험이 있다. 세션 전환은 tmux 가 이미 제공한다(`Ctrl-b s`).
#
# 붙을 세션: 기본 libi_fms(배차·교통 — fleet_node 가 여기 있다).
# 다른 세션을 보고 싶으면 SERVER_ATTACH=libi_ui_fms 처럼 준다. 안 붙으려면 =none.
SERVER_ATTACH="${SERVER_ATTACH:-libi_fms}"
if [ "$SERVER_ATTACH" != "none" ] && command -v tmux >/dev/null 2>&1 \
   && tmux has-session -t "$SERVER_ATTACH" 2>/dev/null; then
  if [ -t 1 ]; then
    echo
    echo "[libi_server] tmux '$SERVER_ATTACH' 에 붙습니다."
    echo "              세션 전환 Ctrl-b s · 창 전환 Ctrl-b n/p · 떼기 Ctrl-b d"
    echo "              (안 붙으려면 SERVER_ATTACH=none $0 ...)"
    exec tmux attach -t "$SERVER_ATTACH"
  else
    echo "[libi_server] 비-TTY 실행이라 안 붙습니다: tmux attach -t $SERVER_ATTACH"
  fi
fi
