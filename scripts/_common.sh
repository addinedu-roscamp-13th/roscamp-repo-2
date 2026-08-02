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

# 같은 이름의 ROS 노드가 **두 개 이상 뜨는 것**을 막는다.
#
# ## 왜 필요한가 — 실측 사고 2026-08-02
#
# `fleet_node`(배차·교통)가 **13시간 동안 두 개** 돌고 있었다. 13:36 에 띄운 것을
# 안 죽인 채 20:21 에 세션을 새로 만든 탓이다. 둘 다 살아서:
#
#   · 같은 로봇에 **서로 다른 CBS 예약**을 발급했다 — 교통관제가 통째로 깨진 상태
#   · 백엔드가 두 노드의 스냅샷을 번갈아 받아 관제 지도가 깜빡였다
#   · 마커가 지도 반대편으로 순간이동했다 (계획 번호는 같은데 노드 수가 9↔5)
#
# **에러가 한 줄도 안 났다.** ROS 2 는 같은 이름 노드의 중복을 막지 않는다.
# 증상만 보고는 AMCL·프론트 렌더링을 의심하게 되어 원인 추적이 아주 오래 걸린다.
#
# 그래서 기동 **전에** 끊는다. 죽이지는 않는다 — 돌던 노드가 지금 로봇을 통제
# 중일 수 있고, 그걸 말없이 죽이면 주행 중인 로봇의 예약이 풀린다.
# 사람이 무엇을 죽일지 정하도록 정확한 명령을 알려주고 멈춘다.
#
#   require_single_instance <패턴> <표시이름> [정리명령]
# 패턴에 맞는 **실제 프로세스** 개수. 셸은 세지 않는다.
#
# ⚠️ `pgrep -f` 는 명령줄 전체를 보므로 **검사하는 셸 자신도 잡는다.** 패턴이 인자로
#    들어가 있으면 자기 자신을 세어 "이미 돌고 있다"는 거짓 양성이 난다. 게다가
#    `$(...)` 커맨드 치환이 만드는 서브셸은 부모의 명령줄을 그대로 물려받아
#    조상 목록으로도 못 거른다(실측: 0개인데 2~3개로 셌다).
#
#    그래서 **프로세스 이름(comm)이 셸인 것을 뺀다.** 이 함수가 세려는 것은 언제나
#    실행 파일(ros2 노드·서버)이지 그것을 감싼 셸이 아니다.
#
# ⚠️ `pgrep -f` 의 패턴은 **정규식(ERE)** 이다. `(`·`)`·`.`·`*` 가 들어간 문자열을
#    그대로 넘기면 엉뚱하게 매칭된다. 경로처럼 메타문자 없는 패턴을 쓸 것.
# ⚠️ **한 인스턴스가 프로세스 두 개인 경우가 있다.** `ros2 run <pkg> <node>` 는 파이썬
#    런처(comm=`ros2`)를 띄우고 그 밑에 **실제 노드 바이너리**(comm=`fleet_node`)를 낳는다.
#    둘 다 cmdline 에 노드 이름이 들어 있어 그대로 세면 **1개가 2개로 잡힌다**
#    (실측 2026-08-02: `count_procs fleet_node` = 2, `ros2 node list` = 1).
#    거부 여부는 맞게 나오지만 "이미 2개 돌고 있습니다" 라는 **문구가 거짓**이 된다.
#
#    그래서 **부모도 같이 매칭된 프로세스는 세지 않는다.** 한 인스턴스는 한 번만 세어진다:
#      · `ros2 run` — 런처(부모)만 세고 노드(자식)는 접힌다 → 1
#      · `ros2 launch` — 런처만 매칭되면 그것 하나 → 1
#      · 진짜 중복 2벌 — 서로 부모-자식이 아니라 둘 다 세어진다 → 2 ✓
#    `ros2` 를 이름으로 빼는 방법은 안 쓴다. 패턴이 런처에만 걸리는 launch 파일에서
#    개수가 0 이 되어 **중복을 그냥 통과시킨다.**
_matched_pids() {
  local pattern="$1" pid comm
  for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
    comm="$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ')"
    [ -z "$comm" ] && continue
    case " $_NOT_A_PROCESS " in *" $comm "*) continue ;; esac
    printf '%s\n' "$pid"
  done
}

# 부모가 같은 목록에 있으면 접는다(위 주석). 남은 pid 만 찍는다.
_instance_pids() {
  local all ppid pid
  all="$(_matched_pids "$1")"
  [ -n "$all" ] || return 0
  for pid in $all; do
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    case "
$all
" in *"
$ppid
"*) continue ;; esac
    printf '%s\n' "$pid"
  done
}

_NOT_A_PROCESS="bash sh dash zsh pgrep ps grep tee timeout xargs"
count_procs() {
  _instance_pids "$1" | grep -c . || true
}

# `count_procs` 와 **같은 기준**으로 걸러 목록을 찍는다. 개수와 목록이 다르면
# 사람이 "2개라며 4줄이 뜨네" 하고 헷갈린다.
list_procs() {
  local pid
  for pid in $(_instance_pids "$1"); do
    ps -o pid=,lstart=,args= -p "$pid" 2>/dev/null | cut -c1-110
  done
}

require_single_instance() {
  local pattern="$1" label="$2" howto="${3:-}"
  local n
  n="$(count_procs "$pattern")"
  [ "$n" -eq 0 ] && return 0

  echo "[중복] ⚠ '$label' 이 이미 ${n}개 돌고 있습니다 — 새로 띄우면 **둘이 동시에 지휘**합니다."
  list_procs "$pattern" | sed 's/^/         /'
  echo "         같은 이름 ROS 노드가 둘이면 서로 다른 명령을 내보내고, **에러는 안 납니다.**"
  [ -n "$howto" ] && echo "         정리: $howto"
  die "'$label' 중복 기동을 막았습니다. 위 프로세스를 정리한 뒤 다시 실행하세요."
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
