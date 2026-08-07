#!/usr/bin/env bash
# 로봇(Pi) 한 방 기동 — 주행 스택 + 앞/뒤 카메라 송출 + 추종 cmd 브리지.
# 창을 네 개 열어 손으로 치던 걸 하나로 묶는다.
#
#   ./libi_pi.sh --robot pinky-3 --domain-id 119                 # 기본 (뒷캠 자동 탐지)
#   ./libi_pi.sh --robot pinky-3 --domain-id 119 --back 4        # 뒷캠 인덱스 직접 지정
#   ./libi_pi.sh --robot pinky-3 --domain-id 119 --no-back       # 뒷캠 아예 안 열기
#   ./libi_pi.sh --robot pinky-3 --domain-id 119 --ai 172.30.1.97 # AI 서버 IP 직접 지정
#   ./libi_pi.sh --robot pinky-3 --domain-id 119 --no-battery    # [디버그] 배터리 자동 전이 OFF
#   ./libi_pi.sh --robot pinky-3 --domain-id 119 --no-fsm        # 모르는 플래그는 pi.sh 로 그대로
#   ./libi_pi.sh --robot pinky-3 --domain-id 119 --dock-debug    # shelf_dock_lidar_viewer.py 카메라 패널용 추가 송출
#
# **`--robot` 과 `--domain-id` 는 필수다.** 도메인에 기본값을 두지 않는다 — 셸 값(보통
# ~/.bashrc 의 119)을 물려쓰면 로봇이 늘었을 때 **에러 없이 안 붙는다.**
#
# ## 배터리 자동 전이 — 안 정하면 **물어본다**
#
# 배터리가 튀면 로봇이 순회 도중 제멋대로 RETURNING 으로 빠진다. 그러면 추종·길잡이·
# 동적 장애물 **무엇을 검증하든 중간에 로봇이 사라진다.** 플래그를 외우게 하지 않으려고
# 기동할 때 물어본다(20초 후 기본 켜짐). `--battery`/`--no-battery` 를 주면 안 묻는다.
#
# **터미널이 아니면 안 묻는다** — pm2·ssh 스크립트·CI 에서 입력을 기다리며 멈추면 기동
# 자체가 실패한다. 그 경우는 켜진 채로 간다(안전 방향).
#
# 끄면 배터리 임계를 닿지 않는 값으로 바꾼다. **BT 노드는 그대로**라 관제 화면
# (/admin/fsm)의 그림이 안 바뀐다 — 노드를 지우면 딴 그림이 된다.
#
#   · 저전력 → RETURNING      low=-1     안 뜸 (배터리가 음수일 수 없다)
#   · IDLE 에서 자동 PATROL    charged=1e9 안 뜸
#   · CHARGING → IDLE          ready=-1   **항상 통과** — 여기까지 막으면 도킹 순간 갇힌다
#
# 복귀는 관제 UI 에서 직접 명령해서 검증한다. `pi.sh --no-battery` 로 그대로 넘어간다.
#
# 뒷캠은 자동으로 켠다 — 인덱스는 /sys 에서 찾는다(CSI·ISP 노드는 걸러낸다).
# 못 찾으면 앞캠만으로 뜨고 경고를 찍는다. 목록을 직접 보려면: v4l2-ctl --list-devices
#
# 정리: ./kill-libi_pi.sh (같은 폴더)
#
# ## 무엇을 띄우나 (전부 tmux 세션 `pinky_pi` 안)
#
#   pi.sh 가 만드는 창들   hw · nav2 · fleet-link · fsm · led   (주행 스택)
#   follow                libi_perception — 세션·카메라선택·추종제어·요청자감시
#   cam                   앞/뒤 카메라 한 프로세스 → UDP:6001
#   keepout               통행 금지 마스크 발행            (--dyn-obstacle 줬을 때만)
#
# 같은 세션에 얹는 이유: 정리 경로를 하나로 유지한다. 세션을 지우면 전부 같이 죽고,
# 창만 닫아 살아남는 경우까지 drive-pi/kill.sh 가 이름으로 다시 쓸어담는다.
#
# ## [2026-07-27] 창 두 개가 사라졌다
#
# **cam-front / cam-back → cam 하나.** camera_sender 가 앞뒤를 한 프로세스로 잡고
# `/libi/camera_select`(BT 가 발행)에 따라 **선택된 것만** 인코딩·송출한다. 장치를 여는
# 주체가 하나로 모여, 두 프로세스가 같은 장치를 열어 앞캠이 죽던 사고가 구조적으로
# 불가능해진다. JPEG 인코딩도 한 벌만 돌아 Pi 부담이 준다. 포트도 6001 하나로 합쳤다.
#
# **follow-drive 제거.** 추종 제어가 AI 서버에서 로봇 쪽 libi_perception 으로 옮겨가
# UDP:6002 경로가 은퇴했다. 남겨 두면 그 브리지가 명령이 없을 때도 20Hz 로 정지 명령을
# 계속 쏴서, 새 PID 와 `/cmd_vel` 을 다툰다 — 이 시스템에는 중재자(twist_mux)가 없어
# **마지막에 도착한 메시지가 이긴다.**
#
# ## 카메라 인자를 왜 env 로 주나
#
# scripts/drive-pi/image-sender.sh 는 `$AI_IP` 하나만 안쪽으로 넘기고 나머지 인자를
# **버린다**(그 파일 마지막 줄). 안쪽 ros_ws/scripts/image-sender.sh 가 소스를 읽는 곳은
# `CAM_ARGS` env 다. 그래서 `--camera 0` 을 인자로 주면 조용히 무시되고 picam 이 뜬다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ⚠️ **모든 창이 같은 DDS 구현을 써야 한다.** 안 그러면 통신이 조용히 끊긴다.
#
# 2026-07-28 실측 — 로봇이 안 움직이던 진짜 원인이었다:
#     twist_mux · pinky_bringup(모터)  →  CycloneDDS   (ros_ws/scripts/pi.sh 의 ROS_SETUP)
#     libi_modes(FSM) · follow_node    →  기본값(Fast DDS)
# DDS 는 **구현이 다르면 매칭 자체를 안 만든다.** 그래서 추종이 만든
# `/cmd_vel_follow` 도, FSM 이 내는 `/libi/motion_lock` 도 twist_mux 에 영영 안 닿았다.
# 화면은 전부 정상이고 에러도 없다 — 바퀴만 안 돈다.
#
# 여기서 export 하면 아래 모든 tmux 창이 상속한다. 안쪽 pi.sh 의 ROS_SETUP 은
# `${RMW_IMPLEMENTATION:-...}` 로 받으므로 이 값이 그대로 유지된다.
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
  export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
  _CDDS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/aba_controller/libi_drive_controller/ros_ws/cyclonedds.xml"
  [ -f "$_CDDS" ] && export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://$_CDDS}"
