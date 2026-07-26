#!/usr/bin/env bash
# 로봇(Pi)에서 딸깍 — 캘리브레이션용 카메라 스트림을 띄우고, 노트북에서 칠 명령을 알려준다.
#
#   ./calib-pi.sh picam     # CSI  (rpicam-vid 480x360 = robot_agent 런타임과 동일)
#   ./calib-pi.sh usb       # USB  (V4L2 640x480      = perception_server 와 동일)
#   ./calib-pi.sh test      # 카메라 없이 합성 보드 — 노트북 화면이 뜨는지 먼저 확인용
#
# Ctrl-C 로 종료. 노트북 쪽은 calib-laptop.sh 를 쓴다.
set -eo pipefail

SRC="${1:-}"
case "$SRC" in
  picam|usb|test) ;;
  *) echo "사용법: ./calib-pi.sh picam|usb|test" >&2; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
PORT="${CALIB_PORT:-8099}"
USB_INDEX="${USB_INDEX:-1}"

# ── 카메라 경합 확인 ─────────────────────────────────────────────
# CSI 는 한 프로세스만 잡는다. image-sender.sh(--picamera) 나 robot_agent 가 떠 있으면
# rpicam-vid 가 조용히 죽는다. 여기서 미리 짚고, 무엇을 끄면 되는지 알려준다.
check_busy() {
  local dev="$1" what="$2"
  [ -e "$dev" ] || { echo "[calib] $dev 가 없습니다 ($what)." >&2; exit 1; }
  local pids
  pids="$(fuser "$dev" 2>/dev/null | tr -s ' ' || true)"
  if [ -n "${pids// /}" ]; then
    echo "[calib] ⚠ $dev 를 이미 쓰는 프로세스가 있습니다 —$pids" >&2
    for p in $pids; do
      echo "        PID $p: $(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | cut -c1-100)" >&2
    done
    echo "        캘리브 촬영 중에는 이걸 꺼야 합니다:  kill$pids" >&2
    exit 1
  fi
}

ARGS=(--source "$SRC" --port "$PORT")
case "$SRC" in
  picam)
    check_busy /dev/video0 "CSI"
    # 런타임과 같은 플립이어야 한다 — 로봇 실기 .env 에서 읽어온다(없으면 none).
    ENVF="$REPO_ROOT/aba_controller/libi_drive_controller/robot_agent/.env"
    FLIP="$(grep -E '^CAMERA_FLIP=' "$ENVF" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' \r')"
    FLIP="${FLIP:-none}"
    echo "[calib] CAMERA_FLIP=$FLIP  (출처: ${ENVF/#$REPO_ROOT\//})"
    ARGS+=(--flip "$FLIP")
    ;;
  usb)
    check_busy "/dev/video$USB_INDEX" "USB 카메라"
    ARGS+=(--index "$USB_INDEX")
    ;;
esac

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "────────────────────────────────────────────────────────────"
echo " 노트북에서 이걸 치세요 (한 칸 실측 크기 m 를 자로 재서):"
echo
echo "   ./calib-laptop.sh $SRC $IP 0.038"
echo "                                 ^^^^^ ← 실측값으로 바꿀 것"
echo "────────────────────────────────────────────────────────────"
echo

exec python3 "$HERE/calib_stream_pi.py" "${ARGS[@]}"
