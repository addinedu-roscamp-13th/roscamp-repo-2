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