fi

SESSION="pinky_pi"          # 안쪽 ros_ws/scripts/pi.sh:28 이 만드는 세션 이름
# [2026-07-27] BACK_PORT/BACK_FPS 는 없앴다. 앞뒤가 한 프로세스라 포트도 fps 도 하나다
# (선택된 캠만 인코딩하므로, 예전에 뒷캠 fps 를 낮춰 아끼려던 비용 자체가 사라졌다).

ROBOT=""
AI_IP=""
# [2026-07-29] 뒷캠은 **항상 켠다.** 인덱스는 아래 detect_back_cam 이 /sys 에서 찾는다.
# 예전엔 `--back <n>` 을 줘야만 떴는데, 매번 인덱스를 외워 치는 게 실제로 더 위험했다 —
# 틀린 값(특히 0)은 조용히 실패하지 않고 **앞캠을 죽인다**(아래 장치 검사 주석).
# 이제 사람이 숫자를 고르지 않는다. 못 찾으면 앞캠만으로 뜬다(경고 한 줄).
#   --back <n>  자동 탐지 대신 그 인덱스를 쓴다
#   --no-back   뒷캠을 아예 안 연다
WITH_BACK=true
BACK_CAM=""     # 빈 값 = 자동 탐지
# 동적 장애물 회피는 **기본 꺼짐**이다. 켜고 끄기가 쉬워야, 좁은 통로가 통째로 막히는
# 상황이 났을 때 즉시 되돌릴 수 있다(맵이 1.26×2.16m 라 실제로 그럴 수 있다).
WITH_DYN_OBSTACLE=false
# 서가도킹 디버그 뷰어(shelf_dock_lidar_viewer.py)용 — 켜면 앞캠을 별도 포트로도
# 추가 송출한다(camera_sender.py --debug-port, 2026-08-05). perception_server 가 이미
# 쓰는 UDP/뷰어 슬롯과 안 부딪히게 VIDEO_PORT+1000 을 쓴다 — shelf_dock_lidar_viewer.py
# 의 cam_port_for()+DEBUG_PORT_OFFSET 과 반드시 같은 규칙이어야 한다.
DOCK_DEBUG=false
DYN_OBSTACLE_FAN_DEG="${DYN_OBSTACLE_FAN_DEG:-60}"
DYN_OBSTACLE_TTL="${DYN_OBSTACLE_TTL:-20.0}"
DYN_OBSTACLE_NEAR_AREA="${DYN_OBSTACLE_NEAR_AREA:-0}"   # 0 = 정책 자체가 꺼짐
# 배터리 자동 전이. "" = 미지정 → 아래에서 물어본다. true/false = 플래그로 명시됨.
BATTERY_AUTO=""
DOMAIN_ID=""
# ── 패널 화면 (로봇 LCD) ────────────────────────────────────────────────────
#
# 로봇 **LCD(240x240 SPI)** 에 정지 이미지 한 장을 띄운다. **기본 켜짐**이다.
#   --panel <경로>  다른 이미지를 쓴다
#   --no-panel      안 띄운다
#
# ⚠️ **X(DISPLAY)로 띄우면 LCD 에 안 나온다.** 처음에 `eog -f` 로 했다가 틀렸다:
#   이 Pi 에는 물리 프레임버퍼가 없다(`/dev/fb*` 자체가 없고 `xrandr` 은 DUMMY0/1/2 뿐).
#   X 로 그린 화면은 **VNC(:5900)로 붙어야만** 보이고 LCD 로는 한 픽셀도 안 간다.
#   LCD 는 SPI 로 직접 그린다 — `lcd_ctrl.py` 가 그 통로다(root 필요).
#   리사이즈는 그쪽이 알아서 한다(`_loop_static` 의 `resize((240,240))`).
#
# ⚠️ 왜 libi_gui 가 아니라 PNG 인가 — **실측이다(2026-07-31).**
#   같은 Pi 에서 libi_gui 를 VNC 모드로 띄우니 **코어 하나의 30%**(4코어 중 7.5%)를
#   상시 먹었다. 뷰어를 안 붙였는데도 그랬다. 원인은 두 가지가 겹친 것이다:
#     · VNC QPA 는 GL 컨텍스트가 없어 `QT_QUICK_BACKEND=software` 가 강제 — 래스터가 전부 CPU
#     · `qml/components/RobotFace.qml` 의 idleBob 이 `loops: Animation.Infinite` — 화면이
#       멈추질 않으니 1280x800 프레임버퍼를 계속 다시 칠한다
#   LCD 정지 이미지는 **코어의 6.5%**(기계 전체 1.6%)다. 0 은 아니지만 1/5 이다.
#   주행·BT·추종이 이미 3코어를 쓰는 Pi 라 그 차이가 그대로 여유가 된다.
#
#   움직이는 패널이 정말 필요해지면 되돌리는 게 아니라 **idleBob 부터 끄고 다시 잰다.**
#
# ⚠️ 이 데몬은 **스택을 내려도 안 죽는다**(kill.sh 가 안 건드린다). 일부러 그렇다 —
#    로봇을 세워 둬도 얼굴은 떠 있는 게 낫다. 끄려면:
#      sudo python3 aba_controller/libi_drive_controller/robot_agent/app/hardware/lcd_ctrl.py stop
#
#: 빈 값 = 안 띄움(--no-panel). 아래 인자 처리에서 기본 경로가 채워진다.
PANEL_IMAGE="__default__"

