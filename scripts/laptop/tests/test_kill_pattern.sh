#!/usr/bin/env bash
# _common.sh 의 kill_pattern / kill_patterns 가 **정말 프로세스를 죽이는가.**
#
# 보는 것은 결과다: 신호를 보냈는지가 아니라 대상이 사라졌는지를 본다.
# 이 함수들은 kill.sh 세 개(laptop·drive-pi·handy-pi)가 공유하므로, 조용히 망가지면
# 카메라 송출·추종 브리지가 살아남아 다음 기동이 포트 바인드 실패로 죽는다.
#
#   ./test_kill_pattern.sh
#
# ⚠️ 패턴 문자열을 이 파일 **안에만** 둔다. 호출한 셸의 argv 에 들어가면
#    `pkill -f` 가 그 셸까지 잡는다(이 테스트를 만들다 실제로 당했다).
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/../../_common.sh"

fail() { echo "FAIL: $*"; exit 1; }

# 1) 매칭이 없으면 조용히 통과해야 한다.
#    (pkill 은 매칭이 없으면 1 을 반환한다 — `set -e` 인 호출부가 여기서 죽으면 안 된다)
kill_patterns "zzq_absent_marker_$$"
echo "ok  매칭 없음 — set -e 아래서도 계속 진행"

# 2) TERM 으로 죽는 프로세스
sleep 4001 & P=$!
sleep 0.3
kill_pattern "sleep 4001" >/dev/null
kill -0 "$P" 2>/dev/null && fail "TERM 후에도 살아 있음"
echo "ok  SIGTERM 으로 종료"

# 3) TERM 을 무시하면 KILL 로 넘어가야 한다.
#    (perception_server 처럼 TERM 을 씹는 프로세스가 실제로 있다 — libi_gui 도 그렇다)
setsid bash -c 'trap "" TERM; exec sleep 4002' &
sleep 0.4
kill_pattern "sleep 4002" >/dev/null 2>&1
sleep 0.4
pgrep -f "sleep 4002" >/dev/null 2>&1 && fail "KILL 폴백이 동작하지 않음"
echo "ok  TERM 무시 → SIGKILL 폴백"

# 4) kill_patterns 다중 인자 — 하나도 빠뜨리면 안 된다.
sleep 4003 & sleep 4004 &
sleep 0.3
kill_patterns "sleep 4003" "sleep 4004" >/dev/null
pgrep -f "sleep 4003" >/dev/null 2>&1 && fail "다중 인자: 4003 생존"
pgrep -f "sleep 4004" >/dev/null 2>&1 && fail "다중 인자: 4004 생존"
echo "ok  다중 인자 전부 종료"

echo "PASS: kill_pattern / kill_patterns"
