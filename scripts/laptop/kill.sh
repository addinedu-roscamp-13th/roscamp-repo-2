#!/usr/bin/env bash
# 노트북 쪽 정리 — 관제(aba_fms_service) 백엔드·프론트엔드는 건드리지 않는다.
#   1) tmux 세션 libi_fms (fms_service.sh — fleet_node · 브릿지 · 어댑터)
#   2) AI 추종 서버(perception_server) · 릴레이 stub(aba_ai_service/main.py)
#   3) 나머지(sim 세션 pinky_sim*, domain_bridge, launch 고아 노드)는 기존 kill.sh 에 위임
#
#   ./kill.sh                  정리
#   ./kill.sh --keep-ai        AI 추종 서버는 남긴다 (서버 스택만 내릴 때)
#
# `--keep-ai` 가 왜 있나: 추종 서버는 **로봇별**이다(all/libi_laptop.sh 의 `libi_laptop_<key>`
# 세션 안에서 돈다). 서버 스택만 내리는데 여기서 패턴으로 쓸어버리면 다른 로봇의 추종까지
# 끊긴다 — 패턴이 로봇을 구분하지 못하기 때문이다. 로봇별 정리는 세션 종료가 담당한다.
#
# 관제 백엔드(:9001)/프론트(:9002) 중지는 aba_fms_service/backend/stop.sh 와
# 프론트 쪽에서 따로 한다 — 이 스크립트는 관여하지 않는다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

KEEP_AI=false
for a in "$@"; do
  case "$a" in
    --keep-ai) KEEP_AI=true ;;
    *) die "모르는 인자: $a  (--keep-ai 만 받습니다)" ;;
  esac
done

if tmux has-session -t libi_fms 2>/dev/null; then
  tmux kill-session -t libi_fms
  echo "killed tmux session: libi_fms"
fi

# 상태 어댑터를 **명시적으로** 정리한다.
#
# robot-link.sh 는 더 이상 시그널 트랩으로 자동 정지하지 않는다(2026-07-26, 의도된 변경 —
# 창이 닫혔다고 어댑터가 죽으면 sim 의 로봇 인식까지 함께 죽는다). 그래서 이 스크립트가
# 어댑터 정리의 명시적 주체다.
#
# ⚠️ tmux 세션을 죽인 **뒤에** 부른다. --foreground 워치독을 먼저 없애면
#    pid 파일 삭제와 워치독의 존재 검사 사이 경쟁이 원천적으로 사라진다.
#
# 아래 ros_ws/kill.sh 의 `pkill -f "robot_state_adapter.py"` 는 2차 그물이다 —
# 프로세스는 그것도 죽이지만, **pid 파일 정리와 신원 검증은 여기서만 한다.**
"$REPO_ROOT/scripts/laptop/robot-link.sh" --all --stop || true

# AI 추종 서버 정리 — ai-server.sh / ai_follower_service.sh 가 띄우는 perception_server 와
# relay-stub.sh 가 띄우는 aba_ai_service/main.py.
#
# ⚠️ 이 둘은 tmux 없이 포그라운드로 뜨는 경우가 많아 **세션 정리로는 안 잡힌다.** 창을 닫아도
#    살아남고, 그러면 UDP:6001 / TCP:5007 을 계속 물고 있어 다음 ai-server.sh 가 바인드
#    실패로 바로 죽는다. 증상은 "왜 영상이 안 뜨지"로 나타나 원인을 찾기 어렵다.
#
# TERM→KILL 과 생존 확인은 _common.sh 의 kill_patterns 가 한다(drive-pi/handy-pi kill.sh 와 공유).
if [ "$KEEP_AI" = true ]; then
  echo "[keep-ai] 추종 서버(perception_server)·릴레이 stub 은 남깁니다 — 로봇별 정리는 all/kill-libi_laptop.sh"
else
  kill_patterns "perception_server.py" "aba_ai_service/main.py"
fi

# sim 세션·domain_bridge·ROS 고아 노드 정리 (domain_bridge 패턴 포함).
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/kill.sh"