# 값을 받는 플래그가 **다음 플래그를 값으로 먹지 않게** 막는다.
#   `--ai --no-back`  →  AI_IP="--no-back" 이 비어 있지 않아 검증을 통과하고,
#   그 주소로 영상을 쏘려다 조용히 실패했다. (codex 2차 지적)
need_arg() {   # <플래그> <값>
  case "${2:-}" in
    ''|--*) die "$1 뒤에 값이 필요합니다 (받은 값: '${2:-없음}')" ;;
  esac
  printf '%s' "$2"
}

ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot)    ROBOT="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    --domain-id) DOMAIN_ID="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    --ai)       AI_IP="$(need_arg "$1" "${2:-}")"; shift 2 ;;
    --back)     WITH_BACK=true; BACK_CAM="${2:?--back 뒤에 USB 캠 인덱스가 필요합니다 (예: --back 1).  목록: v4l2-ctl --list-devices}"; shift 2 ;;
    --no-back)  WITH_BACK=false; shift ;;
    --dyn-obstacle) WITH_DYN_OBSTACLE=true; shift ;;
--dock-debug) DOCK_DEBUG=true; shift ;;
    --no-battery)   BATTERY_AUTO=false; shift ;;
    --battery)      BATTERY_AUTO=true;  shift ;;
    # 값 없이 쓰면 기본 이미지(config/panel/libi_panel.png) — 기본 동작과 같다.
    --panel)    case "${2:-}" in
                  ''|--*) PANEL_IMAGE="__default__"; shift ;;
                  *)      PANEL_IMAGE="$(need_arg "$1" "${2:-}")"; shift 2 ;;
                esac ;;
    --no-panel) PANEL_IMAGE=""; shift ;;
    *)          ARGS+=("$1"); shift ;;     # --no-fsm 등은 pi.sh 로 그대로
  esac
done

# ── 배터리 자동 전이 — 안 정했으면 물어본다 ────────────────────────────────
# 플래그를 외우게 하지 않는다. 배터리 센서가 튀는 동안에는 이걸 매번 꺼야 하는데,
# 기억에 의존하면 검증 중간에 로봇이 RETURNING 으로 사라지고 그 원인을 한참 찾는다.
#
# **터미널이 아니면 안 묻는다** — pm2·ssh 스크립트·CI 에서 입력을 기다리며 멈추면
# 기동 자체가 실패한다. 그런 경우는 기본값(켜짐, = 안전 방향)으로 간다.
if [ -z "$BATTERY_AUTO" ]; then
  if [ -t 0 ] && [ -t 1 ]; then
    echo
    echo "  배터리로 인한 자동 상태 전이 (저전력→복귀 / 충전완료→순회)"
    echo "    끄면: 배터리 값이 튀어도 로봇이 제멋대로 복귀하지 않는다."
    echo "          복귀·순회는 관제 UI 에서 직접 눌러 검증한다."
    echo "    켜면: 평소 운영 동작. 배터리 15% 이하에서 스스로 충전소로 간다."
    read -r -t 20 -p "  배터리 자동 전이를 켤까요? [Y/n] (20초 후 Y) " _ans || _ans=""
    echo
    case "$_ans" in
      [nN]*) BATTERY_AUTO=false ;;
      *)     BATTERY_AUTO=true  ;;
    esac
  else
    BATTERY_AUTO=true
  fi
fi
if [ "$BATTERY_AUTO" = false ]; then
  ARGS+=("--no-battery")
  echo "[libi_pi] ⚠️  배터리 자동 전이 OFF — 저전력 복귀·자동 순회가 뜨지 않습니다."
  echo "         복귀는 관제 UI 에서 직접 명령하세요."
fi

ROBOT="${ROBOT:-${FSM_ROBOT_ID:-}}"
[ -n "$ROBOT" ] || die "로봇 이름이 필요합니다.  예: ./libi_pi.sh --robot pinky-3
  이름은 관제 DB(rc_robots.name)에 등록된 값과 정확히 같아야 합니다 — 다르면 fleet_node 가
  이 로봇을 못 알아보고 배차해도 움직이지 않습니다(scripts/drive-pi/pi.sh 머리말)."

AI_IP="${AI_IP:-${LAPTOP_IP:-}}"
[ -n "$AI_IP" ] || die "AI 서버 IP 가 필요합니다 — --ai 로 주거나 .env 의 LAPTOP_IP 를 채우세요."

