#!/usr/bin/env bash
# 로봇 도메인 <-> 서버 도메인(111) 브릿지를 DB(rc_robots) 기준으로 기동한다.
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

# 루트 .env 를 싣는다. 없으면 app.config 의 ROBOT_DATABASE_URL 이 전역 DATABASE_URL
# (=도서관 웹의 `aba` DB)로 폴백해 **엉뚱한 DB 를 본다.**
#   실측: 단독 실행 시 `(1054, "Unknown column 'ip_address' in 'SELECT'")`
#   fms_service.sh 로 띄우면 _common.sh 가 미리 실어 줘서 안 드러났다.
REPO_ROOT="$(dirname "$FMS_DIR")"
# shellcheck disable=SC1091
. "$REPO_ROOT/scripts/_load_env.sh"

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
