#!/usr/bin/env bash
# AI 서버 기동 — 관리자 추종의 인지(perception) 서버를 띄운다.
#
#   ./ai-server.sh                    로봇(Pi)이 보내는 영상을 UDP 로 받는다
#   ./ai-server.sh --test-pattern     로봇 없이 테스트 패턴으로
#   ./ai-server.sh --camera 0         이 머신의 웹캠으로
#   ./ai-server.sh --drive-host <PI_IP>   추종 명령까지 로봇에 보낼 때
#
# 실행되는 것은 follower_perception/scripts/perception_server.py 다:
#   Pi 카메라 ──UDP:6001──▶ perception_server ──TCP:5007──▶ libi_gui 추종 화면
#                                             └─UDP:6002──▶ 로봇 cmd_bridge (--drive-host 줬을 때)
#
# ⚠️ aba_ai_service/main.py 는 이게 아니다. 그건 UDP:9000→TCP:9010(FMS)/6000(로봇) 릴레이
#    stub 으로, 추종 화면과 무관한 별개 경로다 (아직 더미 추론). 필요하면 relay-stub.sh 로 띄운다.
#
# 필요: torch + ultralytics (follower_perception/requirements.txt). 없으면 여기서 멈춘다.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
FOLLOWER_DIR="$AI_SERVICE_DIR/follower_perception"
REPO_ROOT="$(cd "$AI_SERVICE_DIR/.." && pwd)"

VENV_PY="$REPO_ROOT/.venv/bin/python"
PY="${PYTHON:-$([ -x "$VENV_PY" ] && echo "$VENV_PY" || echo python3)}"

VIDEO_PORT="${VIDEO_PORT:-6001}"     # Pi 의 image-sender.sh 가 보내는 포트와 같아야 한다
VIEWER_PORT="${VIEWER_PORT:-5007}"   # libi_gui 의 PERCEPTION_URL 이 붙는 포트

# --drive-host 가 없으면 perception_server 는 cmd_sink 를 아예 만들지 않는다 — 영상은
# 나오는데 로봇만 안 움직이고, 화면엔 아무 단서가 없어서 원인을 찾기 어렵다. 미리 짚어준다.
case " $* " in
  *" --drive-host "*)
    ;;
  *)
    echo "[ai-server] ⚠ 주행 꺼짐 — 영상만 나가고 로봇은 움직이지 않습니다."
    echo "            움직이게 하려면:  ./ai-server.sh --drive-host <로봇IP>"
    echo "            (로봇에서 cmd_bridge.py 도 떠 있어야 하고, 화면에서 「등록」을 눌러야 합니다)"
    ;;
esac

if ! "$PY" -c "import ultralytics" 2>/dev/null; then
  echo "[ai-server] ultralytics/torch 가 없습니다. 설치 후 다시 실행하세요:"
  echo "  $PY -m pip install -r $FOLLOWER_DIR/requirements.txt"
  exit 1
fi

# 영상 소스를 안 주면 로봇에서 UDP 로 받는다(실제 운용 형태).
ARGS=("$@")
case " ${ARGS[*]} " in
  *" --udp "*|*" --camera "*|*" --test-pattern "*) ;;
  *) ARGS=(--udp "${ARGS[@]}") ;;
esac

cd "$FOLLOWER_DIR"
echo "[ai-server] video-in UDP:$VIDEO_PORT   viewer TCP:$VIEWER_PORT   (libi_gui 는 PERCEPTION_URL=<이 머신 IP>:$VIEWER_PORT)"
exec "$PY" scripts/perception_server.py --udp-port "$VIDEO_PORT" --port "$VIEWER_PORT" "${ARGS[@]}"