# ── 도메인은 **반드시 인자로** ─────────────────────────────────────────────
#
# 예전엔 이 Pi 셸의 ROS_DOMAIN_ID(보통 ~/.bashrc 의 119)를 그대로 썼다. 로봇이 늘면
# 새 Pi 를 복제 설치하면서 그 119 를 물려받고, 관제는 DB 대로 117 브릿지를 열어
# **영영 안 붙는다 — 에러 한 줄 없이.** 조용히 틀리느니 안 뜨는 게 낫다.
#
# 여기서 export 하면 안쪽 pi.sh 의 ROS_SETUP 과 모든 tmux 창이 이 값을 상속한다
# (그 스크립트는 셸의 ROS_DOMAIN_ID 를 그대로 쓴다).
if [ -z "$DOMAIN_ID" ]; then
  DOMAIN_ID="$(robot_domain "$ROBOT")"
  [ -n "$DOMAIN_ID" ] && echo "[libi_pi] 도메인 $DOMAIN_ID (표: pinky-1→117 · pinky-2→118 · pinky-3→119)"
fi
[ -n "$DOMAIN_ID" ] || die "'$ROBOT' 의 도메인을 알 수 없습니다 (표는 실물 pinky-<N> 만 압니다).
  직접 주세요:  ./libi_pi.sh --robot $ROBOT --domain-id <N>
  이 값은 관제 DB(rc_robots.domain_id)와 **같아야** 합니다 — 다르면 브릿지가 로봇을 못 찾습니다."
case "$DOMAIN_ID" in
  *[!0-9]*) die "도메인은 숫자여야 합니다: '$DOMAIN_ID'" ;;
esac
if [ -n "${ROS_DOMAIN_ID:-}" ] && [ "$ROS_DOMAIN_ID" != "$DOMAIN_ID" ]; then
  echo "[libi_pi] ⚠ 셸의 ROS_DOMAIN_ID=$ROS_DOMAIN_ID 를 인자값 $DOMAIN_ID 로 덮어씁니다 (~/.bashrc 확인)"
fi
export ROS_DOMAIN_ID="$DOMAIN_ID"

# 영상 송출 포트를 로봇 번호에서 만든다. 노트북의 libi_laptop.sh 가 **같은 함수**로
# 수신 포트를 계산하므로 서로 값을 넘길 필요가 없다 — 로봇 이름만 맞으면 맞는다.
# (노트북 한 대가 로봇 2대 이상의 영상을 동시에 받으려면 포트가 갈려 있어야 한다.)
robot_ports "$ROBOT"

# ── 뒷캠 장치 검사 ──────────────────────────────────────────────────────────
# ⚠️ 실제로 당한 사고(2026-07-27): `--back-cam 0` 으로 띄웠더니 **앞캠이 죽었다.**
#
#     /dev/video0[16:cap]: Unable to set format: Device or resource busy
#     RuntimeError: Failed to configure camera: Device or resource busy
#
# Pi 에서 /dev/video0 은 USB 웹캠이 아니라 **CSI 카메라(picam)의 unicam/ISP 캡처 노드**다.
# 뒷캠이 OpenCV 로 그걸 열면 앞캠의 libcamera 가 장치를 못 잡고 죽는다. 그러면 추종 영상이
# 통째로 안 나가는데, 죽는 건 앞캠 창이라 원인이 뒷캠이라는 게 안 보인다.
#
# 그래서 띄우기 전에 막는다. 장치 이름은 /sys 에서 읽으므로 v4l2-utils 가 없어도 된다.
list_cams() {
  local f n
  for f in /sys/class/video4linux/video*/name; do
    [ -r "$f" ] || continue
    n="$(basename "$(dirname "$f")")"
    echo "    /dev/$n  $(cat "$f")"
  done
}
# 뒷캠 인덱스를 /sys 에서 찾는다. CSI·코덱·ISP 노드를 이름으로 걸러내고 남은 것 중
# **가장 낮은 번호**를 쓴다(USB 캠은 보통 capture + metadata 두 노드를 만들고, 앞쪽이 capture).
# v4l2-utils 없이 동작한다 — Pi 에 없을 수 있어서 /sys 만 읽는다.
# 후보 전부(공백 구분). 여러 개면 사람에게 알린다 — 어느 쪽이 뒷캠인지는 기계가 모른다.
detect_back_cam_all() {
  local f n name out=""
  for f in /sys/class/video4linux/video*/name; do
    [ -r "$f" ] || continue
    n="$(basename "$(dirname "$f")")"; n="${n#video}"
    case "$n" in ''|*[!0-9]*) continue ;; esac
    name="$(cat "$f")"
    case "$name" in
      *unicam*|*bcm2835*|*rp1-cfe*|*isp*|*pisp*|*rpivid*|*codec*) continue ;;
    esac
    out="$out $n"
  done
  printf '%s' "$out"
}

detect_back_cam() {
  local f n name best=""
  for f in /sys/class/video4linux/video*/name; do
    [ -r "$f" ] || continue
    n="$(basename "$(dirname "$f")")"; n="${n#video}"
    case "$n" in ''|*[!0-9]*) continue ;; esac
    name="$(cat "$f")"
    case "$name" in
      *unicam*|*bcm2835*|*rp1-cfe*|*isp*|*pisp*|*rpivid*|*codec*) continue ;;
    esac
    if [ -z "$best" ] || [ "$n" -lt "$best" ]; then best="$n"; fi
  done
  printf '%s' "$best"
}

