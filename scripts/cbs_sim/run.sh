#!/usr/bin/env bash
# CBS 교통관제 시나리오 시험 + 녹화.
#
# 진짜 fleet_node 와 진짜 CbsTraffic 플러그인을 띄우고, nav2/로봇 자리에만 운동학 모델을
# 놓는다(sim_robots.py). Gazebo 를 로봇 수만큼 띄우지 않는 이유는 그 파일 주석 참고.
#
#   ./run.sh                      기본 시나리오(교차 2대 + 지연 주입)
#   ./run.sh --no-delay           지연 없이
#   ./run.sh --traffic reservation  비교군(반응형)으로 같은 시나리오
#   ./run.sh --seconds 180        더 길게
#
# 결과: $TMPDIR/libi-cbs-sim/{record.json, replay.html} — replay.html 을 브라우저로 연다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FLEET_WS="$REPO_ROOT/aba_fms_service/fleet_ws"
NAVGRAPH="$FLEET_WS/maps/library/arte2.navgraph.yaml"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${TMPDIR:-/tmp}/libi-cbs-sim"
DOMAIN="${LIBI_CBS_SIM_DOMAIN:-91}"     # 실물(86)·sim(90)과 안 겹치게

TRAFFIC="libi_fleet::CbsTraffic"
SECONDS_RUN=110
DELAY=1
OPEN=1
PATROL_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --traffic)
      case "${2:?}" in
        cbs) TRAFFIC="libi_fleet::CbsTraffic" ;;
        reservation) TRAFFIC="libi_fleet::ReservationDeadlock" ;;
        grantall) TRAFFIC="libi_fleet::GrantAllTraffic" ;;
        *) TRAFFIC="$2" ;;
      esac
      shift 2 ;;
    --seconds) SECONDS_RUN="${2:?}"; shift 2 ;;
    --no-delay) DELAY=0; shift ;;
    --patrol) PATROL_ONLY=1; DELAY=0; shift ;;
    --no-open) OPEN=0; shift ;;
    *) echo "[cbs-sim] 알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

need_cmd python3 "sudo apt install -y python3"
ensure_built "$FLEET_WS"
mkdir -p "$OUT"

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "$FLEET_WS/install/setup.bash"
export ROS_DOMAIN_ID="$DOMAIN"

# ── 시나리오 ────────────────────────────────────────────────────────────────
# arte2 navgraph 에서 서로를 마주 보고 지나가야 하는 두 대.
#   Pinky-1: v0(주차장) → v15
#   Pinky-2: v15        → v0
# 둘 다 좁은 중앙 통로(v9, v1)를 지난다 — 시간으로 분리하지 않으면 마주친다.
R1_NAME="Pinky-1"; R1_START=0;  R1_GOAL=15
R2_NAME="Pinky-2"; R2_START=15; R2_GOAL=0

coord() {   # 정점 인덱스 → "x:y"
  python3 - "$NAVGRAPH" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
v = d["levels"]["L1"]["vertices"][int(sys.argv[2])]
print(f"{v[0]}:{v[1]}")
PY
}
# 순회 루프 — fms_service.sh 가 쓰는 실제 정점 순서(순회경로 canonical lap).
# 배차 시나리오에서는 순회가 끼어들면 관측이 지저분해지므로 최소 2정점만 준다.
if [ "$PATROL_ONLY" = "1" ]; then
  PATROL_ROUTE="9 6 7 13 15 14 16 12 11 10"
else
  PATROL_ROUTE="9 6"
fi

R1_XY="$(coord $R1_START)"
R2_XY="$(coord $R2_START)"

echo "[cbs-sim] 도메인 $DOMAIN · 교통 $TRAFFIC"
echo "[cbs-sim] $R1_NAME v$R1_START→v$R1_GOAL,  $R2_NAME v$R2_START→v$R2_GOAL"

# 이 시나리오가 띄우는 노드에는 **고유 이름**을 준다.
# 이게 없으면 정리할 때 로봇 관제(도메인 86)의 진짜 fleet_node 까지 잡을 위험이 있고,
# 반대로 이름이 겹치지 않으면 이전 실행의 좀비를 골라 죽일 수가 없다.
#
# ⚠️ 실제로 겪은 문제: timeout 으로 스크립트가 끊기면 trap 이 안 돌아 fleet_node 가 남는다.
#    그 상태로 다시 돌리면 **같은 도메인에 관제가 둘** 이 되어 로봇이 서로 다른 명령을 받고,
#    로그에는 같은 시각 두 줄이 다른 내용으로 찍힌다(실측: 좀비 5개가 쌓여 있었다).
#    그래서 기동 전에 **이전 좀비를 먼저 정리**한다.
NODE_NAME="cbs_sim_fleet"
SIM_TAG="cbs_sim_robots"

