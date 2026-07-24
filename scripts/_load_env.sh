# 루트 .env 를 환경변수로 올린다. source 전용 (shebang·set 없음).
#
# 왜 별도 파일인가: 이 규칙이 필요한 곳이 `scripts/_common.sh` 만이 아니다.
# 서비스 기동 스크립트(예: aba_fms_service/backend/start.sh)도 직접 실행될 수 있는데,
# 그때 .env 가 안 실리면 **조용히 기능이 꺼진 채로 뜬다** — 실제로
# `LIBI_REAL_DISPATCH` 가 빠져 주문이 EXECUTING 인데 로봇은 안 움직이는 일이 있었다.
# 규칙을 복사하는 대신 여기 한 곳만 둔다.
#
# 호출 전에 REPO_ROOT 가 정해져 있으면 그 값을 쓰고, 없으면 이 파일 위치에서 역산한다.
#
# ⚠️ **명령줄/셸에서 이미 준 값은 덮어쓰지 않는다.**
#   `. .env` 는 파일 값으로 무조건 덮어쓴다. 그래서 예전에는
#       FSM_ROBOT_ID=Pinkysim ROS_DOMAIN_ID=90 ./sim.sh
#   처럼 앞에 붙여 줘도 .env 의 ROS_DOMAIN_ID(=실물 로봇용 119)로 바뀌어,
#   sim 은 119 에서 도는데 브릿지는 90 을 보는 상태가 됐다(FSM 연결 안 됨).
#   호출자가 명시한 값이 항상 이긴다 — 그게 덜 놀랍다.

: "${REPO_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if [ -f "$REPO_ROOT/.env" ]; then
  # 로드 전에 "이미 설정돼 있던" 키를 기억해 두고, 로드 후 되돌린다.
  _preset_keys=()
  _preset_vals=()
  while IFS='=' read -r _k _; do
    case "$_k" in ''|\#*) continue ;; esac
    _k="${_k%%[[:space:]]*}"
    if [ -n "${!_k+x}" ]; then
      _preset_keys+=("$_k")
      _preset_vals+=("${!_k}")
    fi
  done < "$REPO_ROOT/.env"

  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a

  for _i in "${!_preset_keys[@]}"; do
    export "${_preset_keys[$_i]}=${_preset_vals[$_i]}"
  done
  unset _preset_keys _preset_vals _i _k
fi