if [ "$WITH_BACK" = true ] && [ -z "$BACK_CAM" ]; then
  BACK_CAM="$(detect_back_cam)"
  if [ -z "$BACK_CAM" ]; then
    WITH_BACK=false
    echo "[libi_pi] ⚠ USB 뒷캠을 못 찾았습니다 — 앞캠만으로 뜹니다."
    echo "           연결돼 있는데 이러면 인덱스를 직접:  --back <n>   (목록은 아래)"
    echo "$(list_cams)"
  else
    echo "[libi_pi] 뒷캠 자동 탐지: /dev/video$BACK_CAM"
    # ⚠️ capability(캡처 가능 여부)는 안 본다 — /sys 만 읽어 v4l2-utils 없이 돌기 위해서다.
    #    후보가 둘 이상이면 어느 쪽이 뒷캠인지 이 스크립트는 모른다. 그래서 알린다.
    # ⚠️ 후보가 여러 개여도 **같은 물리 장치면 정상이다** — USB 캠 하나가 보통
    #    capture + metadata 두 노드를 만든다(이 Pi 는 video1/video2). 그건 경고할 일이 아니다.
    #    USB 장치가 **둘 이상**일 때만 알린다: 그때는 어느 쪽이 뒷캠인지 기계가 모른다.
    _devs=""
    for _c in $(detect_back_cam_all); do
      _d="$(readlink -f "/sys/class/video4linux/video$_c/device" 2>/dev/null)"
      case " $_devs " in *" $_d "*) ;; *) _devs="$_devs $_d" ;; esac
    done
    set -- $_devs
    [ "$#" -gt 1 ] && echo "[libi_pi] ⚠ USB 영상 장치가 $# 개입니다 — 뒷캠이 /dev/video$BACK_CAM 이 아니면 --back <n> 으로 지정하세요"
    unset _devs _c _d
  fi
fi

if [ "$WITH_BACK" = true ]; then
  [ -e "/dev/video$BACK_CAM" ] || die "/dev/video$BACK_CAM 이 없습니다.
  이 로봇의 영상 장치:
$(list_cams)
  USB 캠 인덱스를 골라 주세요:  ./libi_pi.sh --robot $ROBOT --back <n>"

  BACK_NAME=""
  [ -r "/sys/class/video4linux/video$BACK_CAM/name" ] \
    && BACK_NAME="$(cat "/sys/class/video4linux/video$BACK_CAM/name")"
  # ⚠️ 자동 탐지(detect_back_cam)와 **같은 목록**이어야 한다. 예전엔 수동 경로가 더 헐거워서
  #    (`bcm2835`·`rpivid`·`codec` 를 안 걸렀다) 손으로 준 값이 오히려 위험했다. (codex 2차)
  case "$BACK_NAME" in
    *unicam*|*bcm2835*|*rp1-cfe*|*isp*|*pisp*|*rpivid*|*codec*)
      die "/dev/video$BACK_CAM 는 CSI 카메라(앞캠) 장치입니다 — '$BACK_NAME'
  여기에 뒷캠을 물리면 **앞캠(picam)이 'Device or resource busy' 로 죽어** 추종 영상이 끊깁니다.
  이 로봇의 영상 장치:
$(list_cams)
  USB 캠 인덱스를 골라 주세요:  ./libi_pi.sh --robot $ROBOT --back <n>" ;;
  esac
  echo "[libi_pi] 뒷캠 장치 확인: /dev/video$BACK_CAM  '${BACK_NAME:-이름 미상}'"
fi

# 이미 떠 있으면 창이 중복으로 쌓인다. 안쪽 pi.sh 도 같은 이유로 여기서 멈춘다.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 정리하세요:  $HERE/kill-libi_pi.sh"
fi

echo "[libi_pi] 영상 → $AI_IP:$VIDEO_PORT (로봇 번호에서 계산 — 노트북 libi_laptop.sh 와 같은 규칙)"
echo "[libi_pi] 로봇=$ROBOT  AI=$AI_IP  뒷캠=$([ "$WITH_BACK" = true ] && echo "/dev/video$BACK_CAM" || echo "없음")  동적장애물=$([ "$WITH_DYN_OBSTACLE" = true ] && echo "ON" || echo "OFF(--dyn-obstacle 로 켬)")"

# ── 1) 주행 스택 ────────────────────────────────────────────────────────────
# ⚠️ 파이프를 태우는 게 핵심이다. 안쪽 pi.sh 는 `[ -t 1 ]` 이면 세션에 **attach 해서
#    돌아오지 않는다**(그 파일 마지막). 그러면 아래 카메라 창들이 영영 안 뜬다.
#    stdout 을 파이프로 만들면 attach 를 건너뛰고 안내만 찍고 끝난다.
# 필터 포함 nav2 파라미터를 고르라고 안쪽 런처에 알린다. 이걸 빼면 마스크 발행 노드만
# 뜨고 nav2 는 기본 params 로 기동해 **기능이 조용히 무효가 된다.**
[ "$WITH_DYN_OBSTACLE" = true ] && ARGS+=("--keepout")
"$REPO_ROOT/scripts/drive-pi/pi.sh" --robot "$ROBOT" "${ARGS[@]}" 2>&1 | sed 's/^/[pi] /'

# 세션이 실제로 생겼는지 확인하고 나서 창을 얹는다. ensure_built 가 colcon build 를
# 도는 경우가 있어 시간이 걸릴 수 있다.
for _ in $(seq 1 20); do
  tmux has-session -t "$SESSION" 2>/dev/null && break
  sleep 0.5
done
tmux has-session -t "$SESSION" 2>/dev/null \
  || die "'$SESSION' 세션이 뜨지 않았습니다 — 위 [pi] 출력을 확인하세요."