kill_stale() {
  local n=0
  for p in $(pgrep -f "__node:=$NODE_NAME" 2>/dev/null) $(pgrep -f "$SIM_TAG" 2>/dev/null); do
    [ "$p" = "$$" ] && continue
    kill "$p" 2>/dev/null && n=$((n+1))
  done
  [ "$n" -gt 0 ] && { echo "[cbs-sim] 이전 실행 잔여 프로세스 $n개 정리"; sleep 1; }
  return 0
}

cleanup() {
  [ -n "${FLEET_PID:-}" ] && kill "$FLEET_PID" 2>/dev/null || true
  [ -n "${SIM_PID:-}" ] && kill "$SIM_PID" 2>/dev/null || true
  sleep 0.5
  kill_stale >/dev/null 2>&1 || true    # 강제 종료로 trap 을 못 탄 경우까지
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
kill_stale

# ── ① fleet_node (진짜 관제) ────────────────────────────────────────────────
# arrive_radius 는 fms_service.sh 와 같은 0.05. 순회는 이 시험에서 방해가 되므로 끈다
# (patrol_route 를 최소 2정점만 줘서 자동 순회가 돌지 않게 한다 — 로봇 모드가 IDLE 이면
#  순회는 시작되지 않는다).
ros2 run libi_fleet fleet_node --ros-args \
  -r __node:="$NODE_NAME" \
  -p navgraph_file:="$NAVGRAPH" \
  -p arrive_radius:=0.05 \
  -p traffic_plugin:="$TRAFFIC" \
  -p patrol_route:="$PATROL_ROUTE" \
  -p security_patrol_route:="$PATROL_ROUTE" \
  > "$OUT/fleet_node.log" 2>&1 &
FLEET_PID=$!
sleep 4
kill -0 "$FLEET_PID" 2>/dev/null || { echo "[cbs-sim] ❌ fleet_node 기동 실패:"; tail -20 "$OUT/fleet_node.log"; exit 1; }
grep -m1 "plugins:" "$OUT/fleet_node.log" || true

# ── ② 가짜 로봇 ─────────────────────────────────────────────────────────────
DELAY_ARGS=()
if [ "$DELAY" = "1" ]; then
  # Pinky-1 을 25초 시점부터 25초간 세운다 — 계획 도착 시각을 확실히 넘긴다.
  DELAY_ARGS=(--delay "$R1_NAME:25:25")
fi
python3 "$HERE/sim_robots.py" \
  --robot "$R1_NAME:$R1_XY" --robot "$R2_NAME:$R2_XY" \
  "${DELAY_ARGS[@]}" \
  --record "$OUT/record.json" --seconds "$SECONDS_RUN" \
  > "$OUT/sim_robots.log" 2>&1 &
SIM_PID=$!
sleep 3

# ── ③ 배차 ──────────────────────────────────────────────────────────────────
submit() {   # <robot> <goal>
  ros2 service call /fms/submit_task libi_fleet_msgs/srv/SubmitTask \
    "{task_type: 'delivery', pickup: '', dropoff: '$2', requester: 'T-$1', robot: '$1', priority: 0, arm_actions: 0}" \
    2>&1 | grep -E "accepted|reason" | head -2
}
if [ "$PATROL_ONLY" = "1" ]; then
  echo "[cbs-sim] 순회 모드 — 배차 없음. 루프: $PATROL_ROUTE"
else
  echo "[cbs-sim] 배차…"
  submit "$R1_NAME" "$R1_GOAL"
  submit "$R2_NAME" "$R2_GOAL"
fi

# ── ④ 대기 ──────────────────────────────────────────────────────────────────
echo "[cbs-sim] ${SECONDS_RUN}초 주행 관측 중…"
wait "$SIM_PID" 2>/dev/null || true
kill "$FLEET_PID" 2>/dev/null || true
sleep 1

# ── ⑤ 리플레이 생성 ─────────────────────────────────────────────────────────
python3 "$HERE/make_replay.py" \
  --navgraph "$NAVGRAPH" --record "$OUT/record.json" \
  --template "$HERE/replay.template.html" --out "$OUT/replay.html" \
  --title "$TRAFFIC"

echo
echo "[cbs-sim] 로그:   $OUT/fleet_node.log"
echo "[cbs-sim] 기록:   $OUT/record.json"
echo "[cbs-sim] 리플레이: $OUT/replay.html"
grep -E "재계획|시간표|계획 도착" "$OUT/fleet_node.log" | head -20 || true

if [ "$OPEN" = "1" ] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$OUT/replay.html" >/dev/null 2>&1 &
fi
