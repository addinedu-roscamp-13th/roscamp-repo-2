#!/usr/bin/env bash
# kill.sh 의 kill_patterns_batch 가 매칭된 패턴 여러 개를 **동시에** 죽이는지 확인한다.
# 패턴마다 따로 최대 4초씩 순차로 기다리던 옛 kill_pattern 방식으로 돌아가면
# 이 시험이 타임아웃으로 잡는다(TERM 무시 프로세스 2개 = 순차면 ~8초, 동시면 ~4초).
#
# kill.sh 자체를 source 하지 않는다 — 맨 아래 tmux/curl/ros2 daemon stop 까지
# 실제로 실행돼 버린다. 함수 정의만 sed 로 떼어와 이 셸에 올린다.
#
#   ./test-kill.sh
set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
eval "$(sed -n '/^kill_patterns_batch()/,/^}/p' "$HERE/kill.sh")"

fail() { echo "FAIL: $*"; exit 1; }

# 1) 매칭 없으면 조용히 통과.
kill_patterns_batch TERM "zzq_absent_marker_$$"
echo "ok  매칭 없음 — 조용히 통과"

# 2) TERM 을 무시하는 프로세스 두 개 — 동시에 신호를 받고 동시에 KILL 폴백까지 가야 한다.
setsid bash -c 'trap "" TERM; exec sleep 9001' & P1=$!
setsid bash -c 'trap "" TERM; exec sleep 9002' & P2=$!
sleep 0.3

START=$(date +%s)
kill_patterns_batch TERM "sleep 9001" "sleep 9002" >/dev/null
ELAPSED=$(( $(date +%s) - START ))

pgrep -f "sleep 9001" >/dev/null 2>&1 && fail "sleep 9001 이 안 죽음"
pgrep -f "sleep 9002" >/dev/null 2>&1 && fail "sleep 9002 이 안 죽음"
# 이건 TERM 을 끝까지 무시하는 인위적 최악 케이스라 의도된 4초 유예를 항상 다 채운다
# (진짜 ROS 노드는 대부분 그 전에 죽어 훨씬 빨리 return 0 한다). 패턴별로 다시 돌면
# (예전 버전) 최소 8초. 느린 기기의 pgrep 자체 지연(pinky-3 실측 ~0.27초/회)까지 감안해도
# 10초 밑이면 "패턴마다 따로" 로 되돌아간 회귀는 아니라고 본다.
[ "$ELAPSED" -lt 10 ] || fail "너무 오래 걸림(${ELAPSED}s) — 패턴별로 다시 도는 건 아닌지 확인"
echo "ok  두 패턴을 동시에 종료 (${ELAPSED}s, 순차였다면 8s+)"
