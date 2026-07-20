#!/usr/bin/env bash
# 도서관 웹(aba_service) UI — 회원/사서용. 백엔드(:8000) + 프론트엔드(:3000, vite) 를
# tmux 로 함께 띄운다. 실행법은 각 서비스 문서 방식 그대로(backend/run.sh, frontend/npm run dev).
#
#   ./library.sh
#
# 회원 = 루트(/), 사서 = /admin.
# tmux 세션 'libi_ui_lib' — 창: urls / backend(uvicorn) / frontend(vite).
# 분리 Ctrl+b d / 종료 ./kill.sh (backend·frontend 둘 다 이 세션의 포그라운드라 세션만 지우면 됨)
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

WEB="$REPO_ROOT/aba_service"
SESSION="libi_ui_lib"
IP="$(lan_ip)"

need_cmd tmux "sudo apt install -y tmux"
tmux has-session -t "$SESSION" 2>/dev/null && \
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."

need "$WEB/backend/run.sh" "aba_service 백엔드 실행 스크립트"
# run.sh 는 .venv 를 자동 생성하지 않는다(없으면 시스템 python 폴백). 여기서 보장한다.
ensure_venv "$WEB/backend"
ensure_npm "$WEB/frontend"

BANNER="도서관 웹 (회원/사서)
  회원(member)    http://localhost:3000/         http://$IP:3000/
  사서(librarian) http://localhost:3000/admin    http://$IP:3000/admin
  API             http://localhost:8000/         http://$IP:8000/"

cd "$REPO_ROOT"
tmux new-session -d -s "$SESSION" -n urls \
  bash -c "printf '%s\n' \"$BANNER\"; exec bash"
tmux new-window -t "$SESSION" -n backend \
  bash -c "cd '$WEB/backend' && ./run.sh; exec bash"
tmux new-window -t "$SESSION" -n frontend \
  bash -c "cd '$WEB/frontend' && npm run dev; exec bash"

tmux select-window -t "$SESSION:frontend"
tmux_attach "$SESSION"
