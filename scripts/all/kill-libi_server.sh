#!/usr/bin/env bash
# server.sh 가 띄운 **서버 스택만** 정리 — 로봇별 세션(추종 AI·패널)은 그대로 둔다.
#
#   ./kill-libi_server.sh              # 관제 UI · 도서관 웹 · 브릿지 · fleet_node  (:9001 은 남긴다)
#   ./kill-libi_server.sh --with-api   # :9001 FMS 백엔드까지 함께
#
# 로봇 한 대만 내리려면 ./kill-libi_laptop.sh <이름>, 전부면 ./kill-libi_laptop.sh
#
# ## :9001 을 기본으로 안 죽이는 이유
#
# 그 프로세스는 UI 전용이 아니다 — fleet_telemetry(도메인 86 ROS 스레드)를 물고 있어서,
# 내리면 **로봇 텔레메트리·명령 링크가 함께 끊긴다.** 로봇은 돌려두고 UI 만 껐다 켜는
# 경로를 살려 두려는 것이고, 실제 종료는 scripts/laptop/fms-kill.sh 가 담당한다.
#
# ## 무엇이 무엇을 지우나
#
#   ui/kill.sh fms      libi_ui_fms 세션 (:9002 프론트)
#   ui/kill.sh library  libi_ui_lib 세션 (:8000 백엔드 + :3000 프론트)
#   laptop/kill.sh      libi_fms 세션 + 상태 어댑터 + ros_ws/kill.sh 위임(domain_bridge·고아 노드)
#
# ⚠️ **이 노트북에서 돌던 sim·로봇 스택도 같이 내려간다.** laptop/kill.sh 가 마지막에
#    ros_ws/scripts/kill.sh 로 넘어가는데(그 파일 마지막 줄), 거기서 `pinky_pi`·`pinky_sim*`
#    세션과 nav2/bringup/path-driver 프로세스를 이름으로 쓸어담는다. 노트북에서 sim 을
#    띄워 둔 채 "서버만" 내릴 생각이면 그게 아니다. (codex 적대적 검토 2026-07-29)
#    — 로봇 Pi 는 다른 머신이라 영향 없다.
# ⚠️ 터치패널(libi_gui)과 추종 서버는 **안 건드린다** — 둘 다 로봇별이다.
#    `laptop/kill.sh` 에 `--keep-ai` 를 주는 이유가 그것이다(패턴이 로봇을 구분 못 한다).
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

echo "[kill-libi_server] ⚠ 이 머신의 sim·로봇 세션(pinky_pi·pinky_sim*)도 함께 내려갑니다 — 위 머리말 참고"
"$REPO_ROOT/scripts/ui/kill.sh" fms || true
"$REPO_ROOT/scripts/ui/kill.sh" library || true

if [ "$WITH_API" = true ]; then
  "$REPO_ROOT/scripts/laptop/fms-kill.sh" || true
fi

# ⚠️ exec 다 — 반드시 마지막.
exec "$REPO_ROOT/scripts/laptop/kill.sh" --keep-ai
