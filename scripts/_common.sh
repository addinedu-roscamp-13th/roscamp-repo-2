# scripts/{pi,laptop}/*.sh 가 맨 위에서 source 하는 공통 헬퍼.
# 단독 실행용이 아니다 — source 전용이라 shebang 도 set 도 두지 않는다.
#
# 하는 일:
#   - REPO_ROOT 계산 (자기 위치 역산 — 하드코딩·git 탐지 없음)
#   - .env 로드 (있으면) → LAPTOP_IP / PINKY{N}_IP 등을 환경변수로
#   - resolve_pinky <이름>  → ROBOT_ID / ROBOT_IP 세팅
#   - ensure_built <워크스페이스>  → install/ 없으면 colcon build
#   - die / need
#
# 어느 스크립트든 시작에서 `cd "$REPO_ROOT"` 하면 실행 위치와 무관하게 동작한다
# (이번까지 반복됐던 "다른 폴더에서 실행하면 cd 에러" 문제의 근원 제거).

# scripts/_common.sh 는 루트 바로 아래 → 한 단계 위가 REPO_ROOT.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# .env 로드. 규칙(셸에서 이미 준 값이 이긴다)은 _load_env.sh 한 곳에만 둔다 —
# 서비스 기동 스크립트들도 같은 파일을 쓴다.
# shellcheck disable=SC1091
. "$REPO_ROOT/scripts/_load_env.sh"

die() { echo "[$(basename "${0:-scripts}")] $*" >&2; exit 1; }

# 파일/디렉터리가 없으면 이유를 붙여 죽는다.
need() { [ -e "$1" ] || die "${2:-없음}: $1"; }

# 명령(도구)이 없으면 설치 안내와 함께 죽는다.  need_cmd colcon "sudo apt install ..."
need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' 명령이 없습니다. ${2:-먼저 설치하세요.}"
}

# 파이썬 모듈이 (지정 인터프리터로) import 되는지 확인. 없으면 설치 안내.
#   need_py_module <module> "<설치 안내>" [python]
need_py_module() {
  local mod="$1" hint="$2" py="${3:-python3}"
  "$py" -c "import $mod" >/dev/null 2>&1 || die "파이썬 모듈 '$mod' 이 없습니다($py). ${hint:-설치가 필요합니다.}"
}

# pinky3 → ROBOT_ID=pinky3, ROBOT_IP=$PINKY3_IP (.env 에서).
resolve_pinky() {
  ROBOT_ID="${1:?로봇 이름이 필요합니다 (예: pinky3 · pinky-3)}"
  local key
  # ⚠️ 영숫자만 남긴다. 셸 변수명에는 `-` 를 쓸 수 없어서, DB 이름(`pinky-3`)을 그대로
  # 넣으면 `PINKY-3_IP` 가 되어 `invalid variable name` 으로 죽었다.
  # 이제 `pinky3` 든 `pinky-3` 든 같은 키(PINKY3_IP)로 간다.
  key="$(printf '%s' "$ROBOT_ID" | tr -cd '[:alnum:]' | tr '[:lower:]' '[:upper:]')_IP"
  if [ -z "$key" ] || [ "$key" = "_IP" ]; then
    die "로봇 이름에서 IP 키를 만들 수 없습니다: '$ROBOT_ID'"
  fi
  ROBOT_IP="${!key:-}"
}

