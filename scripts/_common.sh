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

# .env 는 gitignore 라 없을 수 있다. 있으면 통째로 export.
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a
fi

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
  ROBOT_ID="${1:?로봇 이름이 필요합니다 (pinky1|pinky2|pinky3)}"
  local key
  key="$(echo "$ROBOT_ID" | tr '[:lower:]' '[:upper:]')_IP"   # PINKY3_IP
  ROBOT_IP="${!key:-}"
}

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

# 이 머신의 LAN IP 첫 번째. URL 안내에 쓴다.
lan_ip() { hostname -I 2>/dev/null | awk '{print $1}'; }

# 127.0.0.1:<port> 에 누가 듣고 있으면 0. 백엔드 중복 기동 방지에 쓴다.
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

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
