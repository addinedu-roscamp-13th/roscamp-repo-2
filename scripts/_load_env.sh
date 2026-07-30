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
#       FSM_ROBOT_ID=pinky-sim-1 ROS_DOMAIN_ID=90 ./sim.sh
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

# ── CycloneDDS 유니캐스트 피어 — .env 의 IP 목록에서 만든다 ──────────────────
#
# 공유기가 멀티캐스트를 막아서 상대 IP 를 직접 줘야 서로 보인다. 그 목록이 예전엔
# `ros_ws/cyclonedds.xml` 과 각 머신 `~/.bashrc` 두 곳에 손으로 박혀 있었다. 로봇을
# 늘리면 거기에 IP 를 안 넣은 머신만 **에러 없이 서로 안 보인다** — 노드도 뜨고 로그도
# 깨끗한데 토픽만 영영 안 맞는다. 그래서 `.env` 한 곳에서 만든다.
#
# ⚠️ 셸/bashrc 가 이미 CYCLONEDDS_URI 를 정했으면 손대지 않는다(호출자 우선 — 위와 같은 규칙).
# ⚠️ .env 에 IP 가 하나도 없으면 아무것도 안 한다 → libi_pi.sh 의 file:// 폴백이 그대로 산다.
# ⚠️ 문자열(파일 아님)로 넘긴다 — ros_ws/README.md:115 에 이미 쓰던 형식이다.
_libi_ips=""
# `*_IP` 로 끝나는 변수가 곧 머신 IP 다(LAPTOP_IP·PINKY{N}_IP). 새 로봇을 .env 에
# 적기만 하면 여기 자동으로 낀다 — 이 파일은 안 고쳐도 된다.
#
# ⚠️ **여기 없는 IP 는 피어가 안 된다.** 손으로 관리하던 XML 에는 옛 망의 피어가
#    남아 있다(cyclonedds.xml 의 192.168.0.19 · 192.168.1.10). 그 머신들이 아직 필요하면
#    .env 에 아무 이름이나 `*_IP` 로 넣어라 — 예: `OLDFMS_IP=192.168.0.19`.
for _k in $(compgen -v 2>/dev/null | grep -E '_IP$' | sort); do
  _ip="${!_k}"
  case "$_ip" in ''|*[!0-9.]*) continue ;; esac          # 빈 값·호스트명은 건너뛴다
  case " $_libi_ips " in *" $_ip "*) continue ;; esac     # 중복 제거
  _libi_ips="$_libi_ips $_ip"
done

if [ -z "${CYCLONEDDS_URI:-}" ]; then
  if [ -n "$_libi_ips" ]; then
    _peers=""
    for _ip in $_libi_ips; do _peers="$_peers<Peer address=\"$_ip\"/>"; done
    # localhost 는 같은 머신 안의 프로세스끼리(nav2·fleet_link·bringup) 필수 — 빼지 말 것.
    export CYCLONEDDS_URI="<CycloneDDS><Domain id=\"any\"><General><Interfaces><NetworkInterface autodetermine=\"true\"/></Interfaces><AllowMulticast>false</AllowMulticast></General><Discovery><ParticipantIndex>auto</ParticipantIndex><Peers><Peer address=\"localhost\"/>$_peers</Peers></Discovery></Domain></CycloneDDS>"
    unset _peers
  fi
else
  # 셸/bashrc 가 이미 정한 값이 이긴다. 하지만 **거기 없는 .env IP 가 곧 조용한 실패**다
  # (노트북·로봇 둘 다 지금 ~/.bashrc 에서 export 하고 있어서, 로봇을 늘려도 .env 만으로는
  #  피어가 안 늘어난다). 그래서 빠진 것만 골라 한 줄 경고한다.
  # 이 머신 자신의 IP 는 뺀다 — 자기 자신은 피어 목록에 없어도 정상이다.
  _self=" $(hostname -I 2>/dev/null) "
  _missing=""
  for _ip in $_libi_ips; do
    case "$_self" in *" $_ip "*) continue ;; esac
    case "$CYCLONEDDS_URI" in *"$_ip"*) continue ;; esac
    _missing="$_missing $_ip"
  done
  [ -n "$_missing" ] && echo "[env] ⚠ 셸의 CYCLONEDDS_URI 에 없는 .env IP:$_missing" \
    "— 그 머신과는 DDS 가 조용히 안 붙는다. ~/.bashrc 의 CYCLONEDDS_URI export 를 지우면 .env 로 자동 구성된다." >&2
  unset _self _missing
fi
unset _libi_ips _k _ip
