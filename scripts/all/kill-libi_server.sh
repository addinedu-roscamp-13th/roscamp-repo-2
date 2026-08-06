#!/usr/bin/env bash
# server.sh 가 띄운 **서버 스택만** 정리 — 로봇별 세션(추종 AI·패널)은 그대로 둔다.
#
#   ./kill-libi_server.sh              # 관제 UI · 도서관 웹 · 브릿지 · fleet_node · **:9001 FMS**
#   ./kill-libi_server.sh --keep-api   # :9001 만 남긴다 (로봇 돌려둔 채 UI 만 껐다 켤 때)
#
# 로봇 한 대만 내리려면 ./kill-libi_laptop.sh <이름>, 전부면 ./kill-libi_laptop.sh
#
# ## [2026-08-05] :9001 을 **기본으로 죽이도록 뒤집었다**
#
# 예전 기본은 "남긴다" 였고 `--with-api` 를 줘야 죽었다. 남긴 이유는 아래 트레이드오프
# 때문이었는데, 실제로 물린 건 반대쪽이었다 — 이 데몬은 `--reload` 없이 nohup 으로 뜨고
# **어떤 kill.sh 도 안 건드려서**, 백엔드 코드를 고쳐도 옛 프로세스가 계속 응답한다.
# 2026-07-27(panel_bridge 5시간짜리 유령), 2026-08-05(배달 다리 변경·LIBI_ARM_VIA_BT 가
# 안 먹음) 두 번 다 같은 모양으로 시간을 날렸다. 사용자 결정으로 기본을 뒤집는다.
#
# ⚠️ **뒤집으면서 잃는 것(알고 쓰는 것)** — :9001 은 UI 전용이 아니다. 그 프로세스가
# fleet_telemetry(도메인 86 ROS 스레드)를 물고 있어서, 내리면 **로봇 텔레메트리·명령
# 링크가 함께 끊긴다.** 로봇은 돌려둔 채 UI 만 껐다 켜려면 `--keep-api` 를 준다.
# :9001 만 따로 내리는 건 여전히 scripts/laptop/fms-kill.sh 가 담당한다.
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

WITH_API=true
for a in "$@"; do
  case "$a" in
    --keep-api) WITH_API=false ;;
    # 옛 이름 — 이제 기본이라 아무 일도 안 한다. 손가락·메모에 남아 있어도 안 죽게 받아준다.
    --with-api) WITH_API=true ;;
    *) die "모르는 인자: $a  (--keep-api 만 받습니다)" ;;
  esac
done

echo "[kill-libi_server] ⚠ 이 머신의 sim·로봇 세션(pinky_pi·pinky_sim*)도 함께 내려갑니다 — 위 머리말 참고"
"$REPO_ROOT/scripts/ui/kill.sh" fms || true
"$REPO_ROOT/scripts/ui/kill.sh" library || true

if [ "$WITH_API" = true ]; then
  "$REPO_ROOT/scripts/laptop/fms-kill.sh" || true
else
  # 남길 때는 **무엇이 남는지 말한다.** 조용히 남으면 코드를 고쳐도 옛 프로세스가
  # 계속 응답해서 "왜 안 바뀌지" 로 시간을 날린다(머리말의 두 사고가 그 모양이었다).
  if port_open 9001; then
    _p="$(ss -ltnp 2>/dev/null | awk '/:9001 /{print $NF}' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)"
    echo "[kill-libi_server] --keep-api: :9001 남깁니다${_p:+  pid=$_p  기동=$(ps -o lstart= -p "$_p" 2>/dev/null | sed 's/^ *//')}"
    echo "                   백엔드 코드를 고쳤다면 이 프로세스는 옛 코드입니다 — $REPO_ROOT/scripts/laptop/fms-kill.sh"
  fi
fi

# ⚠️ exec 다 — 반드시 마지막.
exec "$REPO_ROOT/scripts/laptop/kill.sh" --keep-ai
