#!/usr/bin/env bash
# robot-link.sh / kill.sh 의 **외부 동작**만 검사한다 — 트랩이 있는지 같은 내부는 보지 않는다.
#
#   케이스 1: 관리 셸(--foreground)이 죽어도 어댑터는 살아 있는가        ← R2
#   케이스 2: 명시적 정지 후 프로세스와 pid 파일이 0 인가                 ← R3
#   케이스 3: 정지가 남의 프로세스를 죽이지 않는가 (pid 재사용 방어)      ← R13
#   케이스 4: 정지 흔적이 어댑터 로그에 남는가                            ← R12
#
# bats 를 쓰지 않는다(이 머신에 없고 sudo 없이 깔 수 없다). 순수 bash + 종료코드면 충분하다.
# 실제 상태 디렉토리(/tmp/libi-robot-link)와 실제 도메인을 오염시키지 않도록
# TMPDIR 과 LIBI_FMS_DOMAIN 을 테스트 전용 값으로 덮어쓴다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
LINK="$REPO/scripts/laptop/robot-link.sh"

ROS_SETUP=/opt/ros/jazzy/setup.bash
FLEET_SETUP="$REPO/aba_fms_service/fleet_ws/install/setup.bash"
if [ ! -f "$ROS_SETUP" ] || [ ! -f "$FLEET_SETUP" ]; then
  echo "SKIP: ROS2 Jazzy 또는 fleet_ws 빌드가 없다"
  exit 0
fi

FAILED=0
pass_case() { echo "  ✅ $1"; }
fail_case() { echo "  ❌ $1"; FAILED=1; }

TESTDIR="$(mktemp -d)"
export TMPDIR="$TESTDIR"                 # robot-link.sh 의 STATE_DIR 가 여기로 온다
export LIBI_FMS_DOMAIN=98                # 실제 fleet 도메인(86) 회피
STATE_DIR="$TESTDIR/libi-robot-link"
ROBOT="LifecycleProbe-1"

