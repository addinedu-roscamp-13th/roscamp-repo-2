#!/usr/bin/env bash
# 노트북/서버에서 실행 — 관제(aba_fms_service) 백엔드·프론트엔드는 빼고,
# 로봇 운영에 필요한 ROS 쪽만 띄운다.
#   - 도메인 브릿지  : 로봇 도메인 ↔ 86 (DB rc_robots 기준, 로봇마다 하나)
#   - fleet_node     : 배차·교통 (도메인 86)
#   - 상태 어댑터    : 로봇 위치 → fleet_node (DB 로봇 전부)
#
# 관제 백엔드(:9001)/프론트(:9002)는 aba_fms_service/backend/start.sh · frontend
# 쪽에서 따로 띄운다(이 스크립트는 관여하지 않는다).
#
#   ./fms_service.sh
#
# tmux 세션 'libi_fms' — 창: bridge / fleet-node / adapters
# 분리: Ctrl+b d  ·  종료: ./kill.sh
set -eo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FMS="$REPO_ROOT/aba_fms_service"
FLEET_WS="$FMS/fleet_ws"
# arte2 맵의 waypoint.yaml 에서 생성한 navgraph(정점 41). 정점 이름이 주문의
# pickup/dropoff 와 같아야 fleet_node 가 목적지를 찾는다.
# 재생성: .venv/bin/python scripts/gen_arte2_navgraph.py
# (구 new_map.navgraph.yaml 은 정점 8개짜리 포팅 placeholder 였다 — 실제 지점이 없다)
NAVGRAPH="$FLEET_WS/maps/library/arte2.navgraph.yaml"
# 도착 판정 반경(m) — arte2 는 1.26m × 2.16m 축소 맵이라 기본값 0.35 를 쓰면 안 된다.
# 0.35 는 맵 가로폭의 28% 라, 로봇이 가만히 있어도 다음 정점이 반경 안에 들어와
# fleet_node 가 0.15초마다 "도착" 처리하며 경로를 훑고 나간다. 그러면 path_request_driver
# 가 매번 nav2 목표를 갈아치워 status=6(ABORTED) → **출발하자마자 멈춘다.**
#   하한 nav2 xy_goal_tolerance = 0.05 / 상한 최소 레인 길이 = 0.062 (v4 유아 ↔ v13 복도-5)
ARRIVE_RADIUS="${ARRIVE_RADIUS:-0.05}"
# 경유 노드 선행 통과 반경(m). 이 반경에 들면 **도착 전에** 다음 노드를 예약·발행해서
# 로봇이 감속·정지하기 전에 새 목표를 받는다(fleet_node.cpp:104 주석).
#
# ⚠️ fleet_node 기본값 0.10 으로는 부족하다. nav2 RPP 가 목표 앞에서 감속을 시작하는데
#    (nav2_params.yaml: approach_velocity_scaling_dist), 허가가 그보다 늦으면 이미 감속에
#    들어간 뒤에 새 목표가 와서 노드마다 한 번 주춤한다.
#
#    현재 감속 시작은 **0.16** 이다(사용자가 "조금씩 줄어들게" 요청해 0.12 → 0.16).
#    즉 허가 0.14 < 감속 0.16 이라 **순서가 뒤집혀 있다** — 마지막 2cm 는 감속에 들어간
#    뒤 새 목표를 받는다. 이건 알고 고른 값이다. 정량 검산(codex):
#      0.14m 지점 속도 = 0.07 × 0.14/0.16 = 0.06125 m/s  (약 12.5% 감속)
#      그 2cm 통과 시간 0.305s vs 무감속 0.286s → **노드당 약 19ms 손해**
#    min_approach_linear_velocity(0.02) 는 4.57cm 아래에서만 걸리므로 관여하지 않는다.
#
#    주춤이 거슬리면 고칠 곳은 이 값이 아니라 approach_velocity_scaling_dist 를 0.10 으로
#    내리는 것이다(허가 0.14 밖으로 빠져 경유 노드에서 감속이 아예 안 걸린다).
#
#    상한: fleet_node 가 반경을 레인 길이의 절반으로 깎는다(fleet_node.cpp:679).
#    실측 레인(codex 검산) 9→6 0.3005 / 6→7 0.2737 / 7→13 0.2985 / 13→15 0.3418 /
#    15→14 0.3832 → 최단 절반이 0.1369 다. **0.14 는 전 구간에서 그대로 적용되지만
#    0.16 으로 올리면 6→7 에서 0.1369 로 깎인다.** 그래서 0.14 가 상한에 가깝다.
PREFETCH_RADIUS="${PREFETCH_RADIUS:-0.14}"
SESSION="libi_fms"

