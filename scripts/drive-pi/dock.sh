#!/usr/bin/env bash
# 로봇(Pi)에서 실행 — 주차 **전체 절차**를 한 번에 돌린다.
#
#   ./dock.sh          회전 → 테이프 탐색 → 라인 추종 → 벽 정지  (IR·초음파 기반)
#   ./dock.sh stop     즉시 중단 (모터를 물고 있으므로 이게 유일한 비상구다)
#
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ 운영에서는 이걸 칠 일이 없다
#
# 도킹은 **BT 가 한다.** `RETURNING` 브랜치의 `ReturnNavigation` 이 nav2 로 주차장까지
# 간 뒤 `/fleet_cmd{dock}` 을 내고, 로봇 쪽 `dock_runner` 가 같은 절차를 실행한다.
#
#       BT  →  /fleet_cmd{dock}  →  fleet_link  →  dock_runner  →  park_dock._park_loop
#
# 이 스크립트는 **그 경로를 안 거치고** HTTP 로 직접 부른다. 쓸 자리는 하나뿐이다:
# 주차를 처음 맞출 때, BT 가 얽히기 전에 주차만 따로 확인하는 것.
#
# 그래서 평소 검증은 **BT 경로로** 하는 게 낫다 — 실제로 도는 길을 시험하는 것이므로:
#
#       ros2 topic pub --once /fleet_cmd std_msgs/msg/String \
#         '{data: "{\"id\":\"d1\",\"action\":\"dock\",\"args\":{}}"}'
#
# 단계별로 쪼개서 보려면 → ./dock/ 폴더 (1-line · 2-rotate · 3-approach · search · status)
#   ⚠️ 주차는 **라인 트레이싱**이다. 아르코는 이 경로에 없다 — dock/README.md 참고.
#   처음 맞출 때는 전체를 한 번에 돌리지 말고 거기서 하나씩 볼 것. 한 번에 돌리면
#   테이프를 못 보는 건지, 각도가 틀린 건지, 벽 거리를 잘못 재는 건지 구분이 안 된다.
#
# ⚠️ robot_agent(FastAPI) 서버가 떠 있어야 한다 — pi.sh 는 안 띄운다:
#       cd ~/controller/drive/robot_agent && pm2 start ecosystem.config.js
# ─────────────────────────────────────────────────────────────────────────────
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/dock/_common.sh"

if [ "${1:-}" = "stop" ]; then
  echo "[dock] 중단"
  dock_post "park/stop" '{}'
  exit 0
fi

echo "[dock] 전체 절차 시작 — 회전 → 탐색 → 라인 추종 → 벽 정지"
echo "       중단: 다른 터미널에서  ./dock.sh stop"
dock_post "park/start"
