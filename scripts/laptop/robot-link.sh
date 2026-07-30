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

# [2026-07-30] 서버 도메인 86 → 111. 기본값은 fms_service.sh·app/ros_domains.py 와 같아야 한다
# (다르면 어댑터가 브릿지가 옮겨 놓은 토픽을 못 찾고, 증상은 "관제에 로봇이 안 뜬다" 뿐이다).
FMS_DOMAIN="${LIBI_FMS_DOMAIN:-${LIBI_SERVER_DOMAIN_ID:-111}}"
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
# 이름에서 -,_,공백 제거 후 첫 글자만 소문자. "pinky-3"→pinky3, "pinkysim"→pinkysim
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

# pid 파일은 **힌트일 뿐 소유권의 근거가 아니다.**
#
# 예전엔 pid 파일의 숫자를 그대로 kill 했다. 두 가지가 깨진다:
#   ① pid 재사용 — 그 번호를 물려받은 **무관한 프로세스를 죽인다**
#   ② 소유권 상실 — 파일이 낡은 pid 를 가리키는 동안 진짜 어댑터가 다른 pid 로 살아 있으면
#      영영 못 찾는다 (파일을 지우는 순간 추적 수단이 사라진다)
#
# 그래서 정지 대상을 **/proc 의 argv 로 직접 찾는다.** 파일이 뭘 가리키든,
# 그 로봇의 어댑터인 프로세스는 전부 잡힌다. 파일은 지우기만 한다.
# argv 를 NUL 로 끊어 **정확 비교**한다 — 부분일치/glob 로 비교하면 로봇 이름에 따라
# 다른 로봇의 어댑터를 잡을 수 있다.
_adapter_pids_for_key() {    # $1=key  → 해당 어댑터 pid 들을 줄 단위로 출력
  local key="$1" pid want="--prefix" argv
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    [ -r "/proc/$pid/cmdline" ] || continue
    # argv 를 배열로 읽는다(NUL 구분). 부분문자열 비교를 쓰지 않는다.
    mapfile -d '' -t argv < "/proc/$pid/cmdline" 2>/dev/null || continue
    local i found_script=0 found_prefix=0
    for i in "${argv[@]}"; do
      case "$i" in */robot_state_adapter.py|robot_state_adapter.py) found_script=1 ;; esac
    done
    [ "$found_script" = "1" ] || continue
    for ((i = 0; i < ${#argv[@]}; i++)); do
      if [ "${argv[i]}" = "$want" ] && [ "${argv[i+1]:-}" = "/$key" ]; then
        found_prefix=1; break
      fi
    done
    if [ "$found_prefix" = "1" ]; then echo "$pid"; fi
  done
  # ⚠️ 반드시 성공으로 끝낸다. 이 스크립트는 `set -eo pipefail` 아래에서 돌고,
  #   호출부는 `pids="$(_adapter_pids_for_key "$key")"` 라는 **단순 대입**이다.
  #   그 대입의 종료상태는 함수의 종료상태이고, 함수의 종료상태는 for 루프의
  #   **마지막 반복**이 남긴 값이다.
  #   → /proc 열거의 마지막 항목이 "다른 로봇의" 어댑터면 found_prefix=0 이라
  #     마지막 명령이 1 을 남기고, errexit 이 스크립트를 그 자리에서 죽인다.
  #     rm -f "$pf" 도, 남은 로봇들의 정리도 실행되지 않는다.
  #     kill.sh 의 `|| true` 가 그 죽음을 삼켜서 조용히 절반만 정리된다.
  #   실측 재현: 동형 함수로 확인 — 대입 다음 줄에 도달하지 못하고 exit 1.
  #   로봇이 1대뿐인 테스트로는 절대 드러나지 않는다(케이스 5 가 그래서 있다).
  return 0
}

# 정지 대상은 DB 가 아니라 **실제로 돌고 있는 프로세스**다.
# DB 가 죽었거나 로봇 등록이 바뀌어도, pid 파일이 낡았어도, 어댑터는 반드시 정리된다.
stop_by_key() {
  local key="$1"
  local pf="$STATE_DIR/$key.pid" log="$STATE_DIR/$key.log"
  local pids; pids="$(_adapter_pids_for_key "$key")"

  rm -f "$pf"      # 파일은 힌트였을 뿐이다. 워치독이 재기동하지 않게 지운다.

  [ -n "$pids" ] || return 0

  # 종료 흔적을 어댑터 로그에 남긴다 — 나중에 "누가 죽였나"를 가르는 유일한 단서다.
  # (POSIX 시그널로는 송신자 pid 를 알 수 없다. 이 줄이 있으면 --stop, 없으면 pkill/Ctrl+C.)
  [ -f "$log" ] && echo "[robot-link] $(date -Is) 정지 요청 (pid $(echo $pids))" >> "$log"

  local pid i
  for pid in $pids; do
    # ⚠️ `|| true` 를 빼면 안 된다. 이 스크립트는 `set -eo pipefail` 아래에서 돈다.
    #   argv 확인과 kill 사이에 프로세스가 스스로 끝나면 kill 이 ESRCH 로 실패하고,
    #   errexit 이 그 자리에서 스크립트를 죽인다 — 남은 로봇들이 정리되지 않는다.
    #   "이미 죽었다" 는 정지 요청 입장에서 성공이지 실패가 아니다.
    kill "$pid" 2>/dev/null || true
  done
  # 종료를 **확인**한다. 안 그러면 "정지했다"고 말해놓고 프로세스가 남는다.
  for i in 1 2 3 4 5 6 7 8 9 10; do
    [ -z "$(_adapter_pids_for_key "$key")" ] && return 0
    sleep 0.5
  done
  for pid in $(_adapter_pids_for_key "$key"); do
    kill -KILL "$pid" 2>/dev/null || true      # 위와 같은 이유 — 이미 죽었으면 성공이다
  done
  sleep 0.5
  return 0
}

stop_one() { stop_by_key "$(key_of "$1")"; }

# 지금 돌고 있는 **모든** 어댑터의 키를 argv 에서 뽑는다.
#
# pid 파일은 힌트일 뿐이라 없을 수도 있다(파일 유실, /tmp 청소, 다른 TMPDIR 로 기동).
# "--all --stop 뒤에 어댑터 0개" 를 지키려면 파일이 아니라 **프로세스**가 기준이어야 한다.
_running_adapter_keys() {
  local pid argv i
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    [ -r "/proc/$pid/cmdline" ] || continue
    mapfile -d '' -t argv < "/proc/$pid/cmdline" 2>/dev/null || continue
    local is_adapter=0
    for i in "${argv[@]}"; do
      case "$i" in */robot_state_adapter.py|robot_state_adapter.py) is_adapter=1 ;; esac
    done
    [ "$is_adapter" = "1" ] || continue
    for ((i = 0; i < ${#argv[@]}; i++)); do
      if [ "${argv[i]}" = "--prefix" ] && [ -n "${argv[i+1]:-}" ]; then
        printf '%s\n' "${argv[i+1]#/}"       # "/pinkysim1" → "pinkysim1"
        break
      fi
    done
  done
  return 0     # 매치가 없어도 성공 — 위 stop_by_key 와 같은 errexit 함정을 피한다
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
  if [ "$ALL" = "1" ]; then
    # --all 은 DB 로 TARGETS 를 만들지만 **정지에는 쓰지 않는다.**
    #
    # 그리고 pid 파일만으로도 부족하다: pid 파일이 지워졌거나(/tmp 청소), 애초에
    # 안 써졌거나, 다른 TMPDIR 로 띄운 어댑터는 파일이 없어서 영영 안 잡힌다.
    # 그러면 "--all --stop 뒤에 어댑터 0개" 라는 약속이 깨진다.
    # 그래서 **실제로 돌고 있는 어댑터 프로세스의 argv 에서 키를 뽑아** 합친다.
    keys="$(
      for pf in "$STATE_DIR"/*.pid; do
        [ -e "$pf" ] || continue
        basename "$pf" .pid
      done
      _running_adapter_keys
    )"
    n=0
    for k in $(printf '%s\n' "$keys" | sort -u); do
      [ -n "$k" ] || continue
      stop_by_key "$k"
      n=$((n + 1))
    done
    echo "[robot-link] 정리 완료 (${n}대)"
  else
    for r in "${TARGETS[@]}"; do stop_one "$r"; done
    echo "[robot-link] 정리 완료 (${#TARGETS[@]}대)"
  fi
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
  # ⚠️ 여기에 시그널 트랩을 두지 않는다. 되살리지 말 것.
  #
  # 예전엔 `trap '... stop_one ...' INT TERM` 이 있었다. 그런데 이 창은 tmux 세션
  # libi_fms 의 일부라(fms_service.sh:108), 세션 종료나 FMS 재시작만으로 트랩이 발동해
  # **--all 대상 어댑터 전부**를 정지시켰다. sim.sh:132 가 자기 몫으로 띄운 어댑터도
  # 같은 pid 파일을 쓰므로 남의 트랩에 함께 쓸려갔다.
  #
  # 그러면 /robot_state 가 끊기고, fleet_node 는 (아직 한 번도 로봇을 못 봤다면)
  # 로봇을 0대로 보아 배차·순회를 시작조차 하지 않는다. 그런데 관제 패널은 amcl_pose 를
  # 직접 읽으므로 로봇이 정상으로 보인다.
  #
  # 규칙: **어댑터는 명시적 정지 요청(--stop, kill.sh)에만 멈춘다.**
  #       창이 닫히는 것은 정지 요청이 아니다.
  echo "[robot-link] 이 창을 닫아도 어댑터는 계속 돕니다."
  echo "[robot-link] 정지: ./scripts/laptop/robot-link.sh --all --stop  (또는 ./scripts/laptop/kill.sh)"
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
