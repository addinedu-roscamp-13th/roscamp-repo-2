#!/usr/bin/env bash
# 노트북에서 딸깍 — 로봇 스트림을 보면서 ChArUco 보드를 수집하고 npz 까지 저장한다.
#
#   ./calib-laptop.sh picam 172.30.1.83 0.035
#   ./calib-laptop.sh usb   172.30.1.83 0.035
#   ./calib-laptop.sh test  172.30.1.83 0.035      # 합성 보드로 리허설(저장 경로도 /tmp)
#                     └소스  └로봇 IP    └한 칸 실측 크기(m)
#
# 보드는 make_board.py 로 뽑은 것 기준(7x5칸 35mm · 마커=칸의 0.75 · DICT_4X4_50).
# 다르게 뽑았으면 환경변수로 맞춘다 — 보드와 검출기의 기하가 다르면 값이 조용히 틀린다:
#   SQUARES=11x7 DICT=DICT_5X5_100 ./calib-laptop.sh picam <IP> 0.025
#
# 예전 체커보드 인쇄물을 쓸 때만:  BOARD=chess ./calib-laptop.sh picam <IP> 0.038 [8x5]
#
# 키:  SPACE 수집 · a 자동 · u 취소 · c 계산+저장 · q 종료
#
# 저장 위치(자동):  config/camera/<카메라>_<실제해상도>.npz
#   picam → config/camera/picam_640x480.npz      usb → config/camera/usb_640x480.npz
#   해상도는 스트림에서 받아 채우므로 이름과 내용이 어긋날 수 없다.
#   다른 곳에 두려면 CALIB_DIR=... 또는 OUT=<파일경로>
set -eo pipefail

SRC="${1:-}"; HOST="${2:-}"; SQUARE="${3:-}"; PATTERN="${4:-auto}"
case "$SRC" in picam|usb|test) ;; *) SRC="" ;; esac
if [ -z "$SRC" ] || [ -z "$HOST" ] || [ -z "$SQUARE" ]; then
  cat >&2 <<'EOF'
사용법: ./calib-laptop.sh picam|usb|test <로봇IP> <한칸실측크기_m> [체커보드일 때만: 내부코너 가로x세로]

  예) ./calib-laptop.sh picam 172.30.1.83 0.0351                 # ChArUco(기본)
      SQUARES=11x7 ./calib-laptop.sh picam 172.30.1.83 0.025     # 보드를 다르게 뽑았을 때
      BOARD=chess  ./calib-laptop.sh picam 172.30.1.83 0.035 8x5 # 예전 체커보드

  ★ 한 칸 크기는 인쇄물을 자로 재서 넣으세요. 프린터 배율 때문에 25mm 로 설정해도
    25mm 가 아닙니다. 여기가 틀리면 재투영오차는 멀쩡한데 거리만 그 비율로 전부 틀어집니다.
EOF
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
# 캘리브 결과는 서비스 밖 공용 위치에 둔다 — robot_agent 뿐 아니라 새 도킹 코드·AI 서비스도
# 읽는다. 파일명의 {res} 는 calib_client 가 **실제 스트림 해상도**로 채운다(K 는 해상도 전용).
CFG="${CALIB_DIR:-$REPO_ROOT/config/camera}"

case "$SRC" in
  picam) OUT="${OUT:-$CFG/picam_{res}.npz}" ;;
  usb)   OUT="${OUT:-$CFG/usb_{res}.npz}" ;;
  test)  OUT="${OUT:-/tmp/camera_calib_test_{res}.npz}" ;;
esac

# 레포 .venv 에 cv2 가 있으면 그걸 쓴다(노트북 표준). 없으면 시스템 python3.
PY="python3"
if [ -x "$REPO_ROOT/.venv/bin/python" ] && "$REPO_ROOT/.venv/bin/python" -c "import cv2" 2>/dev/null; then
  PY="$REPO_ROOT/.venv/bin/python"
fi
"$PY" -c "import cv2, numpy" 2>/dev/null || {
  echo "[calib] cv2/numpy 가 없습니다: $PY -m pip install opencv-python numpy" >&2; exit 1; }

if [ "${BOARD:-charuco}" = "charuco" ]; then
  echo "[calib] $SRC @ $HOST  ·  ChArUco ${SQUARES:-7x5}칸 ${DICT:-DICT_4X4_50}  ·  한 칸 ${SQUARE}m  ·  저장 → $OUT"
else
  echo "[calib] $SRC @ $HOST  ·  체커보드 패턴 ${PATTERN}  ·  한 칸 ${SQUARE}m  ·  저장 → $OUT"
fi
echo "[calib] 같은 카메라·해상도 결과가 있으면 덮어씁니다(계산 성공 시에만)."

ARGS=(--host "$HOST" --port "${CALIB_PORT:-8099}"
      --square-m "$SQUARE" --num "${NUM:-40}" --out "$OUT"
      --board "${BOARD:-charuco}")
if [ "${BOARD:-charuco}" = "charuco" ]; then
  ARGS+=(--squares "${SQUARES:-7x5}" --dict "${DICT:-DICT_4X4_50}")
  [ -n "${MARKER_M:-}" ] && ARGS+=(--marker-m "$MARKER_M")
else
  ARGS+=(--pattern "$PATTERN")
fi

exec "$PY" "$HERE/calib_client.py" "${ARGS[@]}"
