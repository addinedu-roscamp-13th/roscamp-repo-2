#!/usr/bin/env bash
# 서버(노트북) 한 방 기동 — **로봇과 무관한 것만.** 로봇이 몇 대든 이건 한 번만 뜬다.
#
#   ./libi_server.sh
#   ./libi_server.sh --no-web      # 도서관 웹(:8000/:3000) 빼고
#
# ## 왜 로봇별 스크립트와 갈랐나
#
# 예전 `laptop-all.sh`(지금은 없다)는 서버 것과 로봇별 것을 한 세션(`libi_laptop`)에 섞어 놨다.
# 그래서 **두 번째 로봇을 띄우면 "세션이 이미 떠 있습니다" 로 죽었다** — 다중 제어가
# 구조적으로 불가능했다. 여기 있는 것들은 전부 DB(rc_robots) 기준이라 대수와 무관하다:
#
#   · 도메인 브릿지 / 상태 어댑터 — 등록된 로봇 **전부**를 훑는다(robot-link.sh --all)
#   · fleet_node                  — 도메인 86 하나에서 전 로봇 배차
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
#   libi_fms      fms_service.sh  — bridge / fleet-node / adapters   (ROS, 도메인 86)
#   libi_ui_fms   ui/fms.sh       — urls / frontend(:9002) / api(:9001)
#   libi_ui_lib   ui/library.sh   — urls / backend(:8000) / frontend(:3000)
#
# 정리: ./kill-libi_server.sh (여기서 띄운 것만)  ·  ./kill-libi_laptop.sh (로봇 쪽)
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WITH_WEB=true
while [ $# -gt 0 ]; do
  case "$1" in
    --no-web) WITH_WEB=false; shift ;;
    *) die "모르는 인자: $1
  사용법: ./libi_server.sh [--no-web]" ;;
  esac
done

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

cat <<EOF

[libi_server] 서버 스택 기동 완료
  관제 콘솔   http://localhost:9002/
  FMS API     http://localhost:9001/
$([ "$WITH_WEB" = true ] && echo "  도서관 웹   http://localhost:3000/        사서 /admin")

  로봇별(추종 AI·패널):  $HERE/libi_laptop.sh --robot <이름>
  정리:                  $HERE/kill-libi_server.sh   (로봇 쪽은 $HERE/kill-libi_laptop.sh)
EOF
