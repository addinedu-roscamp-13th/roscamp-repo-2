#!/usr/bin/env bash
# laptop-all.sh 가 띄운 것 전부 정리.
#
#   ./kill-laptop.sh              # UI · AI · 패널 · 관제 ROS  (:9001 백엔드는 남긴다)
#   ./kill-laptop.sh --with-api   # :9001 FMS 백엔드까지 함께
#
# ## :9001 을 기본으로 안 죽이는 이유
#
# 그 프로세스는 UI 전용이 아니다 — fleet_telemetry(도메인 86 ROS 스레드)를 물고 있어서,
# 내리면 **로봇 텔레메트리·명령 링크가 함께 끊긴다.** 로봇은 돌려두고 노트북 UI 만 껐다
# 켜는 경로를 살려 두려고 ui/kill.sh·laptop/kill.sh 가 일부러 남기는 것이고, 여기도 그 규칙을
# 따른다. 백엔드 코드를 고쳐서 정말 재기동해야 할 때만 --with-api 를 쓴다
# (그 경우도 scripts/laptop/fms-kill.sh 가 실제 종료를 담당한다).
#
# ## 무엇이 무엇을 지우나
#
#   여기        tmux 세션 libi_laptop (ai · gui 창)
#   ui/kill.sh  libi_ui_fms · libi_ui_lib 세션 + libi_gui 바이너리(pkill -x)
#   laptop/     libi_fms 세션 + 상태 어댑터 + perception_server / relay stub
#     kill.sh   + ros_ws/kill.sh 위임(sim 세션·domain_bridge·ROS 고아 노드)
#
# ⚠️ laptop/kill.sh 는 마지막에 exec 로 넘어간다 — 이 스크립트의 마지막에 둬야 한다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

WITH_API=false
for a in "$@"; do
  case "$a" in
    --with-api) WITH_API=true ;;
    *) die "모르는 인자: $a  (--with-api 만 받습니다)" ;;
  esac
done

# ── 관리자 추종 승인 기록 해제 ─────────────────────────────────────────────
# ⚠️ 세션을 지우기 **전에** 한다. :9001 이 아직 살아 있어야 하고, GUI 도 아직 떠 있어야 한다.
#
# libi_gui 는 정상 종료 때 releaseFollowOnExit() 로 해제를 보낸다(RobotController.cpp).
# 그런데 tmux 세션을 통째로 지우면 그게 못 돈다. 그러면 FMS 에 승인 기록만 살아남아,
# 다음에 패널에서 추종을 누르면 이렇게 거부당한다:
#
#     추종 승인 거부 — 관제에 이 로봇의 추종 승인 기록이 남아 있습니다. 먼저 해제하세요.
#
# 2026-07-27 하루에 두 번 겪었다. 로봇 이름을 인자로 받지 않으려고 status 를 읽어 남은 걸
# 전부 해제한다 — 어차피 노트북 스택을 통째로 내리는 중이라 남겨둘 이유가 없다.
# :9001 이 안 떠 있으면 조용히 건너뛴다(해제할 대상 자체가 없다).
release_follow_grants() {
  port_open 9001 || return 0
  command -v curl >/dev/null 2>&1 || return 0

  local ids
  ids="$(curl -s --max-time 3 http://127.0.0.1:9001/api/robot/admin-follow/status 2>/dev/null \
    | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for r in d.get("following", []):
    rid = r.get("robot_id")
    if rid:
        print(rid)' 2>/dev/null)" || return 0

  local rid
  for rid in $ids; do
    curl -s --max-time 5 -X POST -H 'Content-Type: application/json' \
      -d "{\"robot_id\":\"$rid\"}" \
      http://127.0.0.1:9001/api/robot/admin-follow/release >/dev/null 2>&1 \
      && echo "released admin-follow grant: $rid"
  done
}
release_follow_grants

if tmux has-session -t libi_laptop 2>/dev/null; then
  tmux kill-session -t libi_laptop
  echo "killed tmux session: libi_laptop (ai · gui)"
fi

"$REPO_ROOT/scripts/ui/kill.sh" all || true

if [ "$WITH_API" = true ]; then
  "$REPO_ROOT/scripts/laptop/fms-kill.sh" || true
fi

# ⚠️ exec 다 — 반드시 마지막.
exec "$REPO_ROOT/scripts/laptop/kill.sh"
