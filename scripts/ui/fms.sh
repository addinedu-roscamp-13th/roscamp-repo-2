#!/usr/bin/env bash
# 관제(FMS) UI — 백엔드(:9001, 데몬) + 프론트엔드(:9002, vite) 를 tmux 로 함께 띄운다.
# 실행법은 각 서비스의 문서 방식을 그대로 쓴다(backend/start.sh, frontend/npm run dev).
#
#   ./fms.sh
#
# tmux 세션 'libi_ui_fms' — 창: urls / frontend(vite) / api(로그).
# 분리 Ctrl+b d / 종료 ./kill.sh. 백엔드 데몬 중지: aba_fms_service/backend/stop.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FMS="$REPO_ROOT/aba_fms_service"
SESSION="libi_ui_fms"
IP="$(lan_ip)"

need_cmd tmux "sudo apt install -y tmux"
tmux has-session -t "$SESSION" 2>/dev/null && \
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."

ensure_npm "$FMS/frontend"

# 백엔드는 데몬. 이미 :9001 이 떠 있으면 재사용하고, 없으면 start.sh 로 띄운다.
if port_open 9001; then
  echo "[api] :9001 이미 떠 있음 — 재사용"
else
  "$FMS/backend/start.sh"
fi

BANNER="관제(FMS) UI
  콘솔   http://localhost:9002/        http://$IP:9002/   (로그인 후 /admin/… 관제 화면)
  API    http://localhost:9001/        http://$IP:9001/"

cd "$REPO_ROOT"
tmux new-session -d -s "$SESSION" -n urls \
  bash -c "printf '%s\n' \"$BANNER\"; exec bash"
tmux new-window -t "$SESSION" -n frontend \
  bash -c "cd '$FMS/frontend' && npm run dev; exec bash"
tmux new-window -t "$SESSION" -n api \
  bash -c "echo '[api] 백엔드는 데몬입니다 (중지: aba_fms_service/backend/stop.sh)'; tail -n +1 -f /tmp/pinky_api.log; exec bash"

tmux select-window -t "$SESSION:frontend"
tmux_attach "$SESSION"