# ── 2) 추종·길잡이 감시 노드 ────────────────────────────────────────────────
# ⚠️ ensure_built 는 `install/` 이 **아예 없을 때만** 빌드한다. 새 노드가 추가되거나
#    소스가 바뀌면 install 트리는 남아 있으면서 내용만 낡는다 — 그러면 런처가 **조용히
#    옛 코드를 돌린다.** 진입점이 없으면 빌드하고, 소스가 더 새로우면 크게 경고한다.
LIBI_WS="$REPO_ROOT/aba_controller/libi_modes/ros_ws"
FOLLOW_BIN="$LIBI_WS/install/libi_perception/lib/libi_perception/follow_node"
if [ ! -x "$FOLLOW_BIN" ]; then
  echo "[libi_pi] libi_perception 진입점이 없습니다 → colcon build (한 번만)"
  ( source /opt/ros/jazzy/setup.bash && cd "$LIBI_WS" \
    && colcon build --symlink-install --packages-select libi_perception ) \
    || die "colcon build 실패: $LIBI_WS"
elif [ -n "$(find "$LIBI_WS/src/libi_perception" -name '*.py' -newer "$FOLLOW_BIN" -print -quit 2>/dev/null)" ]; then
  echo "[libi_pi] ⚠️  libi_perception 소스가 빌드보다 새롭습니다 — **옛 코드가 돕니다.**"
  echo "         cd $LIBI_WS && colcon build --symlink-install --packages-select libi_perception"
fi

# ⚠️ 이 창이 없으면 **추종도 길잡이도 통째로 안 돈다.** 이 노드가 소유하는 것:
#      /libi/camera_select        발행 — 없으면 카메라가 영원히 none 이라 영상이 안 나간다
#      TCP:6000 검출 수신          — AI 서버가 고른 주인 검출을 받는다
#      ControlLoop(PID+LiDAR)     — 추종 주행
#      /libi/requester_visible|area 발행 — 길잡이가 사람을 보는 근거
#      /fleet_cmd{follow_admin|guide_watch|watch|stop} 수신 — 세션을 켜고 끈다
#
# 예전 `follow-drive`(UDP:6002 → cmd_bridge)를 대신한다. 그쪽은 AI 서버가 주행을
# 만들었고, 이제는 로봇이 만든다.
tmux new-window -t "$SESSION" -n follow \
  bash -c "source /opt/ros/jazzy/setup.bash && source '$REPO_ROOT/aba_controller/libi_modes/ros_ws/install/setup.bash' && echo '[follow] libi_perception — 세션·카메라선택·추종제어' && ros2 run libi_perception follow_node; exec bash"