# 로봇 이름 → 노트북(AI 서버) 수신 포트. 로봇마다 10 씩 띄운다.
#
# 왜 필요한가: perception_server 의 수신 포트는 **노트북 한 대에 한 벌뿐**이다
# (영상 UDP 6001 · 뷰어 TCP 5007 — perception_server.py 기본값). 앞뒤 캠은 한 포트를
# 공유한다(로봇 camera_sender 가 `/libi/camera_select` 로 고른 것만 보낸다).
# 로봇 2대를 동시에 추종하면 두 번째가 bind 에서 죽는다.
#
# 로봇 쪽(image-sender)과 노트북 쪽(ai-server)이 **같은 규칙으로 각자 계산**하므로
# 서로 포트를 넘겨줄 필요가 없다 — 로봇 이름만 맞으면 맞는다.
#
#   pinky-1 → 6001/5007    pinky-2 → 6011/5017    pinky-3 → 6021/5027
#
# ⚠️ 로봇이 한 대뿐이던 시절 pinky-3 은 6001/5007 이었다. 이 규칙에선 6021/5027 이다.
#    양쪽 스크립트가 같이 계산하니 어긋나지 않지만, 손으로 `ros2`/`nc` 를 칠 때는
#    포트가 바뀐 걸 기억해야 한다.
# ⚠️ 셸/env 에 이미 값이 있으면 그게 이긴다(진단용 수동 지정).
robot_ports() {
  local num off
  num="$(printf '%s' "${1:-}" | grep -oE '[0-9]+' | tail -1)"
  num="${num:-1}"
  off=$(( (num - 1) * 10 ))
  # ⚠️ 셸/.env 에 이미 값이 있으면 그게 이긴다(진단용 수동 지정). 다만 **로봇마다 계산되는
  #    값을 덮어쓰는** 것이라, .env 에 박아 두면 로봇 2대가 같은 포트를 쓰게 된다.
  #    조용히 그러지 않도록 한 줄 찍는다.
  if [ -n "${VIDEO_PORT:-}${VIEWER_PORT:-}" ]; then
    echo "[ports] ⚠ 셸/.env 의 VIDEO_PORT/VIEWER_PORT 를 씁니다 (${VIDEO_PORT:-자동}/${VIEWER_PORT:-자동})" >&2
    echo "        로봇마다 자동으로 갈리는 값입니다 — 2대 이상이면 같은 포트를 물 수 있습니다." >&2
  fi
  VIDEO_PORT="${VIDEO_PORT:-$((6001 + off))}"
  VIEWER_PORT="${VIEWER_PORT:-$((5007 + off))}"
  export VIDEO_PORT VIEWER_PORT
}

# 로봇 이름 → ROS 도메인. **표가 코드에 박혀 있다.**
#
#   pinky-1 → 117    pinky-2 → 118    pinky-3 → 119        (규칙: 116 + 번호)
#
# 왜 하드코딩인가: IP 는 DHCP 라 자주 바뀌지만 **도메인은 배선처럼 고정**이다. 매번 손으로
# 치게 하면 오타 하나로 브릿지가 로봇을 못 찾는데 에러는 안 난다. 예전에 `.env` 의
# ROS_DOMAIN_ID(=119 하나)를 기본값으로 쓰던 것과는 다르다 — 그건 **모든 로봇에 같은 값**이라
# 2대째부터 반드시 틀렸다. 이 표는 로봇마다 다른 값이라 그 실패 자체가 없다.
#
# ⚠️ 로봇 Pi 의 ROS_DOMAIN_ID 와 DB(rc_robots.domain_id)도 같은 값이어야 한다.
#    셋 중 하나만 어긋나면 텔레메트리·FSM 이 관제에 영영 안 올라온다(에러 없음).
#    다른 번호를 써야 하면 `--domain-id` 로 덮어쓴다.
# ⚠️ **실물 `pinky-<N>` 에만 적용한다.** `pinky-sim-2` 같은 이름까지 끝자리로 계산하면
#    118 이 나오는데 sim 은 DB 에 90/91/92 다 — 실물 pinky-2 와 도메인이 겹친다.
#    표 밖 이름은 빈 값을 돌려주고, 호출부가 DB 값으로 간다.
DOMAIN_BASE=116
robot_domain() {
  local name="${1:-}" num
  case "$name" in
    [Pp]inky-[0-9]|[Pp]inky[0-9]|[Pp]INKY-[0-9]) ;;      # pinky-3 · pinky3 · PINKY-3
    *) return 0 ;;
  esac
  num="${name##*[!0-9]}"
  printf '%s' "$((DOMAIN_BASE + num))"
}

