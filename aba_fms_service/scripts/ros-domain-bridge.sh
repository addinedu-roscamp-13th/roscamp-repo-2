#!/usr/bin/env bash
# 로봇 도메인 <-> 서버 도메인(86) 브릿지를 DB(rc_robots) 기준으로 기동한다.
# gen_domain_bridges.py 는 두 가지가 갖춰진 셸에서 돌아야 한다:
#   - ROS2(ros2 명령) — 브릿지 자체를 서브프로세스로 띄우는 데 필요
#   - backend/.venv 의 파이썬 — pymysql 이 시스템 python3 에는 없다
# 매번 순서를 외우기 귀찮아서 스크립트로 뺐다.
#
#   ./ros-domain-bridge.sh          → 생성 + 기동 (기본, --run)
#   ./ros-domain-bridge.sh --check  → DB 값만 확인, 기동 안 함
set -eo pipefail
# -u(미정의 변수 에러)는 안 쓴다 — ROS2 setup.bash 내부 변수(AMENT_TRACE_SETUP_FILES 등)와
# 충돌한다. sim.sh/pi.sh/fsm-bt.sh 도 같은 이유로 -e 만 쓴다.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FMS_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PY="$FMS_DIR/backend/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "[ros-domain-bridge] backend/.venv 가 없습니다. 먼저 만드세요:"
  echo "  cd $FMS_DIR/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

source /opt/ros/jazzy/setup.bash

ARGS=("$@")
if [ ${#ARGS[@]} -eq 0 ]; then
  ARGS=(--run)
fi

cd "$FMS_DIR"
exec "$VENV_PY" scripts/gen_domain_bridges.py "${ARGS[@]}"
