#!/usr/bin/env bash
# 로봇(Pi) 한 방 진단 — "지금 왜 이러지"에 필요한 것만 한 화면에.
#
#   ssh pinky@192.168.1.11 'bash /home/roscamp-repo-2/scripts/all/diag-pi.sh'
#   bash diag-pi.sh 40        # 창별 로그를 40줄까지 (기본 12)
#
# ## 왜 스크립트로 굳혔나 (2026-07-30)
#
# 하루 동안 같은 조사를 열 번 넘게 손으로 다시 만들었고, 매번 같은 함정에 걸렸다.
# 그 함정들을 여기 박아 둔다 — 다시 안 걸리게.
#
#   ① `tmux capture-pane` 은 **창 폭에서 줄바꿈된 그대로** 준다. `-J` 없이 grep 하면
#      로그 앞부분이 잘려 `r]: Passing new path` 같은 쓰레기가 나온다. → 항상 `-J`
#   ② `pkill -f <패턴>` 은 **자기를 실행한 ssh 셸의 명령줄까지 매칭**한다. 패턴 문자열이
#      cmdline 에 들어 있으니까. 그래서 접속이 통째로 끊긴다(exit 255).
#      → **이 스크립트는 아무것도 죽이지 않는다.** 종료는 kill-libi_pi.sh 가 한다.
#   ③ colcon 산출물 경로는 `build/<pkg>/<pkg>/` 다. `build/<pkg>/build/lib/...` 가 아니다.
#      (`--symlink-install` 이라 `build/lib` 는 없을 수 있다)
#   ④ ROS 로그 타임스탬프는 소수 **9자리**다. 2자리로 정규식을 짜면 하나도 안 잡힌다.
#   ⑤ 스로틀은 **간헐적**이다. 단일 스냅샷으로는 못 잡는다 — 아래 `--trace` 안내 참고.
set -uo pipefail

LINES="${1:-12}"
SESSION="pinky_pi"

hr() { printf '\n\033[1m── %s %s\033[0m\n' "$1" "$(printf '─%.0s' $(seq 1 $((60 - ${#1}))))"; }
# ⚠️ -J 필수 (함정 ①)
w() { tmux capture-pane -p -J -t "$SESSION:$1" -S "-$2" 2>/dev/null; }
# ROS 타임스탬프를 초.백분의일 로 줄인다 (함정 ④)
ts() { sed -E 's/\[([0-9]{10})\.([0-9]{2})[0-9]*\]/\1.\2/g'; }

hr "열 / CPU"
TEMP=$(vcgencmd measure_temp 2>/dev/null | tr -d "temp='C")
THR=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
printf '  온도 %s°C   load%s\n' "${TEMP:-?}" "$(cut -d' ' -f1-3 /proc/loadavg | sed 's/^/ /')"
printf '  throttled=%s' "${THR:-?}"
if [ -n "${THR:-}" ]; then
  v=$((THR))
  # bit0 저전압 · bit1 주파수캡 · bit2 스로틀 · bit3 소프트온도제한  (16~19 = 발생이력)
  now=""; [ $((v & 1)) -ne 0 ] && now+="저전압 "
  [ $((v & 2)) -ne 0 ] && now+="주파수캡 "
  [ $((v & 4)) -ne 0 ] && now+="스로틀 "
  [ $((v & 8)) -ne 0 ] && now+="소프트온도제한 "
  printf '  →  지금: %s\n' "${now:-없음 ✅}"
fi
top -bn1 2>/dev/null | grep '^%Cpu' | head -1 | sed 's/^/  /'
echo "  ── CPU 상위 8 ──"
ps -eo pcpu,comm --sort=-pcpu 2>/dev/null | sed -n '2,9p' | awk '{printf "    %6s%%  %s\n", $1, $2}'

hr "cmd_vel 중재 불변식"
# twist_mux 하나여야 한다 — 근거: pinky_bringup/config/twist_mux.yaml
if command -v ros2 >/dev/null 2>&1; then
  timeout 12 ros2 topic info /cmd_vel -v 2>/dev/null \
    | grep -E 'Publisher count|Subscription count|Node name' | sed 's/^/  /' \
    || echo "  (조회 실패 — ROS 미소싱이거나 스택이 안 떠 있음)"