# ⚠️ 키를 하드코딩하지 않는다. `key_of()` 는 구분자를 지운 뒤 **첫 글자만** 소문자로
#    바꾸므로 "LifecycleProbe-1" → "lifecycleProbe1" 이다(전부 소문자가 아니다).
#    규칙을 여기 복제하면 규칙이 바뀔 때 조용히 어긋나고, 테스트는 pid 파일을 못 찾아
#    "어댑터 기동 실패" 라는 **엉뚱한 이유로** 죽는다. 그래서 상태 디렉토리에서 찾아낸다 —
#    이 디렉토리는 이 테스트 전용이라 여기 있는 .pid 는 우리 어댑터의 것뿐이다.
key_from_state_dir() {
  local pf
  for pf in "$STATE_DIR"/*.pid; do
    [ -e "$pf" ] || continue
    basename "$pf" .pid
    return 0
  done
  return 1
}

cleanup_all() {
  pkill -9 -f "robot_state_adapter.py --robot $ROBOT" 2>/dev/null
  rm -rf "$TESTDIR"
  return 0
}
trap cleanup_all EXIT

adapter_pid() {
  local pf
  for pf in "$STATE_DIR"/*.pid; do
    [ -e "$pf" ] || continue
    cat "$pf" 2>/dev/null
    return 0
  done
  return 1
}

adapter_alive() {
  local p; p="$(adapter_pid)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

# pid 파일 존재만으로는 부족하다 — start_one 은 실패해도 pid 파일을 남긴다.
# 그래서 **살아 있는 것**까지 확인한다. 안 그러면 죽은 어댑터로 테스트가 통과한다.
wait_for_adapter() {           # $1=초
  local deadline=$((SECONDS + $1))
  while [ $SECONDS -lt $deadline ]; do
    adapter_alive && return 0
    sleep 0.5
  done
  return 1
}

echo "[test] 케이스 1 — 관리 셸이 죽어도 어댑터는 산다 (R2)"

"$LINK" "$ROBOT" --foreground > "$TESTDIR/fg.log" 2>&1 &
FG_PID=$!

if ! wait_for_adapter 40; then
  fail_case "어댑터가 기동하지 못했다(또는 즉시 죽었다)"
  cat "$TESTDIR/fg.log"
  exit 1
fi
ADAPTER_PID="$(adapter_pid)"
KEY="$(key_from_state_dir)"              # 실제로 만들어진 키 (하드코딩하지 않는다)
pass_case "어댑터 기동 확인 (pid $ADAPTER_PID, key $KEY)"

# adapter_alive 만으로는 robot-link.sh 가 트랩(또는 그 자리를 대신하는 안내문) 설치
# 지점까지 실제로 도달했는지 알 수 없다 — start_one 내부의 `sleep 2` 가 끝나기 전에
# pid 파일이 먼저 쓰이므로, wait_for_adapter 는 트랩이 설치되기 한참 전에 이미 성공한다.
# 그 상태로 바로 SIGTERM 을 보내면 스크립트가 트랩 설치 줄에 도달하기도 전에(기본 시그널
# 처리로) 죽어버려, 트랩이 있든 없든 항상 "생존"으로 보이는 거짓 통과가 나온다(실측 확인함).
# 그래서 트랩 설치 직전 줄("[robot-link] 로그:")이 fg.log 에 찍히는 것을 먼저 기다린다.
for _ in $(seq 1 20); do
  grep -q '\[robot-link\] 로그:' "$TESTDIR/fg.log" 2>/dev/null && break
  sleep 0.5
done

# 창이 닫히는 상황 = 관리 셸에 SIGTERM.
kill -TERM "$FG_PID" 2>/dev/null
wait "$FG_PID" 2>/dev/null

sleep 3        # 트랩이 있다면 이 사이에 어댑터가 죽는다
if kill -0 "$ADAPTER_PID" 2>/dev/null; then
  pass_case "관리 셸 종료 후에도 어댑터 생존 (pid $ADAPTER_PID)"
else
  fail_case "관리 셸이 죽자 어댑터도 죽었다 — 암묵적 정지 경로가 남아 있다"
fi

echo
echo "[test] 케이스 2 — 명시적 정지 후 아무것도 남지 않는다 (R3)"

# 케이스 1이 남긴 어댑터가 살아 있어야 한다. 죽어 있으면 정지 테스트가 무의미하다.
if ! adapter_alive; then
  "$LINK" "$ROBOT" > "$TESTDIR/start2.log" 2>&1
  if ! wait_for_adapter 40; then
    fail_case "케이스 2 준비 실패 — 어댑터 기동 불가"
    cat "$TESTDIR/start2.log"
  fi
fi
STOP_TARGET="$(adapter_pid)"
if [ -z "$STOP_TARGET" ] || ! kill -0 "$STOP_TARGET" 2>/dev/null; then
  fail_case "정지 대상 어댑터가 살아 있지 않다 — 이 케이스는 아무것도 검증하지 못한다"
else
  # --all 은 DB 를 조회한다. 이 테스트 로봇은 DB 에 없다.
  # 그래도 정지는 반드시 되어야 한다 — 정지 대상은 pid 파일이 정본이기 때문이다.
  "$LINK" --all --stop > "$TESTDIR/stop.log" 2>&1
  STOP_RC=$?
  sleep 2

  if [ "$STOP_RC" != "0" ]; then
    fail_case "--all --stop 이 0 이 아닌 코드로 끝났다 (rc=$STOP_RC)"
  elif kill -0 "$STOP_TARGET" 2>/dev/null; then
    fail_case "--all --stop 후에도 어댑터가 살아 있다 (pid $STOP_TARGET) — DB 조회에 묶여 있다"
  else
    pass_case "--all --stop 후 어댑터 프로세스 0개"
  fi

  LEFTOVER="$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)"
  if [ "$LEFTOVER" = "0" ]; then
    pass_case "--all --stop 후 pid 파일 0개"
  else
    fail_case "pid 파일이 $LEFTOVER 개 남았다"
  fi
fi

echo
echo "[test] 케이스 3 — 정지가 남의 프로세스를 죽이지 않는다 (R13, pid 재사용 방어)"

# 어댑터가 아닌 프로세스의 pid 를 pid 파일에 심어 둔다. 신원 확인이 없으면 이걸 죽인다.
# 키는 케이스 1 에서 실제로 만들어진 것을 쓴다(하드코딩 금지 — key_of 규칙은 첫 글자만 소문자다).
sleep 300 &
VICTIM=$!
mkdir -p "$STATE_DIR"
echo "$VICTIM" > "$STATE_DIR/${KEY:-probefallback}.pid"

"$LINK" --all --stop > "$TESTDIR/stop_victim.log" 2>&1
sleep 1

if kill -0 "$VICTIM" 2>/dev/null; then
  pass_case "무관한 프로세스(pid $VICTIM)를 죽이지 않았다"
else
  fail_case "무관한 프로세스(pid $VICTIM)를 죽였다 — pid 신원 검증이 없다"
fi
kill -9 "$VICTIM" 2>/dev/null
wait "$VICTIM" 2>/dev/null

if [ "$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)" != "0" ]; then
  fail_case "신원 불일치 pid 파일이 정리되지 않았다"
else
  pass_case "신원 불일치 pid 파일은 제거됐다"
fi

echo
echo "[test] 케이스 4 — kill.sh 가 어댑터 정리를 tmux 종료 **뒤에** 호출한다 (R3 순서)"
KILLSH="$REPO/scripts/laptop/kill.sh"
TMUX_LINE="$(grep -n 'tmux kill-session' "$KILLSH" | head -1 | cut -d: -f1)"
STOP_LINE="$(grep -n 'robot-link.sh.*--stop' "$KILLSH" | head -1 | cut -d: -f1)"
if [ -z "$STOP_LINE" ]; then
  fail_case "kill.sh 가 어댑터를 명시적으로 정리하지 않는다"
elif [ -z "$TMUX_LINE" ]; then
  fail_case "kill.sh 에서 tmux kill-session 을 찾지 못했다"
elif [ "$STOP_LINE" -lt "$TMUX_LINE" ]; then
  fail_case "어댑터 정리($STOP_LINE)가 tmux 종료($TMUX_LINE)보다 앞선다 — 워치독과 경쟁한다"
else
  pass_case "kill.sh 순서: tmux 종료($TMUX_LINE) → 어댑터 정리($STOP_LINE)"
fi

echo
echo "[test] 케이스 5 — 로봇이 여러 대여도 --all --stop 이 전부 정리한다 (다중 로봇 경로)"
# ⚠️ 케이스 1~4 는 로봇이 **한 대**뿐이라, 다중 로봇에서만 나는 고장을 구조적으로 못 본다.
#    실제 배포는 주행 로봇 3대다. /proc 열거의 마지막 항목이 "다른 로봇의" 어댑터일 때
#    _adapter_pids_for_key 가 1 을 반환하고 errexit 이 스크립트를 죽이면,
#    --all --stop 이 중간에 끊겨 나머지 로봇 어댑터가 살아남는다. 여기서 그걸 잡는다.
ROBOT_B="LifecycleProbe-2"
"$LINK" "$ROBOT" > "$TESTDIR/m_a.log" 2>&1
"$LINK" "$ROBOT_B" > "$TESTDIR/m_b.log" 2>&1
sleep 2
BEFORE="$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)"
if [ "$BEFORE" != "2" ]; then
  fail_case "다중 로봇 준비 실패 — pid 파일 $BEFORE 개 (2 개여야 함)"
  cat "$TESTDIR/m_a.log" "$TESTDIR/m_b.log"
else
  "$LINK" --all --stop > "$TESTDIR/m_stop.log" 2>&1
  MRC=$?
  sleep 2
  ALIVE="$(pgrep -f "robot_state_adapter.py --robot LifecycleProbe-" 2>/dev/null | wc -l)"
  AFTER="$(ls "$STATE_DIR"/*.pid 2>/dev/null | wc -l)"
  if [ "$MRC" != "0" ]; then
    fail_case "--all --stop 이 중간에 죽었다 (rc=$MRC) — errexit 로 정리가 끊긴다"
    tail -3 "$TESTDIR/m_stop.log"
  elif [ "$ALIVE" != "0" ]; then
    fail_case "정지 후에도 어댑터 $ALIVE 개 생존 — 다중 로봇 정리가 불완전하다"
  elif [ "$AFTER" != "0" ]; then
    fail_case "정지 후 pid 파일 $AFTER 개 잔여"
  else
    pass_case "로봇 2대 → --all --stop → 프로세스 0개, pid 파일 0개, rc=0"
  fi
fi
pkill -9 -f "robot_state_adapter.py --robot LifecycleProbe-" 2>/dev/null

echo
if [ "$FAILED" = "0" ]; then
  echo "[test] 전부 통과"
else
  echo "[test] 실패 있음"
fi
exit "$FAILED"
