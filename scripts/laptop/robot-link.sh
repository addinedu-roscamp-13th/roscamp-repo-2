#!/usr/bin/env bash
# 서버에서 실행 — 로봇 상태를 fleet_node 에 이어주는 **서버측 어댑터**.
#
# fleet_node 는 `/robot_state`(rmf_fleet_msgs/RobotState)를 구독해 로봇을 인식하는데,
# 로봇도 sim 도 그 타입을 발행하지 않는다(amcl_pose·battery 만 낸다). 그래서 이 어댑터가
# **domain_bridge 가 서버 도메인(86)으로 옮겨 놓은** `/<key>/amcl_pose` 를 읽어
# `/robot_state` 로 재발행한다. 로봇은 무수정이다.
# 이게 없으면 fleet_node 가 로봇을 **0대**로 보고 배차 자체가 불가능하다.
#
# 반대 방향(fleet_node 경로 → nav2)은 **로봇 쪽**에서 돈다 — drive-pi/pi.sh 의 path-driver 창
# (sim 은 laptop/sim.sh 가 같은 창을 띄운다).
#
# 보통은 직접 부를 일이 없다: `fms_service.sh` 가 `--all` 로 자동 기동한다.
#
#   ./robot-link.sh --all               # DB 로봇 전부 (백그라운드)
#   ./robot-link.sh --all --foreground  # 전부, 포그라운드 (tmux 창용)
#   ./robot-link.sh pinky3              # 한 대만
#   ./robot-link.sh pinky3 --stop
#   ./robot-link.sh --all --stop
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FMS_DOMAIN="${LIBI_FMS_DOMAIN:-86}"
STATE_DIR="${TMPDIR:-/tmp}/libi-robot-link"
mkdir -p "$STATE_DIR"

ALL=0; STOP=0; FG=0; ROBOT=""
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    --stop) STOP=1 ;;
    --foreground|--fg) FG=1 ;;
    *) ROBOT="$a" ;;
  esac
done

# 브릿지 접두사 규칙 — gen_domain_bridges.py 와 같아야 한다:
# 이름에서 -,_,공백 제거 후 첫 글자만 소문자. "Pinky-3"→pinky3, "Pinkysim"→pinkysim
key_of() { printf '%s' "$1" | tr -d '_ -' | sed 's/^\(.\)/\L\1/'; }

# DB 조회용 파이썬(pymysql 필요) — 시스템 python3 엔 없어서 서비스 venv 를 찾는다.
find_dbpy() {
  for c in "$REPO_ROOT/aba_service/backend/.venv/bin/python" \
           "$REPO_ROOT/aba_fms_service/backend/.venv/bin/python" \
           "$REPO_ROOT/.venv/bin/python" python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import pymysql" 2>/dev/null; then
      echo "$c"; return
    fi
  done
  echo ""
}

list_robots() {
  local dbpy; dbpy="$(find_dbpy)"
  [ -n "$dbpy" ] || { echo ""; return; }
  "$dbpy" - <<'PY' 2>/dev/null || true
import os, urllib.parse
url = os.environ.get("ROBOT_DATABASE_URL") or os.environ.get("ADMIN_DATABASE_URL") or ""
try:
    import pymysql
    u = urllib.parse.urlparse(
        url.replace("+asyncmy","").replace("+aiomysql","").replace("+pymysql",""))
    c = pymysql.connect(host=u.hostname or "127.0.0.1", port=u.port or 3306,
                        user=u.username or "", password=u.password or "",
                        database=(u.path or "/").lstrip("/"), connect_timeout=4)
    cur = c.cursor()
    # domain_id 가 있는 = 브릿지가 만들어지는 로봇만. 서버/팔은 대상이 아니다.
    cur.execute("SELECT name FROM rc_robots WHERE domain_id IS NOT NULL AND is_active=1")
    print("\n".join(r[0] for r in cur.fetchall())); c.close()
except Exception:
    pass
PY
}

