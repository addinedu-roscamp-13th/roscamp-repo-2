#!/usr/bin/env bash
# 노트북 쪽 정리 — FMS 한 벌을 통째로 지운다.
#   1) 백엔드(:9001)
#   2) tmux 세션 libi_fms (fms_service.sh — fleet_node · 브릿지 · 어댑터 · 프론트)
#   3) 나머지(sim 세션 pinky_sim*, domain_bridge, launch 고아 노드)는 기존 kill.sh 에 위임
#
#   ./kill.sh                  전부 정리
#   ./kill.sh --keep-backend   백엔드만 남긴다 (프론트만 다시 띄울 때 등)
#
# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ [2026-07-22] 백엔드도 함께 끄도록 바꿨다
#
# 예전엔 "데몬이라 안 건드린다"였다. 그런데 그게 **조용히 어긋난 상태를 만든다**:
#
#   백엔드가 ROS_DOMAIN_ID=119 인 셸에서 떴다 (실물 로봇 도메인이 export 돼 있었다)
#     → 나중에 fms_service.sh 로 fleet_node·브릿지를 domain 86 에 다시 띄웠는데
#     → kill.sh 가 백엔드를 안 껐으므로 백엔드만 119 에 남았다
#     → /robot_state 에 Pinky-3 이 흐르는데 백엔드는 못 받는다
#     → 관제에 로봇이 안 잡히고, 스냅샷엔 예전 세션의 로봇이 stale 로 남아 있다
#
# 로그는 정상처럼 보였다("ROS 링크 시작 (domain 86)"). 그래서 원인을 찾는 데 오래 걸렸다.
# **재기동 범위가 반쪽이면 그 경계에서 이런 게 생긴다** — 기본을 "전부"로 둔다.
# ─────────────────────────────────────────────────────────────────────────────
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

KEEP_BACKEND=0
for arg in "$@"; do
  [ "$arg" = "--keep-backend" ] && KEEP_BACKEND=1
done

if [ "$KEEP_BACKEND" = "1" ]; then
  echo "[kill] 백엔드는 남깁니다 (--keep-backend)"
else
  "$REPO_ROOT/aba_fms_service/backend/stop.sh" || true
fi

if tmux has-session -t libi_fms 2>/dev/null; then
  tmux kill-session -t libi_fms
  echo "killed tmux session: libi_fms"
fi

# sim 세션·domain_bridge·ROS 고아 노드 정리 (domain_bridge 패턴 포함).
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
