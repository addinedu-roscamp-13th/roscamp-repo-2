#!/usr/bin/env bash
# libi_gui(로봇 터치패널)를 "어느 로봇의 패널인지" 지정해서 띄운다.
#
#   ./gui.sh pinky3                       # robot_id 지정
#   FMS_URL=http://192.168.0.9:9001 ./gui.sh pinky3
#
# robot_id 는 FMS 승인 요청의 키다 — libi_modes 가 상태를 발행할 때 쓰는 값과 반드시 같아야
# 한다(pi.sh 의 FSM_ROBOT_ID). 다르면 FMS 가 "알 수 없는 로봇"으로 거부한다.
#
# ROS_DOMAIN_ID 는 여기서 정하지 않는다 — 실물은 로봇마다 도메인이 고정(87/88/89)이라
# 셸에 이미 설정된 값을 그대로 쓴다(pi.sh 와 같은 원칙). GUI 가 도메인을 직접 쓰진
# 않지만, 어느 로봇 패널인지 확인할 수 있게 기동 로그에 함께 찍는다.
set -eo pipefail
# -u 는 안 쓴다 — ROS2 setup.bash 계열과 함께 source 될 때 미정의 변수로 죽는다
# (sim.sh/pi.sh/ros-domain-bridge.sh 도 같은 이유로 -e 만 쓴다).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 첫 인자가 옵션(`-` 로 시작)이면 robot_id 가 아니다 — 그대로 받으면 `--vnc` 라는 이름의
# 로봇으로 FMS 승인을 요청하게 되고, 거부 사유가 "알 수 없는 로봇"이라 원인이 안 드러난다.
case "${1:-}" in -*|"") ;; *) ROBOT_ID_ARG="$1" ;; esac
export ROBOT_ID="${ROBOT_ID_ARG:-${ROBOT_ID:-}}"
export FMS_URL="${FMS_URL:-http://127.0.0.1:9001}"
# 도서 검색/추천이 붙는 곳(회원 앱과 같은 aba_service backend). 로봇이 아니라 서버
# 머신이므로 기본값이 맞는 경우는 거의 없다 — 실제 서버 주소로 넘겨야 한다.
export ABA_SERVICE_URL="${ABA_SERVICE_URL:-http://127.0.0.1:8000}"
# 추종 화면이 붙을 AI 서버(perception_server). 로봇이 아니라 별도 머신이므로 기본값이
# 맞는 경우는 거의 없다 — 실제 AI 서버 주소로 넘겨야 영상이 뜬다.
export PERCEPTION_URL="${PERCEPTION_URL:-127.0.0.1:5007}"

# ── VNC 모드 ──────────────────────────────────────────────────────────────────
# 창을 이 PC 에 띄우는 대신 **앱 자체가 VNC 서버가 된다**(Qt 의 vnc QPA 플러그인).
# 태블릿/로봇 패널에서 뷰어로 붙어 같은 화면을 본다. 화면 캡처도, X11 도, 별도
# 미러링 도구(Weylus 등)도 필요 없다 — Wayland 세션에서도 그대로 된다.
#
# ⚠️ GL 컨텍스트가 없어 `QT_QUICK_BACKEND=software` 가 **필수**다. 이 QML 에는
#    ShaderEffect·파티클이 없어 화면은 동일하게 나오지만, 렌더가 CPU 로 간다.
#    추종 화면(카메라 스트림)은 그만큼 CPU 를 더 쓴다 — 실측 후 판단할 것.
# **기본이 VNC 다.** 패널은 태블릿·로봇 화면으로 보는 것이고, 개발 PC 에 창을 띄우는 건
# 예외다 — 창이 필요하면 `--window` 로 명시한다.
VNC="${VNC:-1}"
VNC_SIZE="${VNC_SIZE:-1280x800}"   # Style.js 의 screenW/screenH 와 같은 설계 해상도
VNC_PORT="${VNC_PORT:-5900}"

# robot_id 뒤의 인자는 앱으로 그대로 넘긴다. VNC 관련 것만 여기서 걷어낸다.
PASS_ARGS=()
# $1 이 robot_id 일 때만 걷어낸다. `./gui.sh --vnc` 처럼 옵션이 먼저 오면 robot_id 가 아니다
# (안 가르면 `--vnc` 를 robot_id 로 오인해 그 이름으로 FMS 승인을 요청한다).
case "${1:-}" in -*|"") ;; *) shift ;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --vnc)       VNC=1 ;;
    --window)    VNC=0 ;;   # 이 PC 에 창을 띄운다(예전 기본값)
    # 값이 빠지면 다음 플래그를 값으로 먹는다 — set -e 로 조용히 죽지 말고 이유를 남긴다.
    --vnc-size)  case "${2:-}" in ""|-*) echo "[gui] --vnc-size 뒤에 값이 필요합니다 (예: 1280x800)"; exit 1 ;; esac
                 VNC_SIZE="$2"; shift ;;
    --vnc-port)  case "${2:-}" in ""|-*) echo "[gui] --vnc-port 뒤에 값이 필요합니다 (예: 5900)"; exit 1 ;; esac
                 VNC_PORT="$2"; shift ;;
    *)           PASS_ARGS+=("$1") ;;
  esac
  shift
done

if [ -z "$ROBOT_ID" ]; then
  echo "[gui] 사용법: ./gui.sh <robot_id> [--window] [--vnc-size 1280x800] [--vnc-port 5900]"
  echo "[gui]        (예: ./gui.sh pinky3        — 기본이 VNC. 태블릿/로봇 패널에서 붙는다)"
  echo "[gui]        (   ./gui.sh pinky3 --window — 이 PC 에 창을 띄운다)"
  echo "[gui] robot_id 는 pi.sh 의 FSM_ROBOT_ID 와 같은 값이어야 합니다."
  exit 1
fi

BIN="$SCRIPT_DIR/build/libi_gui"
if [ ! -x "$BIN" ]; then
  echo "[gui] 빌드가 없습니다. 먼저 빌드하세요:"
  echo "  cmake -S '$SCRIPT_DIR' -B '$SCRIPT_DIR/build' -DCMAKE_BUILD_TYPE=Release && cmake --build '$SCRIPT_DIR/build' -j"
  exit 1
fi

echo "[gui] robot_id=$ROBOT_ID  domain=${ROS_DOMAIN_ID:-(미설정)}  fms=$FMS_URL  aba=$ABA_SERVICE_URL  perception=$PERCEPTION_URL"

# ROS2 런타임 (rclcpp 노드 — /libi/fsm_state 구독, ui_last_touch_at·fleet_cmd 발행)
[ -f /opt/ros/jazzy/setup.bash ] && source /opt/ros/jazzy/setup.bash

if [ "$VNC" = 1 ]; then
  export QT_QUICK_BACKEND=software
  MY_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "[gui] VNC 모드 — 뷰어로 ${MY_IP:-<이 PC IP>}:$VNC_PORT 에 접속하세요 (해상도 $VNC_SIZE)"
  echo "[gui]   태블릿 뷰어(bVNC/RealVNC)로 붙는다. 로봇에는 띄울 화면이 없다 —"
  echo "[gui]   로봇 LCD 는 240x240 SPI(pinky_lcd_server)라 이 화면이 안 들어간다."
  echo "[gui]   ⚠️ 비밀번호가 없다. 같은 LAN 안에서만 쓰고, 밖으로 열려면 ssh -L 로 감쌀 것."
  exec "$BIN" -platform "vnc:size=$VNC_SIZE:port=$VNC_PORT" "${PASS_ARGS[@]}"
fi

exec "$BIN" "${PASS_ARGS[@]}"