# ── 순회 루트 이름 → navgraph 정점 인덱스 해석 ──────────────────────────────
# fleet_node 의 patrol_route/security_patrol_route 는 정수 인덱스를 받는다. 그런데 인덱스는
# waypoint.yaml 삽입순서라 비순차(순회경로-6=17, -8=18, -7=19, -5=20)여서 하드코딩하면
# navgraph 재생성 때 조용히 밀린다. 그래서 navgraph 메타의 정점 이름을 읽어 **매 기동 때**
# 해석한다. 이름 없음/중복/2개 미만이면 fleet_node 를 안 띄우고 즉시 종료(무-grant 방지).
# ⚠️ waypoint.yaml 을 고쳤으면 이 스크립트 전에 navgraph 를 재생성해야 한다:
#     <venv>/python scripts/gen_arte2_navgraph.py
PATROL_NAMES="순회경로-1 예술서가 문학서가 순회경로-6 순회경로-7 순회경로-8 순회경로-5 순회경로-4 순회경로-3 순회경로-2"
SECURITY_NAMES="$PATROL_NAMES"   # 일단 주간과 동일(야간 전용 경로 생기면 여기만 교체)

RESOLVE_PY=""
for c in "$FMS/backend/.venv/bin/python" "$REPO_ROOT/aba_service/backend/.venv/bin/python" python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import yaml" 2>/dev/null; then RESOLVE_PY="$c"; break; fi
done
[ -z "$RESOLVE_PY" ] && die "python(pyyaml) 을 찾지 못해 순회 루트를 해석할 수 없습니다."

resolve_route() {   # <navgraph> <공백구분 이름들> → 인덱스열(stdout). 실패 시 non-zero + stderr.
  "$RESOLVE_PY" - "$1" "$2" <<'PY'
import sys, yaml
navgraph, names = sys.argv[1], sys.argv[2].split()
d = yaml.safe_load(open(navgraph))
verts = d["levels"]["L1"]["vertices"]
name_to_idx = {}
for i, v in enumerate(verts):
    meta = v[2] if len(v) > 2 and isinstance(v[2], dict) else {}
    nm = meta.get("name")
    if nm is None:
        continue
    if nm in name_to_idx:
        print(f"[resolve] navgraph 중복 정점 이름: {nm}", file=sys.stderr); sys.exit(2)
    name_to_idx[nm] = i
out = []
for nm in names:
    if nm not in name_to_idx:
        print(f"[resolve] navgraph 에 정점 없음: {nm} (navgraph 재생성했는지 확인)", file=sys.stderr); sys.exit(3)
    out.append(str(name_to_idx[nm]))
if len(out) < 2:
    print("[resolve] 해석된 순회 노드 2개 미만", file=sys.stderr); sys.exit(4)
sys.stderr.write("[resolve] " + "  ".join(f"{n}={i}" for n, i in zip(names, out)) + "\n")
print(" ".join(out))
PY
}

PATROL_ROUTE="$(resolve_route "$NAVGRAPH" "$PATROL_NAMES")" || die "patrol_route 해석 실패 — 위 stderr 참고 (navgraph 재생성 필요할 수 있음)"
SECURITY_ROUTE="$(resolve_route "$NAVGRAPH" "$SECURITY_NAMES")" || die "security_patrol_route 해석 실패"
echo "[fms] patrol_route         = $PATROL_ROUTE"
echo "[fms] security_patrol_route = $SECURITY_ROUTE"

need_cmd tmux "sudo apt install -y tmux"
tmux has-session -t "$SESSION" 2>/dev/null && \
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 ./kill.sh 로 정리하세요."

