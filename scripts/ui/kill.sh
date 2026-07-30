#!/usr/bin/env bash
# UI 정리 — fms.sh / library.sh 가 띄운 tmux 세션과 그 안의 프론트/백엔드를 내리고,
# libi_gui.sh 로 띄운 터치패널 프로세스도 함께 종료한다.
# 세션을 지우면 그 창의 포그라운드 프로세스(vite, uvicorn)도 함께 죽는다.
#
#   ./kill.sh            # 전부 (관제 + 도서관 + 터치패널)
#   ./kill.sh fms        # 관제(FMS) UI 만
#   ./kill.sh library    # 도서관 웹만
#   ./kill.sh gui        # libi_gui 터치패널만
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

# libi_gui 는 tmux 가 아니라 사용자 터미널에서 exec 로 뜬다(libi_gui.sh → gui.sh → 바이너리).
# 그래서 세션이 아니라 프로세스를 직접 찾는다. `-x`(프로세스 이름 완전일치)로 잡는다 —
# `pkill -f libi_gui` 로 하면 cmdline 에 그 글자가 든 빌드·편집기·셸까지 같이 죽고,
# 경로로 앵커하면 `./build/libi_gui` 처럼 상대경로로 띄운 경우를 놓친다.
#
# ⚠️ SIGTERM 만으로는 안 죽는다 — rclcpp 가 SIGTERM 핸들러를 걸어 ROS 컨텍스트만 내리고,
# Qt 이벤트 루프는 그대로 돌아 프로세스가 남는다(실측: TERM 후에도 살아 있음).
# 그래서 TERM 으로 예를 갖추고, 2초 안 죽으면 KILL 한다. 패널은 저장할 상태가 없다.
kill_gui() {
  if ! pkill -x libi_gui; then
    echo "[skip] libi_gui 안 떠 있음 (터치패널)"
    return
  fi
  for _ in 1 2 3 4; do
    sleep 0.5
    pgrep -x libi_gui >/dev/null || { echo "killed: libi_gui 터치패널"; return; }
  done
  pkill -9 -x libi_gui || true   # 그 사이 죽었으면 1 — set -e 로 여기서 끝나지 않게
  echo "killed: libi_gui 터치패널 (SIGKILL — TERM 무시)"
}

case "${1:-all}" in
  all)          kill_fms; kill_library; kill_gui ;;
  fms)          kill_fms ;;
  library|lib)  kill_library ;;
  gui)          kill_gui ;;
  *) die "알 수 없는 대상: $1  (fms | library | gui | 생략=전체)" ;;
esac