else
  echo "  (ros2 없음 — source /opt/ros/jazzy/setup.bash 후 다시)"
fi

hr "최근 타임라인"
echo "  ── GOAL 발행 ──"
w fleet-link "$((LINES * 4))" | grep -a "send goal" | ts | tail -6 | sed 's/^/    /'
echo "  ── FSM 전이 ──"
w fsm "$((LINES * 4))" | grep -aE "PATROL|INTERACTING|IDLE|RETURNING|전이" | ts | tail -6 | sed 's/^/    /'
echo "  ── 모터 워치독 / 발행자 감시 ──"
w hw "$((LINES * 4))" | grep -aE "명령이 없어|발행자가|Failed to read|ERROR" | ts | tail -6 | sed 's/^/    /'
echo "  ── nav2 (abort·halt 가 보이면 그게 멈춤의 직접 원인이다) ──"
# ⚠️ `Cancelling` / `Stopping the robot` 을 반드시 포함할 것. 2026-07-30 에 이 둘이
#    패턴에 없어서 진짜 원인(로봇이 자기 목표를 취소)을 며칠 놓칠 뻔했다.
#    `Aborting handle` 은 **결과**고 `Client requested to cancel` 이 **원인**이다.
w nav2 "$((LINES * 8))" | grep -aE "halt|Abort|abort|Cancelling|Cancellation|Stopping the robot|missed|Received goal|Begin navigating|Timed out" \
  | ts | tail -8 | sed 's/^/    /'

hr "배포 신선도 (조용히 옛 코드로 도는 것 방지)"
# CLAUDE.md 의 "조용히 실패하는 세 가지" 중 ②. 실제 도는 건 build/<pkg>/<pkg>/ 다 (함정 ③)
# ⚠️ 디렉터리 mtime 과 비교하면 **거짓 경고**가 난다(2026-07-30 두 번 속았다).
# 파일 내용을 직접 비교한다 — 그게 "지금 도는 코드가 소스와 같은가"의 유일한 답이다.
for pair in "libi_modes/ros_ws:libi_perception:src/libi_perception/libi_perception" \
            "libi_drive_controller/ros_ws:pinky_bringup:src/pinky_pro/pinky_bringup/pinky_bringup"; do
  ws="${pair%%:*}"; rest="${pair#*:}"; pkg="${rest%%:*}"; sub="${rest#*:}"
  src="/home/roscamp-repo-2/aba_controller/$ws/$sub"
  bld="/home/roscamp-repo-2/aba_controller/$ws/build/$pkg/$pkg"
  if [ ! -d "$src" ] || [ ! -d "$bld" ]; then echo "  ?  $pkg — 경로 없음"; continue; fi
  diff_files=""
  for f in "$src"/*.py; do
    b="$bld/$(basename "$f")"
    [ -f "$b" ] || { diff_files="$diff_files $(basename "$f")(빌드에없음)"; continue; }
    cmp -s "$f" "$b" || diff_files="$diff_files $(basename "$f")"
  done
  if [ -n "$diff_files" ]; then
    echo "  ⚠️  $pkg — 빌드본이 소스와 다르다. colcon build 필요:$diff_files"
  else
    echo "  ✅ $pkg — 빌드본 == 소스"
  fi
done
# robot_agent 는 colcon 패키지가 아니다(그냥 FastAPI 앱) — 빌드 없이 프로세스 재시작이면 반영된다.
echo "  ℹ️  robot_agent 는 빌드 불필요 (재시작만)"
echo "  git: $(cd /home/roscamp-repo-2 2>/dev/null && git log --oneline -1 2>/dev/null || echo '?')"

hr "더 볼 것"
cat <<'EOF'
  스로틀은 간헐적이라 이 스냅샷으로는 놓친다. 연속 기록:
    while :; do echo "$(date +%s) $(vcgencmd measure_temp) $(vcgencmd get_throttled) \
      $(grep -o '^[0-9.]*' /proc/loadavg)"; sleep 2; done > /tmp/thr.log &
  토픽 실측 (체인 어디서 끊기는지):
    ros2 topic hz /cmd_vel /cmd_vel_nav_out --window 40
  종료: scripts/all/kill-libi_pi.sh   (이 스크립트는 아무것도 죽이지 않는다 — 함정 ②)
EOF