# ── .env → rc_robots 등록을 **창을 띄우기 전에** 끝낸다 ────────────────────
#
# 등록 자체는 브릿지 창의 gen_domain_bridges.py 도 한다. 그런데 아래 adapters 창
# (robot-link --all)은 DB 목록을 **한 번만 읽고 재시도하지 않는다.** 두 창이 거의 동시에
# 뜨므로, .env 에 새로 적은 로봇이 첫 기동에서 어댑터 0대로 굳을 수 있다 — 브릿지는
# 떠도 fleet_node 는 그 로봇을 못 본다(조용한 실패). 순서를 고정해서 그 경쟁을 없앤다.
# (codex 적대적 검토 2026-07-29)
#
# ⚠️ 실패하면 **여기서 멈춘다** — 아래 창이 뜬 뒤에는 되돌릴 방법이 없다.
if [ -x "$FMS/backend/.venv/bin/python" ]; then
  # ⚠️ 실패를 **삼키지 않는다.** 여기서 넘어가면 브릿지 창이 나중에 등록에 성공해도
  #    어댑터는 이미 DB 를 한 번 읽고 끝난 뒤라, 그 로봇이 0대로 굳는다(경쟁이 그대로 재발).
  #    DB 일시 장애 같은 경우가 정확히 그 모양이다. (codex 2차 지적)
  if ! ( cd "$FMS" && ./backend/.venv/bin/python scripts/gen_domain_bridges.py --sync-only ) \
       2>&1 | sed 's/^/[robots] /'; then
    die ".env → rc_robots 동기화 실패. DB(MariaDB)와 ROBOT_DATABASE_URL 을 확인하세요.
  이 단계를 건너뛰면 새 로봇이 상태 어댑터에 안 잡혀 fleet_node 가 못 봅니다."
  fi
fi

cd "$REPO_ROOT"
tmux new-session -d -s "$SESSION" -n bridge \
  bash -c "cd '$FMS' && echo '[bridge] 로봇 도메인 <-> 86 (DB rc_robots 기준)...' && ./scripts/ros-domain-bridge.sh; exec bash"

# fleet_node(배차·교통) — 백엔드와 같은 도메인 86 에서 돈다(fleet_link 가 같은 도메인 전제).
# fleet_ws 안 빌드면 colcon build. RMW/CycloneDDS 는 ~/.bashrc 설정을 따른다(bridge 와 동일).
if [ -f "$FLEET_WS/install/setup.bash" ] || ensure_built "$FLEET_WS"; then
  tmux new-window -t "$SESSION" -n fleet-node \
    bash -c "source /opt/ros/jazzy/setup.bash && source '$FLEET_WS/install/setup.bash' && export ROS_DOMAIN_ID=86 && echo '[fleet-node] 배차·교통 (domain 86)...' && ros2 run libi_fleet fleet_node --ros-args -p navgraph_file:='$NAVGRAPH' -p arrive_radius:=$ARRIVE_RADIUS -p prefetch_radius:=$PREFETCH_RADIUS -p patrol_route:='$PATROL_ROUTE' -p security_patrol_route:='$SECURITY_ROUTE'; exec bash"
fi

# 로봇 상태 어댑터 — 도메인 86 에서 돈다(fleet_node·브릿지와 같은 자리라 여기 둔다).
#
# fleet_node 는 `/robot_state`(rmf_fleet_msgs/RobotState)로 로봇을 인식하는데, 로봇도 sim 도
# 그 타입을 발행하지 않는다(amcl_pose·battery 만 낸다). 이 어댑터가 브릿지로 넘어온
# `/<key>/amcl_pose` 를 읽어 `/robot_state` 로 재발행한다 — 없으면 **로봇 0대**로 보인다.
#
# DB(rc_robots)에 등록된 로봇마다 하나씩 띄운다. 브릿지와 같은 목록을 쓰므로 따로 맞출 게 없다.
# ⚠️ 반대 방향(fleet_node 경로 → nav2)은 **로봇 쪽**에서 돈다:
#      실물 → drive-pi/pi.sh 가 함께 띄운다
#      sim  → sim.sh 가 함께 띄운다
if [ -f "$FLEET_WS/install/setup.bash" ]; then
  tmux new-window -t "$SESSION" -n adapters \
    bash -c "cd '$REPO_ROOT' && ./scripts/laptop/robot-link.sh --all --foreground; exec bash"
fi

tmux select-window -t "$SESSION:bridge"
tmux_attach "$SESSION"
