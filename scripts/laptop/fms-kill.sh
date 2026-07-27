#!/usr/bin/env bash
# FMS 백엔드(:9001) 데몬만 내린다 — kill.sh 들이 **일부러** 안 건드리는 그것.
#
#   ./fms-kill.sh
#
# ## 왜 kill.sh 에 안 넣고 따로 두나
#
# :9001 은 UI 전용이 아니다. 그 프로세스가 `fleet_telemetry`(도메인 86 ROS 스레드)를 물고
# 있어서, 내리면 로봇 텔레메트리·명령 링크가 함께 끊긴다. 그래서 "로봇은 돌려두고 UI 만
# 껐다 켠다"가 가능하도록 ui/kill.sh·laptop/kill.sh 는 이걸 남긴다(각 파일 주석 참고).
# 정말 내려야 할 때만 이 스크립트를 명시적으로 부른다.
#
# ## 언제 필요한가
#
# 이 데몬은 `--reload` 없이 nohup 으로 뜨고(backend/start.sh) **어떤 kill.sh 도 안 건드린다.**
# 그래서 백엔드 코드를 고쳐도 옛 프로세스가 계속 응답한다 — 2026-07-27 에 실제로 겪었다:
# `panel_bridge` 를 추가했는데 5시간 전에 뜬 데몬이 살아 있어, 패널의 ROS2 요청을 아무도
# 받지 않고 5초 타임아웃만 반복됐다("FMS 통신 오류/무응답"). 그래서 기동 시각을 먼저 찍는다.
#
# 종료 자체는 backend/stop.sh 가 한다(PID 파일 + venv 절대경로 패턴 + SIGKILL 폴백).
# 여기서 더 하는 건 **포트가 실제로 풀렸는지 확인**하는 것 하나다 — "죽였다"와 "다시 띄울 수
# 있다"는 다르고, 다음 start.sh 가 실패하는 건 후자다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

BACKEND="$REPO_ROOT/aba_fms_service/backend"
need "$BACKEND/stop.sh" "FMS 백엔드 stop.sh"

if ! port_open 9001; then
  echo "[fms-kill] :9001 이 이미 닫혀 있습니다 — 내릴 백엔드가 없습니다."
  exit 0
fi

# 지금 도는 게 언제 뜬 건지 보여준다. 코드를 고친 시각보다 앞서면 그게 곧 원인이다.
PID="$(ss -ltnp 2>/dev/null | awk '/:9001 /{print $NF}' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)"
if [ -n "${PID:-}" ]; then
  echo "[fms-kill] :9001 pid=$PID  기동=$(ps -o lstart= -p "$PID" 2>/dev/null | sed 's/^ *//')"
fi

"$BACKEND/stop.sh"

# stop.sh 는 프로세스만 본다. 포트가 풀렸는지는 여기서 확인한다.
for _ in $(seq 1 6); do
  port_open 9001 || { echo "[fms-kill] ✅ :9001 해제됨. 다시 띄우려면: $BACKEND/start.sh"; exit 0; }
  sleep 0.5
done

echo "[fms-kill] ⚠ :9001 이 아직 열려 있습니다 — 다른 프로세스가 잡고 있는지 확인하세요:"
echo "           ss -ltnp | grep :9001"
exit 1
