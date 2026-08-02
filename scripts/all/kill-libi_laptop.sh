#!/usr/bin/env bash
# libi_laptop.sh 가 띄운 **로봇별 노트북 스택**(추종 AI · 터치패널) 정리.
# 서버 스택(DB·FMS·브릿지·fleet_node·도서관 웹)은 안 건드린다 — 그건 ./kill-libi_server.sh.
#
#   ./kill-libi_laptop.sh              # 로봇 전부 (libi_laptop_* 세션 모두)
#
# ⚠️ 인자 없이 부르면 **이 런처가 띄운 것만**이 아니다: 손으로 띄운 `libi_gui`·
#    `perception_server.py` 도 이름으로 쓸어담는다(포트를 물고 남으면 다음 기동이 즉사해서).
#    특정 로봇만 건드리려면 이름을 줘라 — 그 경로는 세션만 지운다.
#   ./kill-libi_laptop.sh pinky-3      # 그 로봇만 — 다른 로봇은 계속 돈다
#
# ## 관리자 추종 승인을 먼저 푸는 이유
#
# libi_gui 는 정상 종료 때 releaseFollowOnExit() 로 해제를 보낸다(RobotController.cpp).
# tmux 세션을 통째로 지우면 그게 못 돈다. 그러면 FMS 에 승인 기록만 살아남아, 다음에
# 패널에서 추종을 누르면 이렇게 거부당한다:
#
#     추종 승인 거부 — 관제에 이 로봇의 추종 승인 기록이 남아 있습니다. 먼저 해제하세요.
#
# 2026-07-27 하루에 두 번 겪었다. 그래서 **세션을 지우기 전에** :9001 로 해제를 보낸다
# (:9001 이 안 떠 있으면 해제할 대상 자체가 없으므로 조용히 건너뛴다).
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT="${1:-}"

release_grant() {   # <robot_id>
  curl -s --max-time 5 -X POST -H 'Content-Type: application/json' \
    -d "{\"robot_id\":\"$1\"}" \
    http://127.0.0.1:9001/api/robot/admin-follow/release >/dev/null 2>&1 \
    && echo "released admin-follow grant: $1"
}

# 남아 있는 승인 기록을 FMS 에서 읽어 전부 해제한다. 로봇 이름 없이 부르는 경로용 —
# 어차피 노트북 스택을 통째로 내리는 중이라 남겨둘 이유가 없다.
release_all_grants() {
  local ids rid
  ids="$(curl -s --max-time 3 http://127.0.0.1:9001/api/robot/admin-follow/status 2>/dev/null \
    | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for r in d.get("following", []):
    rid = r.get("robot_id")
    if rid:
        print(rid)' 2>/dev/null)" || return 0
  for rid in $ids; do release_grant "$rid"; done
}

if port_open 9001 && command -v curl >/dev/null 2>&1; then
  if [ -n "$ROBOT" ]; then release_grant "$ROBOT"; else release_all_grants; fi
fi

if [ -n "$ROBOT" ]; then
  SESSION="libi_laptop_$(robot_key "$ROBOT")"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    echo "killed tmux session: $SESSION (ai · gui)"
  else
    echo "'$SESSION' 세션이 없습니다 — 이미 정리됐거나 다른 이름으로 띄웠습니다."
  fi
  # ⚠️ perception_server 를 패턴으로 죽이지 않는다 — 패턴이 로봇을 구분하지 못해
  #    다른 로봇의 추종 서버까지 끊는다. 세션 안에서 돌던 것은 위에서 이미 내려갔다.
  echo "[kill] 서버 스택과 다른 로봇은 그대로 둡니다 — 서버 정리는 $HERE/kill-libi_server.sh"
  exit 0
fi

# 로봇 전부. 대수가 늘어도 목록을 고칠 필요가 없게 이름 규칙으로 훑는다.
# `libi_laptop_*` 와 단수 `libi_laptop` 은 이름을 바꾸기 전 세션이라 같이 본다 —
# 옛 스크립트로 띄워 둔 게 남아 있을 수 있다.
for s in $(tmux ls -F "#{session_name}" 2>/dev/null \
           | grep -E "^(libi_laptop(_|$)|libi_laptop_)" || true); do
  tmux kill-session -t "$s" 2>/dev/null && echo "killed tmux session: $s (ai · gui)"
done

# 터치패널은 tmux 밖(사용자 터미널 exec)에서 뜬 것도 있어 프로세스로 잡는다.
"$REPO_ROOT/scripts/ui/kill.sh" gui || true

# 세션 밖에서 손으로 띄운 추종 서버·릴레이 stub. 전부 내리는 경로라 로봇을 구분할 필요가 없다.
# ⚠️ 이것들은 포그라운드로 뜨는 경우가 많아 세션 정리로는 안 잡힌다. 남으면 UDP/TCP 포트를
#    물고 있어 다음 기동이 바인드 실패로 즉사한다("왜 영상이 안 뜨지"로 나타난다).
kill_patterns "perception_server.py" "aba_ai_service/main.py"

echo "[kill] 서버 스택(FMS·브릿지·도서관 웹)은 그대로 둡니다 — 정리는 $HERE/kill-libi_server.sh"

# ── fleet_node 중복 감시 ─────────────────────────────────────────────────────
#
# ⚠️ **여기서 죽이지는 않는다.** fleet_node 는 서버 스택 소관이고(위 줄), 지금 로봇을
#    통제 중일 수 있다. 말없이 죽이면 주행 중인 로봇의 예약이 풀린다.
#
# 대신 **중복만 알린다.** 실측 사고 2026-08-02: `kill-libi_laptop.sh` 로 노트북만
# 내리고 서버 세션을 새로 띄우기를 반복하다 fleet_node 가 **13시간 동안 두 개** 돌았다.
# 둘이 같은 로봇에 서로 다른 CBS 예약을 발급해 교통관제가 깨졌는데 **에러가 한 줄도
# 안 났다** — 관제 지도 깜빡임·마커 순간이동으로만 드러나 원인 추적에 오래 걸렸다.
#
# 이 스크립트가 그 사고의 길목이라 여기에 그물을 둔다. 기동 쪽 차단은
# `scripts/laptop/fms_service.sh` 의 `require_single_instance` 가 맡는다.
_fleet_n="$(count_procs "libi_fleet/lib/libi_fleet/fleet_node")"
[ -z "$_fleet_n" ] && _fleet_n=0
if [ "$_fleet_n" -gt 1 ]; then
  echo "[kill] ⚠⚠ fleet_node 가 ${_fleet_n}개 돌고 있습니다 — **둘이 동시에 지휘 중입니다.**"
  list_procs "libi_fleet/lib/libi_fleet/fleet_node" | sed 's/^/         /'
  echo "         같은 로봇에 서로 다른 예약이 나가 관제 지도가 깜빡이고 순회가 어긋납니다."
  echo "         정리: $HERE/kill-libi_server.sh   (그 뒤 서버 스택을 다시 올리세요)"
fi
