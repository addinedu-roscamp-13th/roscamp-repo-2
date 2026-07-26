#!/usr/bin/env bash
# 노트북에서 실행 — Gazebo 시뮬레이션 스택(gazebo·nav2·rviz·bridge·fleet-link·fsm·path-driver)을
# tmux 로 띄우고, 이 로봇의 상태 어댑터(amcl_pose → /robot_state)도 함께 띄운다
# (사서 GUI 가 로봇을 그리려면 필요 — 파일 하단 주석 참고). 로봇 없이 전체 파이프라인을 검증할 때 쓴다.
#
#   ./sim.sh --robot Pinky-sim-1 --domain 90   # 기본
#   ./sim.sh --robot Pinky-sim-1 --domain 90 viewer     # 가제보 GUI 포함
#   ./sim.sh --robot Pinky-sim-1 --domain 90 --no-fsm   # fsm 창 없이
#
# ## 이름·도메인을 왜 매번 직접 주나
# sim 이 FMS 와 붙으려면 **로봇 이름과 도메인이 DB(rc_robots) 등록값과 같아야** 한다.
#   - 이름이 다르면 → fleet_node 가 FSM 상태를 매칭 못 해 관제에 "상태 미상"
#   - 도메인이 다르면 → 브릿지가 엉뚱한 도메인을 봐서 아무것도 안 넘어옴
# 자동 추론은 하지 않는다 — 조용히 엉뚱한 로봇/도메인으로 뜨는 것보다 멈추고 묻는 편이 낫다.
# DB(rc_robots)는 값을 정해주는 데 쓰지 않고, 등록 목록 안내와 대조 검증에만 쓴다.
#
# 정리: ./kill.sh (sim 세션 pinky_sim 까지 함께 지운다)
set -eo pipefail

# ⚠️ _common.sh 를 부르기 **전에** 명령줄 값을 붙잡아 둔다.
# _common.sh 가 .env 를 로드하면 ROS_DOMAIN_ID 가 어차피 채워져서, 그 뒤에는
# "사용자가 준 값"과 ".env 기본값"을 구별할 수 없다. 한 노트북에서 sim 을 여러 개
# (다른 이름·도메인으로) 띄우려면 이 구별이 꼭 필요하다.
_CLI_ROBOT="${FSM_ROBOT_ID:-}"
_CLI_DOMAIN="${ROS_DOMAIN_ID:-}"

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

# --robot / --domain 은 여기서 소비하고 나머지는 그대로 넘긴다.
#
# ⚠️ 왜 env 접두사(`ROS_DOMAIN_ID=90 ./sim.sh`)가 아니라 플래그인가:
#   ~/.bashrc 가 이미 `export ROS_DOMAIN_ID=119`(실물용)를 하고 있어서, 스크립트 입장에서
#   "사용자가 방금 준 값"과 "bashrc 가 켜준 값"을 구별할 수 없다. 그래서 env 로는 의도가
#   전달되지 않는다. 플래그는 오해가 없다.
WANT_ROBOT=""
WANT_DOMAIN=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot) WANT_ROBOT="${2:-}"; shift 2 ;;
    --domain) WANT_DOMAIN="${2:-}"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

# DB 조회에는 pymysql 이 필요한데 시스템 python3 엔 없다 — 서비스 venv 를 찾는다.
DBPY=""
for c in "$REPO_ROOT/aba_service/backend/.venv/bin/python" \
         "$REPO_ROOT/aba_fms_service/backend/.venv/bin/python" \
         "$REPO_ROOT/.venv/bin/python"; do
  if [ -x "$c" ] && "$c" -c "import pymysql" 2>/dev/null; then DBPY="$c"; break; fi
done

# DB 는 **값을 정하는 데 쓰지 않는다** — 등록 목록을 안내·검증하는 용도로만 읽는다.
_DB_LIST=""
if [ -n "$DBPY" ]; then
  _DB_LIST="$("$DBPY" - <<'PY' 2>/dev/null || true
import os, urllib.parse
url = os.environ.get("ROBOT_DATABASE_URL") or os.environ.get("ADMIN_DATABASE_URL") or ""
try:
    import pymysql
    u = urllib.parse.urlparse(
        url.replace("+asyncmy", "").replace("+aiomysql", "").replace("+pymysql", ""))
    c = pymysql.connect(host=u.hostname or "127.0.0.1", port=u.port or 3306,
                        user=u.username or "", password=u.password or "",
                        database=(u.path or "/").lstrip("/"), connect_timeout=4)
    cur = c.cursor()
    cur.execute("SELECT name, domain_id FROM rc_robots "
                "WHERE domain_id IS NOT NULL AND is_active=1 ORDER BY id")
    for name, dom in cur.fetchall():
        print(f"{name} {dom}")
    c.close()
except Exception:
    pass
PY
)"
fi

