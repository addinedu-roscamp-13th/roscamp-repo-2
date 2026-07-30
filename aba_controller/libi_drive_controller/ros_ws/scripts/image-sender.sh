#!/usr/bin/env bash
# 로봇(Pi) 카메라 -> UDP 로 AI 서버에 영상만 전송. 추종 전체(추적/명령)가 아니라
# "영상이 AI 서버까지 도착하는가"만 먼저 검증할 때 쓴다 — 원본은
# aba_ai_service/follower_perception/scripts/camera_sender.py, 여기 scripts/ 는 다른
# Pi 런처들(sim.sh/pi.sh)과 같은 위치에 두려고 얇게 감싼 것.
#
#   ./image-sender.sh <AI_SERVER_IP>
#
# picamera2 는 시스템 파이썬 패키지라 반드시 시스템 python3 로 실행한다(venv 아님).
set -eo pipefail

AI_IP="${1:?사용법: ./image-sender.sh <AI_SERVER_IP>}"
PORT="${VIDEO_PORT:-6001}"
FPS="${FPS:-15}"
CAM_ARGS="${CAM_ARGS:---picamera}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts -> ros_ws -> libi_drive_controller -> aba_controller -> (레포 루트) -> aba_ai_service
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
FOLLOWER_DIR="$REPO_ROOT/aba_ai_service/follower_perception"

# 로봇 체크아웃에 aba_controller 만 있는 경우가 있다(서버 코드는 안 내려감). 그러면 여기서
# 날것의 cd 에러가 나서 원인이 안 보이므로 먼저 짚어준다.
if [ ! -d "$FOLLOWER_DIR" ]; then
  echo "[image-sender] $FOLLOWER_DIR 가 없습니다."
  echo "  camera_sender.py 는 aba_ai_service 에 있는데, 이 체크아웃엔 안 받아져 있습니다."
  echo "  로봇에서도 그 서브트리가 필요합니다 (follower_perception/pi.sh 도 원래 로봇용입니다):"
  echo "    cd $REPO_ROOT"
  echo "    git sparse-checkout list            # sparse 체크아웃인지 확인"
  echo "    git sparse-checkout add aba_ai_service"
  echo "    git pull"
  exit 1
fi

# 이 전송 경로는 **캘리브 보정을 하지 않는다** — 픽셀만 보낸다. 거리(solvePnP)를 쓰는 쪽이
# K 를 읽어야 하고, 그 K 는 **이 스크립트가 내보내는 프레임과 같은 기하**여야 한다.
# camera_sender 는 --picamera 일 때 --rotate 180 이 기본이라(카메라가 거꾸로 달려 YOLO 가
# 사람을 똑바로 보게 하려는 것) 회전 없는 캘리브 파일을 그대로 쓰면 주점이 반대쪽이 된다.
# 어느 파일을 써야 하는지 여기서 찍어 둔다 — 받는 쪽에서 헷갈릴 일이 없게.
case " $CAM_ARGS " in
  *" --rotate 0 "*) ROT=0 ;;
  *" --rotate "*)   ROT="$(printf '%s\n' "$CAM_ARGS" | sed -n 's/.*--rotate \([0-9]*\).*/\1/p')" ;;
  *" --picamera "*|*"--picamera") ROT=180 ;;    # camera_sender.py:79 기본값
  *) ROT=0 ;;
esac
CALIB_BASE="$REPO_ROOT/config/camera"

# ⚠️ [2026-07-30] 캘리브 파일명이 **해상도로 갈린다.** 예전엔 640x480 을 하드코딩했는데,
#    `--width` 를 내리면(오늘 480 으로 내렸다) K 가 프레임 기하와 안 맞는다 —
#    fx/fy/cx/cy 가 전부 픽셀 단위라 해상도에 비례하기 때문이다.
#    틀린 K 로 solvePnP 를 돌리면 **거리가 틀리고 마커 도킹이 조용히 어긋난다**
#    (에러가 안 난다. 로봇이 엉뚱한 자리에 선다). 그래서 --width 를 따라간다.
case " $CAM_ARGS " in
  *" --width "*) CAM_W="$(printf '%s\n' "$CAM_ARGS" | sed -n 's/.*--width \([0-9]*\).*/\1/p')" ;;
  *) CAM_W=640 ;;                                  # camera_sender.py --width 기본값
esac
CAM_H=$(( CAM_W * 3 / 4 ))
CALIB_NAME="picam_${CAM_W}x${CAM_H}"
if [ "$ROT" = "0" ]; then CALIB="$CALIB_BASE/${CALIB_NAME}.npz"; else CALIB="$CALIB_BASE/${CALIB_NAME}_rot${ROT}.npz"; fi
echo "[image-sender] ${CAM_W}x${CAM_H} rotate=${ROT}° → 이 스트림에 맞는 캘리브: ${CALIB#$REPO_ROOT/}"
[ -f "$CALIB" ] || echo "[image-sender] ⚠ 그 파일이 없습니다. rotate_calib.py 로 만드세요:
    python3 scripts/cam-calib/rotate_calib.py config/camera/${CALIB_NAME}.npz $ROT
  (원본 ${CALIB_NAME}.npz 자체가 없으면 그 해상도로 체스보드 촬영부터 해야 합니다 —
   다른 해상도의 K 를 그대로 쓰면 거리가 틀립니다.)"

# camera_sender 는 이제 `/libi/camera_select` 를 구독해 어느 캠을 내보낼지 정한다.
# ROS 를 source 하지 않으면 rclpy import 가 실패해 **구독 없이** 뜨고, 그러면 BT 가
# 카메라를 켜고 끄지 못한다(스크립트는 계속 돌기 때문에 조용히 어긋난다).
# 시스템 python3 로 돌아야 하는 제약(picamera2)과 충돌하지 않는다 — ROS 도 시스템 파이썬이다.
if [ -f /opt/ros/jazzy/setup.bash ]; then
  # shellcheck disable=SC1091
  source /opt/ros/jazzy/setup.bash
else
  echo "[image-sender] ⚠ /opt/ros/jazzy 가 없습니다 — camera_select 구독 없이 돕니다."
fi

cd "$FOLLOWER_DIR"
exec python3 scripts/camera_sender.py --host "$AI_IP" --port "$PORT" --fps "$FPS" $CAM_ARGS
