#!/usr/bin/env bash
# 노트북에서 딸깍 — 로봇 스트림을 보면서 체커보드를 수집하고 npz 까지 저장한다.
#
#   ./calib-laptop.sh picam 172.30.1.83 0.038
#   ./calib-laptop.sh usb   172.30.1.83 0.038
#   ./calib-laptop.sh test  172.30.1.83 0.038      # 합성 보드로 리허설(저장 경로도 /tmp)
#                     └소스  └로봇 IP    └한 칸 실측 크기(m)
#
# 키:  SPACE 수집 · a 자동 · u 취소 · c 계산+저장 · q 종료
#
# 저장 위치(자동):
#   picam → aba_controller/.../robot_agent/config/camera_calib.npz       (aruco_dock 이 읽는 그 경로)
#   usb   → aba_controller/.../robot_agent/config/camera_calib_usb.npz   (읽으려면 코드 1줄 필요, README 참고)
set -eo pipefail

SRC="${1:-}"; HOST="${2:-}"; SQUARE="${3:-}"; PATTERN="${4:-auto}"
case "$SRC" in picam|usb|test) ;; *) SRC="" ;; esac
if [ -z "$SRC" ] || [ -z "$HOST" ] || [ -z "$SQUARE" ]; then
  cat >&2 <<'EOF'
사용법: ./calib-laptop.sh picam|usb|test <로봇IP> <한칸실측크기_m> [내부코너 가로x세로]

  예) ./calib-laptop.sh picam 172.30.1.83 0.0382         # 패턴 자동 감지
      ./calib-laptop.sh picam 172.30.1.83 0.025  5x5     # 직접 지정

  ★ 한 칸 크기는 인쇄물을 자로 재서 넣으세요. 프린터 배율 때문에 25mm 로 설정해도
    25mm 가 아닙니다. 여기가 틀리면 재투영오차는 멀쩡한데 거리만 그 비율로 전부 틀어집니다.
EOF
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
CFG="$REPO_ROOT/aba_controller/libi_drive_controller/robot_agent/config"

case "$SRC" in
  picam) OUT="${OUT:-$CFG/camera_calib.npz}" ;;
  usb)   OUT="${OUT:-$CFG/camera_calib_usb.npz}" ;;
  test)  OUT="${OUT:-/tmp/camera_calib_test.npz}" ;;
esac

# 레포 .venv 에 cv2 가 있으면 그걸 쓴다(노트북 표준). 없으면 시스템 python3.
PY="python3"
if [ -x "$REPO_ROOT/.venv/bin/python" ] && "$REPO_ROOT/.venv/bin/python" -c "import cv2" 2>/dev/null; then
  PY="$REPO_ROOT/.venv/bin/python"
fi
"$PY" -c "import cv2, numpy" 2>/dev/null || {
  echo "[calib] cv2/numpy 가 없습니다: $PY -m pip install opencv-python numpy" >&2; exit 1; }

echo "[calib] $SRC @ $HOST  ·  한 칸 ${SQUARE}m  ·  패턴 ${PATTERN}  ·  저장 → $OUT"
[ "$SRC" = "picam" ] && echo "[calib] ⚠ 기존 camera_calib.npz 를 덮어씁니다(계산 성공 시에만)."

exec "$PY" "$HERE/calib_client.py" \
  --host "$HOST" --port "${CALIB_PORT:-8099}" \
  --pattern "$PATTERN" \
  --square-m "$SQUARE" --num "${NUM:-40}" --out "$OUT"