# 로봇 이름 → DB 에 등록된 도메인 (rc_robots.domain_id). 못 찾으면 빈 문자열.
# 위 표와 **대조**하는 데 쓴다 — 둘이 다르면 브릿지는 DB 값으로 열리므로 그쪽이 진실이다.
#
# 왜 DB 인가: 도메인 브릿지도 여기서 나온다(gen_domain_bridges.py). 런처가 .env 의
# `ROS_DOMAIN_ID`(**로봇 한 대 기준값**)를 기본으로 쓰면, 로봇이 늘어난 순간
# pinky-1 을 119(=pinky-3 도메인)로 띄우는 사고가 난다 — 브릿지는 117 을 여니
# 영영 안 붙고, 에러는 안 난다. 출처가 하나여야 그 부류가 사라진다.
#
# pymysql 이 있는 파이썬을 찾는다(시스템 python3 엔 보통 없다). 못 찾거나 DB 가
# 안 떠 있으면 조용히 빈 값 — 호출자가 셸/.env 로 폴백하면 된다.
robot_domain_db() {
  local c py=""
  for c in "$REPO_ROOT/aba_fms_service/backend/.venv/bin/python" \
           "$REPO_ROOT/aba_service/backend/.venv/bin/python" \
           "$REPO_ROOT/.venv/bin/python" python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import pymysql" 2>/dev/null; then py="$c"; break; fi
  done
  [ -n "$py" ] || return 0
  "$py" - "$1" <<'PY' 2>/dev/null
import os, sys, urllib.parse
url = os.environ.get("ROBOT_DATABASE_URL") or os.environ.get("ADMIN_DATABASE_URL") or ""
try:
    import pymysql
    u = urllib.parse.urlparse(
        url.replace("+asyncmy", "").replace("+aiomysql", "").replace("+pymysql", ""))
    conn = pymysql.connect(host=u.hostname or "127.0.0.1", port=u.port or 3306,
                           user=u.username or "", password=u.password or "",
                           database=(u.path or "/").lstrip("/"), connect_timeout=4)
    cur = conn.cursor()
    cur.execute("SELECT domain_id FROM rc_robots WHERE name=%s AND robot_type='pinky'", (sys.argv[1],))
    row = cur.fetchone()
    conn.close()
    if row and row[0] is not None:
        print(int(row[0]))
except Exception:
    pass
PY
}

# 로봇 이름 → tmux 세션·파일 이름에 쓸 키. `pinky-3` → `pinky3`
# (gen_domain_bridges.py 의 bridge_key 와 같은 규칙이라 브릿지 토픽 접두사와도 맞는다.)
robot_key() { printf '%s' "${1:-}" | tr -d '_ -' | tr '[:upper:]' '[:lower:]'; }

# 워크스페이스가 안 빌드돼 있으면(=install/setup.bash 없음) colcon build.
# 이미 빌드돼 있으면 아무것도 안 한다 — 매번 재빌드하지 않는다.
ensure_built() {
  local ws="$1"
  need "$ws" "워크스페이스"
  if [ -f "$ws/install/setup.bash" ]; then
    return 0
  fi
  need_cmd colcon "sudo apt install -y python3-colcon-common-extensions"
  [ -f /opt/ros/jazzy/setup.bash ] || die "ROS2 jazzy 가 없습니다(/opt/ros/jazzy/setup.bash). ROS2 를 먼저 설치하세요."
  echo "[build] $ws 미빌드 → colcon build (한 번만)"
  ( source /opt/ros/jazzy/setup.bash && cd "$ws" && colcon build ) \
    || die "colcon build 실패: $ws  (수동: cd $ws && colcon build)"
}

# 프론트엔드 node_modules 없으면 npm install (한 번만).
ensure_npm() {
  local dir="$1"
  need "$dir" "프론트엔드 디렉터리"
  if [ -d "$dir/node_modules" ]; then
    return 0
  fi
  need_cmd npm "Node.js 설치가 필요합니다 (nvm 또는 sudo apt install -y nodejs npm)"
  echo "[npm] $dir → npm install (한 번만)"
  ( cd "$dir" && npm install ) || die "npm install 실패: $dir  (수동: cd $dir && npm install)"
}

# 백엔드 .venv 가 없으면 만들고 requirements.txt 설치. 있으면 그대로 둔다.
# (fms backend/start.sh 는 자체적으로 이걸 하지만, aba_service run.sh 는 안 해서 여기서 보장한다.)
ensure_venv() {
  local dir="$1"
  need "$dir" "백엔드 디렉터리"
  if [ -x "$dir/.venv/bin/python" ]; then
    return 0
  fi
  need_cmd python3 "sudo apt install -y python3-venv"
  echo "[venv] $dir/.venv 생성 (한 번만)"
  ( cd "$dir" && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip ) \
    || die "venv 생성 실패: $dir"
  if [ -f "$dir/requirements.txt" ]; then
    echo "[venv] requirements.txt 설치..."
    ( cd "$dir" && .venv/bin/pip install -q -r requirements.txt ) \
      || die "requirements 설치 실패: $dir  (수동: cd $dir && .venv/bin/pip install -r requirements.txt)"
  fi
}