# ── 3) 카메라 송출 ──────────────────────────────────────────────────────────
# 앞뒤를 **한 프로세스**가 잡는다. 뒷캠 인덱스를 주면 `--back-camera` 로 넘어간다.
# 어느 캠을 내보낼지는 BT 가 `/libi/camera_select` 로 정한다 — 아무 세션도 없으면
# `none` 이라 아무것도 안 나간다(캡처와 생프레임 탭은 계속 돈다).
CAM_ARGS="--picamera"
[ "$WITH_BACK" = true ] && CAM_ARGS="$CAM_ARGS --back-camera $BACK_CAM"
# ⚠️ [2026-07-30] `nice -n 10` — **카메라 설정은 하나도 안 바꾼다.** 스케줄링 우선순위만 낮춘다.
#
# 왜: 실측(Pi 4코어) camera_sender 가 CPU **77%** 를 쓴다. 그 부하에서 nav2 가 제어 주기를
# 놓쳤고(`Control loop missed its desired rate of 10.0000 Hz`) 순회가 20초씩 멈췄다.
#
# 총량이 부족한 것도 맞지만, **같은 우선순위로 경쟁하는 것**이 더 직접적인 원인이다.
# 영상은 몇 프레임 늦어도 추종 품질이 조금 떨어질 뿐인데, 제어 루프가 늦으면 로봇이 선다.
# 그래서 영상 쪽을 양보시킨다.
#
# 양수 nice 는 권한이 필요 없다(낮추는 것만 한다). 되돌리려면 `nice -n 10 ` 만 지운다.
#
# ── [2026-07-30 저녁] 위 주석의 "JPEG 인코딩이 원인"은 **틀렸다.** FPS=10 으로 내린 근거 ──
#
# 원래 여기 "JPEG 를 CPU 로 인코딩한다(udp_video.py:30 cv2.imencode)"라고 적혀 있었다.
# 두 가지가 그 진단과 안 맞는다:
#
#   ① 순회 중에는 `cv2.imencode` 가 **한 번도 안 불린다.** camera_select 가 `none` 이면
#      camera_sender.py:147 `if choice != "none":` 에서 걸러져 송출 경로 전체를 건너뛴다.
#      그런데도 77% 를 쓴다 — 즉 인코딩은 범인일 수가 없다.
#   ② 인코딩 비용 자체가 작다. 640x480 실측: q90 0.78ms · q70 0.67ms · q30 0.66ms.
#      10fps 면 코어의 **0.7%** 다. 품질을 70→30 으로 내려도 바이트만 줄지 시간은 그대로다
#      (DCT 는 품질과 무관하고 엔트로피 코딩만 줄어든다).
#
# 실제로 도는 것은 **캡처 두 벌 + 회전 + 탭 기록**이고, 이것들은 `none` 이어도 계속 돈다
# (그래야 마커 도킹이 프레임을 얻는다 — camera_sender.py 머리말의 계약).
# 셋 다 프레임 수에 **정비례**하므로 유일하게 공짜인 손잡이가 fps 다.
#
# ── [2026-07-30 2차] fps 15 복귀 + **앞캠만 480x360** ───────────────────────────
#
# 1차로 fps 15→10 을 했더니 77% → 62% 로 떨어지고 스로틀도 풀렸다(84.7°C·0xe0008 →
# 76.9°C·0x0). 그런데 **추종 반응이 둔해졌다** — 실측 `cmd_vel_follow` 가 9.60Hz 였다.
# 검출이 카메라 fps 에 묶여 있어서, fps 를 내리면 제어 출력이 그대로 따라 내려간다.
#
# 그래서 프레임 수 대신 **프레임 크기**를 줄인다. 480x360 은 640x480 대비 픽셀이 56% 다.
#   순 효과: 0.56(픽셀) × 1.5(fps 10→15) = **0.84** — 지금보다도 싸고 반응은 15fps.
#
# ⚠️ **뒷캠은 640x480 그대로 둔다.** 같이 480 으로 내리려 했는데 이 USB 캠이 거부했다:
#    실측 지원 4:3 모드가 **320x240 · 640x480 · 800x600 뿐**이라, 480x360 을 요청하면
#    **640x360(16:9)** 으로 열린다 — 폭은 그대로고 세로만 잘려 **화각이 바뀐다.**
#    그 상태로 YOLO·마커를 보면 조용히 다른 그림을 본다. 그래서 두 캠을 갈랐다
#    (`--width` = 앞캠, `--back-width` = 뒷캠). 뒷캠도 줄이려면 `--back-width 320`
#    인데 픽셀이 25% 라 검출 거리를 크게 잃는다 — 필요할 때 재고 결정할 것.
#
#    이 사실은 `_camera_frames` 가 설정 후 실제 값을 찍게 해서 잡았다
#    (`[cam1] 캡처 해상도 요청 480x360 → 실제 640x360 ⚠️ 요청과 다름`).
#    해상도를 손대면 **그 줄을 반드시 확인**할 것 — 조용히 어긋나는 종류다.
#
# 잃는 것: YOLO 가 `imgsz=640` 으로 레터박싱하므로(detector.py — imgsz 미전달, ultralytics
# 기본값) 480 은 서버에서 도로 640 으로 늘어난다. **연산량은 그대로고 검출 거리가 준다.**
# 뒷캠도 YOLO 를 탄다(follow_node `_peek_people` — 회복 트리가 반대 캠으로 사람을 찾는다).
# 마커 도킹도 해상도가 인식 거리다. 도킹이나 검출 거리가 나빠지면 **여기부터 되돌려라.**
#
# 되돌리기: `--width 480` 을 지우면 640.
#
# ## [2026-08-07] 17 → **15** (사용자 지정)
#
# ⚠️ **프레임 단위 상수 셋이 같이 움직인다.** 안 고치면 조용히 어긋난다:
#     · `follower_perception/constants.py COAST_LIMIT` 24 → **21**
#       (프레임 단위다. 1.4초 유지 = 1.4×15. 안 고치면 1.6초가 되어
#        `libi_perception/config.py GUIDE_COAST_SEC`(1.4초)와 어긋난다)
#     · `scripts/security_recorder.py SecurityParams.fps` 17 → **15**
#       (프리롤 링버퍼 길이 = preroll_sec × fps)
#     · `scripts/security_recorder.py FfmpegClipWriter(fps=)` 17 → **15**
#       (안 맞으면 저장된 mp4 가 빨리감기/느리게 재생된다)
#
# ## [2026-07-31] 15 → 17
#
# 아래 모델로 320px 17fps = 16.45 + 17×2.91 = **65.9%** 다(지금 60.1%, +5.8%p).
# 스로틀이 걸렸던 지점은 640px 15fps 의 **85.0%**(84.7°C·0xe0008) 라 그것과는 거리가 있다.
# 노트북 쪽도 여유가 있다 — `POSE_EVERY_N_FRAMES = 1` 이라 pose 가 매 프레임 도는데
# 13.4ms × 17 = 228ms/s = 코어의 23% 다.
#
# ⚠️ **CPU% 가 아니라 온도를 봐라.** 지난번에 실제로 망가진 것은 점유율이 아니라
#    스로틀이었다. 올린 뒤 **부하 상태에서** 한 번 확인할 것:
#      vcgencmd measure_temp ; vcgencmd get_throttled     # 0x0 이 아니면 되돌린다
# [2026-07-30 3차] **두 캠 다 320x240.** 뒷캠이 수용하는 유일한 저해상도 4:3 이다(실측).
#
# ## ⚠️ 비용은 픽셀에 단순 비례하지 않는다 — 앞선 두 예측이 다 틀렸다
#
# 1차에 "fps 33% 감소" 2차에 "픽셀 비례로 46%" 라고 적었는데 둘 다 빗나갔다.
# 3점을 실측해서 모델을 세웠다 (camera_sender 단독, 15초 평균, 앞뒤 두 캠):
#
#     320px  5fps  ->  31.0%        640px 15fps  ->  85.0%
#     320px 15fps  ->  60.1%
#
#     비용 ≈ 16.4%(고정) + 2.91%/fps(프레임당) + 1.66%/fps(픽셀분, 640 기준)
#
#   검산: 640@10fps 예측 62.1% / 실측 62.3%.  320@15fps 예측 60.0% / 실측 60.1%.
#
# 즉 **고정비 16% 가 있고, 프레임당 비용의 대부분이 픽셀과 무관**하다(V4L2·libcamera
# 왕복, 파이썬 루프, 탭 파일 생성·rename). 해상도를 내려도 지우는 건 `1.66%/fps` 뿐이다.
# **fps 가 해상도보다 두 배 가까이 센 손잡이다** — 더 줄여야 하면 fps 부터 만져라.
#
# ⚠️ 대가는 **검출 거리**다. YOLO 가 imgsz=640 으로 업스케일하므로(detector.py, imgsz
#    미전달) 먼 사람과 작은 마커가 불리해진다. 추종이 멀리서 안 잡히거나 도킹이 마커를
#    늦게 찾으면 **여기부터 되돌린다.**
#
# ⚠️ 뒷캠 실측 수용 모드: 320x240·640x480·800x600(4:3), 352x288, 640x360·1280x720(16:9).
#    480x360 과 360x270 은 **거부**하고 각각 640x360·352x288 로 연다 — 화각이 바뀐다.
#    그래서 두 캠을 같은 값으로 맞출 수 있는 저해상도는 320x240 뿐이다.
CAM_ARGS="$CAM_ARGS --width 320 --back-width 320"
DOCK_DEBUG_PORT=""
if [ "$DOCK_DEBUG" = true ]; then
  DOCK_DEBUG_PORT=$((VIDEO_PORT + 1000))
  CAM_ARGS="$CAM_ARGS --debug-port $DOCK_DEBUG_PORT"
  echo "[libi_pi] 도킹 디버그 캠 → $AI_IP:$DOCK_DEBUG_PORT (shelf_dock_lidar_viewer.py 기본 포트와 같은 규칙)"
