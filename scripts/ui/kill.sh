#!/usr/bin/env bash
# UI 정리 — fms.sh / library.sh 가 띄운 tmux 세션과 그 안의 프론트/백엔드를 내린다.
# 세션을 지우면 그 창의 포그라운드 프로세스(vite, uvicorn)도 함께 죽는다.
#
#   ./kill.sh            # 둘 다 (관제 + 도서관)
#   ./kill.sh fms        # 관제(FMS) UI 만
#   ./kill.sh library    # 도서관 웹만
#
# FMS 백엔드(:9001)는 데몬이라 세션과 무관 — 로봇 관제와 공유하므로 자동 종료하지 않는다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

kill_session() {   # <세션이름> <설명>
  if tmux has-session -t "$1" 2>/dev/null; then
    tmux kill-session -t "$1"
    echo "killed tmux session: $1 ($2)"
  else
    echo "[skip] $1 세션 없음 ($2)"
  fi
}

warn_port() {   # <포트> — 세션을 지웠는데도 열려 있으면 알림
  if port_open "$1"; then
    echo "[경고] :$1 이 아직 열려 있습니다 — 남은 프로세스가 있는지 확인하세요."
  fi
}

kill_fms() {
  kill_session libi_ui_fms "관제 프론트 :9002"
  sleep 1
  warn_port 9002
  if port_open 9001; then
    echo "[api] FMS 백엔드(:9001)는 데몬으로 남아 있습니다 (로봇 관제와 공유)."
    echo "      UI 만 쓸 거였으면 중지: aba_fms_service/backend/stop.sh"
  fi
}

kill_library() {
  kill_session libi_ui_lib "도서관 프론트 :3000 + 백엔드 :8000"
  sleep 1
  warn_port 3000
  warn_port 8000
}

case "${1:-all}" in
  all)          kill_fms; kill_library ;;
  fms)          kill_fms ;;
  library|lib)  kill_library ;;
  *) die "알 수 없는 대상: $1  (fms | library | 생략=전체)" ;;
esac
