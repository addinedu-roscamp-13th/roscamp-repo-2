#!/usr/bin/env bash
# CBS 시간표를 만들어 애니메이션으로 연다. ROS 노드도 로봇도 필요 없다 —
# "이 계획이 정말 무충돌인가" 만 보는 도구다.
#
#   ./run.sh --robot 0:15 --robot 15:0                 두 대 교차
#   ./run.sh --robot 0:15 --robot 15:0 --clearance 2   여유를 벌려서
#   ./run.sh --robot 0:15 --robot 15:0 --robot 6:3     세 대
#   ./run.sh --robot 0:15 --no-open                    브라우저 안 열고 파일만
#
# 옵션은 cbs_viz 에 그대로 넘어간다: --clearance <틱> --speed <m/s> --tick <초> --navgraph <yaml>
# 정점 인덱스가 뭔지 모르겠으면 뷰어를 한 번 열어 보면 번호가 찍혀 있다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

FLEET_WS="$REPO_ROOT/aba_fms_service/fleet_ws"
NAVGRAPH_DEFAULT="$FLEET_WS/maps/library/arte2.navgraph.yaml"
OUT_DIR="${TMPDIR:-/tmp}/libi-cbs-viewer"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OPEN=1
ARGS=()
HAS_ROBOT=0
HAS_NAVGRAPH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-open) OPEN=0; shift ;;
    --robot) HAS_ROBOT=1; ARGS+=("$1" "${2:?--robot 뒤에 start:goal 이 필요합니다}"); shift 2 ;;
    --navgraph) HAS_NAVGRAPH=1; ARGS+=("$1" "${2:?--navgraph 뒤에 경로가 필요합니다}"); shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [ "$HAS_ROBOT" = "0" ]; then
  echo "[cbs] ❌ 로봇을 하나 이상 지정해야 합니다." >&2
  echo >&2
  echo "  사용법:  ./run.sh --robot <출발정점>:<목표정점> [--robot ...] [--clearance <틱>]" >&2
  echo "  예:      ./run.sh --robot 0:15 --robot 15:0" >&2
  exit 1
fi
[ "$HAS_NAVGRAPH" = "1" ] || ARGS+=(--navgraph "$NAVGRAPH_DEFAULT")

need_cmd python3 "sudo apt install -y python3"
ensure_built "$FLEET_WS"

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
# shellcheck disable=SC1091
source "$FLEET_WS/install/setup.bash"

mkdir -p "$OUT_DIR"
PLAN="$OUT_DIR/plan.json"
HTML="$OUT_DIR/viewer.html"

echo "[cbs] 계획 계산 중…"
if ! ros2 run libi_fleet cbs_viz "${ARGS[@]}" > "$PLAN"; then
  echo "[cbs] ❌ 계획 실패 — 위 메시지를 확인하세요 (여유를 줄이거나 목표를 바꿔 보세요)." >&2
  exit 1
fi

# 템플릿의 자리표시자에 계획 JSON 을 끼워 넣는다. fetch 를 쓰면 file:// 에서 CORS 로
# 막히므로, 열기만 하면 되도록 **한 파일로 합친다**.
python3 - "$HERE/viewer.template.html" "$PLAN" "$HTML" <<'PY'
import sys, pathlib
tpl, plan, out = (pathlib.Path(p) for p in sys.argv[1:4])
html = tpl.read_text(encoding="utf-8")
marker = "/*PLAN_JSON*/"
if marker not in html:
    sys.exit(f"템플릿에 {marker} 자리표시자가 없습니다: {tpl}")
out.write_text(html.replace(marker, plan.read_text(encoding="utf-8"), 1), encoding="utf-8")
print(f"[cbs] 뷰어 생성: {out}")
PY

if [ "$OPEN" = "1" ]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$HTML" >/dev/null 2>&1 &
  else
    echo "[cbs] xdg-open 이 없습니다 — 브라우저로 직접 여세요: $HTML"
  fi
fi
echo "[cbs] ✅ $HTML"