fi
tmux new-window -t "$SESSION" -n cam \
  bash -c "cd '$REPO_ROOT' && echo '[cam] 앞/뒤 → $AI_IP:$VIDEO_PORT (BT 가 camera_select 로 고름, nice+10, 15fps, 320x240)' && VIDEO_PORT='$VIDEO_PORT' CAM_ARGS='$CAM_ARGS' FPS='15' nice -n 10 ./scripts/drive-pi/image-sender.sh '$AI_IP'; exec bash"

# ── 4) 동적 장애물 (기본 꺼짐) ──────────────────────────────────────────────
# 켜면 nav2 가 필터 포함 파라미터로 뜨고 마스크 발행 노드가 함께 돈다.
# 끄면 이 기능이 없던 때와 **완전히 같은 경로**다 — 문제가 나면 플래그만 빼면 된다.
if [ "$WITH_DYN_OBSTACLE" = true ]; then
  tmux new-window -t "$SESSION" -n keepout \
    bash -c "cd '$REPO_ROOT' && echo '[keepout] 통행 금지 마스크 발행 (부채꼴 ${DYN_OBSTACLE_FAN_DEG}° / TTL ${DYN_OBSTACLE_TTL}s)' && source /opt/ros/jazzy/setup.bash && source '$REPO_ROOT/aba_controller/libi_modes/ros_ws/install/setup.bash' && ros2 run libi_perception keepout_node --ros-args -p fan_deg:=$DYN_OBSTACLE_FAN_DEG -p ttl_sec:=$DYN_OBSTACLE_TTL -p near_area_max:=$DYN_OBSTACLE_NEAR_AREA; exec bash"
fi

# ── 5) 패널 화면 — 로봇 LCD (기본 켜짐 · --no-panel 로 끈다) ─────────────────
# 근거·수치는 PANEL_IMAGE 선언부 주석 참고 (X 로는 LCD 에 안 나온다 · 코어 6.5%).
[ "$PANEL_IMAGE" = "__default__" ] && PANEL_IMAGE="$REPO_ROOT/config/panel/libi_panel.png"
if [ -n "$PANEL_IMAGE" ]; then
  LCD_CTRL="$REPO_ROOT/aba_controller/libi_drive_controller/robot_agent/app/hardware/lcd_ctrl.py"
  # 어느 실패든 **경고만 하고 계속 간다.** 그림 한 장 때문에 주행 스택을 못 띄우면 손해가 크다.
  if [ ! -f "$PANEL_IMAGE" ]; then
    echo "[libi_pi] ⚠ 패널 이미지가 없습니다 — 건너뜁니다: $PANEL_IMAGE" >&2
  elif [ ! -f "$LCD_CTRL" ]; then
    echo "[libi_pi] ⚠ lcd_ctrl.py 가 없습니다 — 패널을 건너뜁니다: $LCD_CTRL" >&2
  else
    # ⚠️ `sudo -n`(비대화)로 준다. 비밀번호를 물으면 기동이 거기서 멈춘다
    #    (kill.sh 의 LED 소등이 같은 이유로 `-n` 을 쓴다).
    # lcd_ctrl.py 는 스스로 데몬화해서 즉시 반환한다 — tmux 창이 필요 없다.
    if sudo -n python3 "$LCD_CTRL" image "$PANEL_IMAGE" 2>/dev/null | grep -q '^OK'; then
      echo "[libi_pi] ── 패널 → LCD 240x240 ($(basename "$PANEL_IMAGE"))"
    else
      echo "[libi_pi] ⚠ LCD 표시 실패 — 건너뜁니다 (sudo -n 이 막혔거나 SPI 가 없다)" >&2
    fi
  fi
fi

echo "[libi_pi] 정리: $HERE/kill-libi_pi.sh"

# 세션에 붙여서 끝낸다 — 창을 눈으로 보며 디버깅하는 게 목적이다.
# (비-TTY 면 tmux_attach 가 안내만 찍고 넘어간다 — _common.sh)
#
# 창 이동: Ctrl+b 0..8   ·  분리: Ctrl+b d
# 처음 볼 곳은 fleet-link 다 — ros_bridge 가 죽으면 여기에만 사유가 찍히고,
# 그게 죽으면 모든 goal 이 "ROS 브리지가 활성화되지 않았습니다" 로 실패한다.
tmux select-window -t "$SESSION:fleet-link" 2>/dev/null || true
tmux_attach "$SESSION"