stop_one() {
  local key; key="$(key_of "$1")"
  local pf="$STATE_DIR/$key.pid"
  [ -f "$pf" ] && kill "$(cat "$pf")" 2>/dev/null || true
  rm -f "$pf"
}

start_one() {
  local robot="$1" key pf log
  key="$(key_of "$robot")"; pf="$STATE_DIR/$key.pid"; log="$STATE_DIR/$key.log"

  # 같은 이름으로 두 번 발행하면 위치가 튄다 — 중복 기동을 막는다.
  if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
    echo "  [robot-link] $robot 이미 실행 중 (pid $(cat "$pf"))"
    return 0
  fi

  ROS_DOMAIN_ID="$FMS_DOMAIN" setsid nohup python3 \
    "$REPO_ROOT/aba_fms_service/scripts/robot_state_adapter.py" \
    --robot "$robot" --prefix "/$key" \
    > "$log" 2>&1 < /dev/null &
  echo $! > "$pf"
  sleep 2
  if kill -0 "$(cat "$pf")" 2>/dev/null; then
    echo "  [robot-link] ✅ $robot  (/$key/amcl_pose → /robot_state, domain $FMS_DOMAIN)"
  else
    echo "  [robot-link] ❌ $robot 기동 실패 — $log"
    tail -3 "$log" | sed 's/^/      /'
  fi
}

# ── 대상 목록 ────────────────────────────────────────────────────────────────
TARGETS=()
if [ "$ALL" = "1" ]; then
  while IFS= read -r n; do [ -n "$n" ] && TARGETS+=("$n"); done <<< "$(list_robots)"
elif [ -n "$ROBOT" ]; then
  TARGETS=("$ROBOT")
else
  die "로봇 이름을 주거나 --all 을 쓰세요.  예: $0 --all"
fi

if [ "$STOP" = "1" ]; then
  for r in "${TARGETS[@]}"; do stop_one "$r"; done
  echo "[robot-link] 정리 완료 (${#TARGETS[@]}대)"
  exit 0
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
  echo "[robot-link] ⚠️ domain_id 가 등록된 로봇이 없습니다 — 관제 「로봇 추가」에서 등록하세요."
  [ "$FG" = "1" ] && { sleep infinity; }
  exit 0
fi

# ── ROS 환경 (rmf_fleet_msgs 는 fleet_ws overlay 에 있다) ────────────────────
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
FLEET_SETUP="$REPO_ROOT/aba_fms_service/fleet_ws/install/setup.bash"
[ -f "$FLEET_SETUP" ] || die "fleet_ws 미빌드: $FLEET_SETUP
  빌드: cd aba_fms_service/fleet_ws && colcon build --packages-select libi_fleet"
# shellcheck disable=SC1090
source "$FLEET_SETUP"

echo "[robot-link] 상태 어댑터 ${#TARGETS[@]}대 (로봇 위치 → fleet_node, domain $FMS_DOMAIN)"
for r in "${TARGETS[@]}"; do start_one "$r"; done
echo "[robot-link] 로그: $STATE_DIR/"

if [ "$FG" = "1" ]; then
  # tmux 창에서 살아 있게 두고, 자식이 죽으면 로그로 알린다.
  trap 'for r in "${TARGETS[@]}"; do stop_one "$r"; done; exit 0' INT TERM
  echo "[robot-link] (Ctrl+C 로 어댑터 종료)"
  while true; do
    sleep 10
    for r in "${TARGETS[@]}"; do
      pf="$STATE_DIR/$(key_of "$r").pid"
      if [ -f "$pf" ] && ! kill -0 "$(cat "$pf")" 2>/dev/null; then
        echo "[robot-link] ⚠️ $r 어댑터가 죽었습니다 — 재기동"
        rm -f "$pf"; start_one "$r"
      fi
    done
  done
fi