# ── 이름·도메인은 **반드시 명시**한다 (자동 추론 없음) ──────────────────────
#
# 한 노트북에서 sim 을 여러 개(다른 이름·도메인) 띄우므로, 자동으로 골라주면 "내가 무엇을
# 띄웠는지"가 흐려진다. 조용히 엉뚱한 로봇/도메인으로 뜨는 것보다 **멈추고 묻는 편이 낫다.**
# DB 는 값을 정해주는 데 쓰지 않고, **검증과 안내**에만 쓴다.
if [ -z "$WANT_ROBOT" ] || [ -z "$WANT_DOMAIN" ]; then
  echo "[sim] ❌ 로봇 이름과 도메인을 반드시 지정해야 합니다." >&2
  echo >&2
  echo "  사용법:  ./sim.sh --robot <이름> --domain <번호> [viewer|--no-fsm]" >&2
  echo >&2
  if [ -n "${_DB_LIST:-}" ]; then
    echo "  관제에 등록된 로봇 (이름·도메인이 여기와 같아야 FMS 와 붙습니다):" >&2
    printf '%s\n' "$_DB_LIST" | sed 's/^/    /' >&2
    echo >&2
    FIRST_NAME="$(printf '%s' "$_DB_LIST" | head -1 | awk '{print $1}')"
    FIRST_DOM="$(printf '%s' "$_DB_LIST" | head -1 | awk '{print $2}')"
    echo "  예:  ./sim.sh --robot $FIRST_NAME --domain $FIRST_DOM" >&2
  else
    echo "  ⚠️ DB 에서 로봇 목록을 읽지 못했습니다 — 관제 「로봇 추가」에서 먼저 등록하세요" >&2
    echo "     (이름 / 종류=pinky / IP=127.0.0.1 / 포트=9001 / 도메인)" >&2
  fi
  exit 1
fi

export FSM_ROBOT_ID="$WANT_ROBOT"
export ROS_DOMAIN_ID="$WANT_DOMAIN"

# 준 값이 DB 등록값과 다르면 **막지는 않되 경고**한다 — 일부러 다르게 띄우는 실험도 있다.
if [ -n "${_DB_LIST:-}" ]; then
  DB_DOM="$(printf '%s\n' "$_DB_LIST" | awk -v n="$WANT_ROBOT" 'tolower($1)==tolower(n){print $2}')"
  if [ -z "$DB_DOM" ]; then
    echo "[sim] ⚠️ '$WANT_ROBOT' 은 관제에 등록돼 있지 않습니다 — 브릿지가 안 생겨 FMS 와 안 붙습니다."
  elif [ "$DB_DOM" != "$WANT_DOMAIN" ]; then
    echo "[sim] ⚠️ 도메인 불일치: 관제 등록=$DB_DOM, 지금 지정=$WANT_DOMAIN"
    echo "[sim]    브릿지는 $DB_DOM 을 보므로 이대로면 FMS 와 안 붙습니다."
  fi
fi

# sim 을 여러 개 띄울 수 있게 tmux 세션 이름을 로봇마다 다르게 한다.
# (기본 세션명은 pinky_sim 고정이라 두 번째 sim 이 "이미 떠 있습니다"로 거절된다)
export SIM_SESSION="${SIM_SESSION:-pinky_sim_$(printf '%s' "$FSM_ROBOT_ID" | tr -cd '[:alnum:]_')}"

echo "[sim] 로봇=$FSM_ROBOT_ID  도메인=$ROS_DOMAIN_ID  세션=$SIM_SESSION"

ensure_built "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws"
ensure_built "$REPO_ROOT/aba_controller/libi_modes/ros_ws"

# 상태 어댑터를 함께 띄운다. **이건 선택 사항이 아니다.**
#
#   fleet_node 는 /robot_state(rmf_fleet_msgs/RobotState)를 구독해서 "어떤 로봇이 어디
#   있는가"를 안다. 그런데 로봇도 sim 도 그 타입을 발행하지 않는다(amcl_pose·battery 만).
#   이 어댑터가 /pinky{key}/amcl_pose 를 읽어 /robot_state 로 재발행하는 **유일한 경로**다.
#   없으면 fleet_node 가 로봇을 0대로 보고 배차도 순회도 시작조차 되지 않는다.
#
#   ⚠️ 그런데 그 상태가 화면에는 정상으로 보인다. FMS 관제 모니터링은
#      /pinky{key}/amcl_pose 를 직접 읽으므로(fleet_telemetry) 로봇이 그대로 뜬다.
#      "패널에 보이니 괜찮겠지"가 2026-07-26 순찰 정지 진단을 몇 시간 늦췄다.
#      사서(libi) GUI 는 /robot_state(fleet_link)를 보므로 여기서만 로봇이 사라진다.
#
#   ⚠️ 프로세스가 떠 있다고 일하고 있는 것도 아니다. amcl_pose 를 못 받으면 어댑터는
#      /robot_state 를 한 번도 발행하지 않는다. 그 상태는 로그의 'amcl_pose 대기' 로 보인다:
#         tail -f /tmp/libi-robot-link/<key>.log
#
# 이 로봇 하나만 띄운다(백그라운드, setsid). 어댑터는 창·세션 수명과 분리돼 있고,
# 정지는 명시적 요청에만 일어난다 — ./kill.sh 또는 robot-link.sh --all --stop.
# exec 로 넘어가면 이 셸이 사라지므로 반드시 그 전에 띄운다(어댑터는 amcl_pose 가 늦게 떠도 기다린다).
"$REPO_ROOT/scripts/laptop/robot-link.sh" "$FSM_ROBOT_ID" \
  || echo "[sim] ⚠️ 상태 어댑터 기동 실패 — fleet_node 가 이 로봇을 인식하지 못해 배차·순회가 동작하지 않습니다. 확인: pgrep -af robot_state_adapter"

cd "$REPO_ROOT"
exec "$REPO_ROOT/aba_controller/libi_drive_controller/ros_ws/scripts/sim.sh" "${ARGS[@]}"