# MariaDB(시스템 서비스)가 안 떠 있으면 시작한다. 이미 떠 있으면 아무것도 안 한다.
# systemctl start 는 sudo 필요 — 비밀번호 프롬프트가 뜰 수 있다(인터랙티브 실행 전제).
ensure_mariadb() {
  systemctl is-active --quiet mariadb 2>/dev/null && return 0
  echo "[db] MariaDB 가 안 떠 있음 → 시작 시도 (sudo 비밀번호가 필요할 수 있습니다)"
  sudo systemctl start mariadb \
    || die "MariaDB 시작 실패. 수동: sudo systemctl start mariadb"
}

# 이 머신의 LAN IP 첫 번째. URL 안내에 쓴다.
lan_ip() { hostname -I 2>/dev/null | awk '{print $1}'; }

# 127.0.0.1:<port> 에 누가 듣고 있으면 0. 백엔드 중복 기동 방지에 쓴다.
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# cmdline 패턴으로 프로세스를 종료한다. TERM → 최대 4초 대기 → KILL.
#
# TERM 을 먼저 주는 이유: 소켓(UDP:6001·TCP:5007 등)을 닫고 나가야 포트가 즉시 풀린다.
# 곧바로 -9 로 죽이면 포트가 잠시 물려 있어 다음 기동이 바인드 실패로 즉사한다.
# ROS 노드라면 DDS 정상 탈퇴까지 겸한다(안 그러면 `ros2 node list` 에 유령이 남는다).
#
# ⚠️ `pgrep/pkill -f` 는 **cmdline 전체**를 본다. 패턴이 넓으면 무관한 프로세스까지
#    잡으므로 반드시 파일명 이상으로 좁힌다(예: "main.py" ✗ / "aba_ai_service/main.py" ✓).
#    호출한 셸의 argv 에 패턴 문자열이 들어 있으면 **그 셸까지 죽는다** — 실측으로 겪었다.
#
# ⚠️ `|| true` 가 붙은 이유: 호출부가 `set -e` 라, 매칭이 사라진 순간의 pkill 실패(1)로
#    스크립트 전체가 조용히 끝나버린다.
kill_pattern() {
  local pattern="$1"
  pgrep -f "$pattern" >/dev/null 2>&1 || return 0

  pkill -TERM -f "$pattern" 2>/dev/null || true
  echo "SIGTERM: $pattern"

  for _ in $(seq 1 8); do
    pgrep -f "$pattern" >/dev/null 2>&1 || return 0
    sleep 0.5
  done

  pkill -KILL -f "$pattern" 2>/dev/null || true
  echo "SIGKILL(잔여): $pattern"
}

# kill_pattern 을 여러 개 돌리고, **정말 사라졌는지 확인한다.**
# "죽였다" 와 "포트가 비었다" 는 다르고, 다음 기동이 실패하는 건 후자다.
kill_patterns() {
  local p
  for p in "$@"; do kill_pattern "$p"; done
  for p in "$@"; do
    if pgrep -f "$p" >/dev/null 2>&1; then
      echo "[kill] ⚠ '$p' 가 아직 살아 있습니다 — 확인: pgrep -af '$p'"
    fi
  done
}

# tmux 세션에 붙는다 — 단, 인터랙티브(TTY)일 때만. 백그라운드(비-TTY)로 실행하면
# 'tmux attach' 가 "not a terminal" 로 죽으므로, 세션은 그대로 두고 안내만 한다.
# 세션·서비스는 이 함수 전에 이미 new-session -d 로 떠 있어 백그라운드에서도 정상 동작한다.
tmux_attach() {
  if [ -t 1 ]; then
    tmux attach -t "$1"
  else
    echo "[bg] '$1' 세션이 백그라운드로 떴습니다. 붙으려면: tmux attach -t $1"
  fi
}
