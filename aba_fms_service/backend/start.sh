#!/bin/bash
# PinkyPro 백엔드 시작 스크립트 (가상환경 자동 생성 포함)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 루트 .env 를 실어 준다. 이걸 안 하면 **기능이 조용히 꺼진 채로 뜬다** —
# 특히 LIBI_REAL_DISPATCH 가 없으면 orchestrator 가 fleet_node 로 배차를 넘기지 않아
# 주문은 EXECUTING 인데 로봇이 안 움직인다(로그에도 아무것도 안 남는다).
# scripts/laptop/fms_service.sh 로 띄우면 _common.sh 가 해 주지만, 이 스크립트를
# 직접 실행하는 경우(재기동·디버깅)가 잦아서 여기서도 챙긴다. 규칙은 한 파일에만 있다.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_ROOT/scripts/_load_env.sh"

# ── 서버(관제) ROS 도메인 ────────────────────────────────────────────────────
# ⚠️ 바로 위 `_load_env.sh` 가 루트 `.env` 의 `ROS_DOMAIN_ID=119` 를 실어 준다. 그건
#    **실물 로봇 pinky-3 한 대의 값**이다. 2026-07-30 에 이 프로세스가 그 값을 그대로
#    타서 관제 노드(`fastapi_ros_bridge`)가 로봇 도메인에 올라갔고, 로봇의 twist_mux
#    중재를 우회해 `/cmd_vel` 을 직접 발행했다.
#
#    지금은 백엔드가 `app/ros_domains.py` 의 서버 도메인(기본 111)에 **명시적으로** 뜨므로
#    셸 값이 무엇이든 안전하다. 아래 인자는 그 값을 덮고 싶을 때만 쓴다.
#
#      ./start.sh --domain-id 111
while [ $# -gt 0 ]; do
  case "$1" in
    --domain-id)
      [ -n "${2:-}" ] || { echo "[start] --domain-id 뒤에 값이 필요합니다"; exit 1; }
      export LIBI_SERVER_DOMAIN_ID="$2"; shift 2 ;;
    *) echo "[start] 모르는 인자: $1
  사용법: ./start.sh [--domain-id N]"; exit 1 ;;
  esac
done
echo "[start] 서버 ROS 도메인 = ${LIBI_SERVER_DOMAIN_ID:-111} (셸 ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-미설정})"

VENV="$SCRIPT_DIR/.venv"
LOG="/tmp/pinky_api.log"
PID_FILE="/tmp/pinky_api.pid"
PORT=9001

# ── 가상환경 확인 / 생성 ───────────────────────────────────────────────────────
if [ ! -f "$VENV/bin/uvicorn" ]; then
    echo "[setup] 가상환경 생성 중..."
    python3 -m venv "$VENV"
    echo "[setup] 패키지 설치 중..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r requirements.txt
    echo "[setup] 완료"
fi

# ── 이미 실행 중인지 확인 ─────────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[info] 백엔드가 이미 실행 중입니다 (PID: $OLD_PID, 포트: $PORT)"
        echo "[info] 로그: tail -f $LOG"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# ── 백그라운드 시작 ───────────────────────────────────────────────────────────
echo "[start] PinkyPro 백엔드 서버 시작 (포트: $PORT)..."
if [ -f /opt/ros/jazzy/setup.bash ]; then
    source /opt/ros/jazzy/setup.bash
fi
# fleet_ws overlay — 없으면 app/fleet_link.py 가 `libi_fleet_msgs` 를 import 못 해
# "fleet_link 비활성" 으로 떨어지고, 그러면 로봇 관측도 submit_task 도 전부 죽는다
# (/api/fleet/snapshot 이 linked=false, robots=[] 로 보인다).
FLEET_WS_SETUP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/fleet_ws/install/setup.bash"
if [ -f "$FLEET_WS_SETUP" ]; then
    source "$FLEET_WS_SETUP"
else
    echo "[start] ⚠️ fleet_ws 미빌드 — fleet 링크 없이 뜹니다: $FLEET_WS_SETUP"
fi
nohup "$VENV/bin/uvicorn" main:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# 1초 대기 후 정상 기동 확인
sleep 1
if kill -0 "$PID" 2>/dev/null; then
    echo "[ok] 서버 시작됨 (PID: $PID)"
    echo "[ok] API:  http://$(hostname -I | awk '{print $1}'):$PORT"
    echo "[ok] Docs: http://$(hostname -I | awk '{print $1}'):$PORT/docs"
    echo "[log] 로그 확인: tail -f $LOG"
else
    echo "[error] 서버 기동 실패 — 로그 확인: cat $LOG"
    cat "$LOG" | tail -20
    rm -f "$PID_FILE"
    exit 1
fi
