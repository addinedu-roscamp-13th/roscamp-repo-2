#!/usr/bin/env bash
# 로봇(Pi) 한 방 기동 — 주행 스택 + 앞/뒤 카메라 송출 + 추종 cmd 브리지.
# 창을 네 개 열어 손으로 치던 걸 하나로 묶는다.
#
#   ./pi-all.sh --robot Pinky-3                       # 앞캠만 (기본)
#   ./pi-all.sh --robot Pinky-3 --back 4              # 뒷캠 /dev/video4 도 함께
#   ./pi-all.sh --robot Pinky-3 --ai 192.168.1.10     # AI 서버 IP 직접 지정
#   ./pi-all.sh --robot Pinky-3 --no-battery          # [디버그] 배터리 자동 전이 OFF
#   ./pi-all.sh --robot Pinky-3 --battery             # 물어보지 말고 켠 채로
#   ./pi-all.sh --robot Pinky-3 --no-fsm              # 모르는 플래그는 pi.sh 로 그대로 넘어간다
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
# 뒷캠 인덱스는 로봇마다 다르다. 목록:  v4l2-ctl --list-devices
#
# 정리: ./kill-pi.sh (같은 폴더)
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
SESSION="pinky_pi"          # 안쪽 ros_ws/scripts/pi.sh:28 이 만드는 세션 이름
# [2026-07-27] BACK_PORT/BACK_FPS 는 없앴다. 앞뒤가 한 프로세스라 포트도 fps 도 하나다
# (선택된 캠만 인코딩하므로, 예전에 뒷캠 fps 를 낮춰 아끼려던 비용 자체가 사라졌다).

ROBOT=""
AI_IP=""
# 뒷캠은 **명시할 때만** 뜬다(opt-in). 기본값을 두면 안 되는 이유는 아래 장치 검사 주석 참고 —
# 인덱스는 로봇마다 다르고, 틀린 값은 조용히 실패하지 않고 **앞캠을 죽인다.**
# laptop-all.sh 의 --back 과 같은 결이다.
WITH_BACK=false
# 동적 장애물 회피는 **기본 꺼짐**이다. 켜고 끄기가 쉬워야, 좁은 통로가 통째로 막히는
# 상황이 났을 때 즉시 되돌릴 수 있다(맵이 1.26×2.16m 라 실제로 그럴 수 있다).
WITH_DYN_OBSTACLE=false
DYN_OBSTACLE_FAN_DEG="${DYN_OBSTACLE_FAN_DEG:-60}"
DYN_OBSTACLE_TTL="${DYN_OBSTACLE_TTL:-20.0}"
DYN_OBSTACLE_NEAR_AREA="${DYN_OBSTACLE_NEAR_AREA:-0}"   # 0 = 정책 자체가 꺼짐
# 배터리 자동 전이. "" = 미지정 → 아래에서 물어본다. true/false = 플래그로 명시됨.
BATTERY_AUTO=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --robot)    ROBOT="${2:-}"; shift 2 ;;
    --ai)       AI_IP="${2:-}"; shift 2 ;;
    --back)     WITH_BACK=true; BACK_CAM="${2:?--back 뒤에 USB 캠 인덱스가 필요합니다 (예: --back 1).  목록: v4l2-ctl --list-devices}"; shift 2 ;;
    --dyn-obstacle) WITH_DYN_OBSTACLE=true; shift ;;
    --no-battery)   BATTERY_AUTO=false; shift ;;
    --battery)      BATTERY_AUTO=true;  shift ;;
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
  echo "[pi-all] ⚠️  배터리 자동 전이 OFF — 저전력 복귀·자동 순회가 뜨지 않습니다."
  echo "         복귀는 관제 UI 에서 직접 명령하세요."
fi

ROBOT="${ROBOT:-${FSM_ROBOT_ID:-}}"
[ -n "$ROBOT" ] || die "로봇 이름이 필요합니다.  예: ./pi-all.sh --robot Pinky-3
  이름은 관제 DB(rc_robots.name)에 등록된 값과 정확히 같아야 합니다 — 다르면 fleet_node 가
  이 로봇을 못 알아보고 배차해도 움직이지 않습니다(scripts/drive-pi/pi.sh 머리말)."

AI_IP="${AI_IP:-${LAPTOP_IP:-}}"
[ -n "$AI_IP" ] || die "AI 서버 IP 가 필요합니다 — --ai 로 주거나 .env 의 LAPTOP_IP 를 채우세요."

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
if [ "$WITH_BACK" = true ]; then
  [ -e "/dev/video$BACK_CAM" ] || die "/dev/video$BACK_CAM 이 없습니다.
  이 로봇의 영상 장치:
$(list_cams)
  USB 캠 인덱스를 골라 주세요:  ./pi-all.sh --robot $ROBOT --back <n>"

  BACK_NAME=""
  [ -r "/sys/class/video4linux/video$BACK_CAM/name" ] \
    && BACK_NAME="$(cat "/sys/class/video4linux/video$BACK_CAM/name")"
  case "$BACK_NAME" in
    *unicam*|*bcm2835*|*rp1-cfe*|*isp*|*pisp*)
      die "/dev/video$BACK_CAM 는 CSI 카메라(앞캠) 장치입니다 — '$BACK_NAME'
  여기에 뒷캠을 물리면 **앞캠(picam)이 'Device or resource busy' 로 죽어** 추종 영상이 끊깁니다.
  이 로봇의 영상 장치:
$(list_cams)
  USB 캠 인덱스를 골라 주세요:  ./pi-all.sh --robot $ROBOT --back <n>" ;;
  esac
  echo "[pi-all] 뒷캠 장치 확인: /dev/video$BACK_CAM  '${BACK_NAME:-이름 미상}'"
fi

# 이미 떠 있으면 창이 중복으로 쌓인다. 안쪽 pi.sh 도 같은 이유로 여기서 멈춘다.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  die "'$SESSION' 세션이 이미 떠 있습니다. 먼저 정리하세요:  $HERE/kill-pi.sh"
fi

echo "[pi-all] 로봇=$ROBOT  AI=$AI_IP  뒷캠=$([ "$WITH_BACK" = true ] && echo "/dev/video$BACK_CAM" || echo "없음(--back <n> 으로 켬)")  동적장애물=$([ "$WITH_DYN_OBSTACLE" = true ] && echo "ON" || echo "OFF(--dyn-obstacle 로 켬)")"

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
  echo "[pi-all] libi_perception 진입점이 없습니다 → colcon build (한 번만)"
  ( source /opt/ros/jazzy/setup.bash && cd "$LIBI_WS" \
    && colcon build --symlink-install --packages-select libi_perception ) \
    || die "colcon build 실패: $LIBI_WS"
elif [ -n "$(find "$LIBI_WS/src/libi_perception" -name '*.py' -newer "$FOLLOW_BIN" -print -quit 2>/dev/null)" ]; then
  echo "[pi-all] ⚠️  libi_perception 소스가 빌드보다 새롭습니다 — **옛 코드가 돕니다.**"
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
tmux new-window -t "$SESSION" -n cam \
  bash -c "cd '$REPO_ROOT' && echo '[cam] 앞/뒤 → $AI_IP:6001 (BT 가 camera_select 로 고름)' && CAM_ARGS='$CAM_ARGS' ./scripts/drive-pi/image-sender.sh '$AI_IP'; exec bash"

# ── 4) 동적 장애물 (기본 꺼짐) ──────────────────────────────────────────────
# 켜면 nav2 가 필터 포함 파라미터로 뜨고 마스크 발행 노드가 함께 돈다.
# 끄면 이 기능이 없던 때와 **완전히 같은 경로**다 — 문제가 나면 플래그만 빼면 된다.
if [ "$WITH_DYN_OBSTACLE" = true ]; then
  tmux new-window -t "$SESSION" -n keepout \
    bash -c "cd '$REPO_ROOT' && echo '[keepout] 통행 금지 마스크 발행 (부채꼴 ${DYN_OBSTACLE_FAN_DEG}° / TTL ${DYN_OBSTACLE_TTL}s)' && source /opt/ros/jazzy/setup.bash && source '$REPO_ROOT/aba_controller/libi_modes/ros_ws/install/setup.bash' && ros2 run libi_perception keepout_node --ros-args -p fan_deg:=$DYN_OBSTACLE_FAN_DEG -p ttl_sec:=$DYN_OBSTACLE_TTL -p near_area_max:=$DYN_OBSTACLE_NEAR_AREA; exec bash"
fi

echo "[pi-all] 정리: $HERE/kill-pi.sh"

# 세션에 붙여서 끝낸다 — 창을 눈으로 보며 디버깅하는 게 목적이다.
# (비-TTY 면 tmux_attach 가 안내만 찍고 넘어간다 — _common.sh)
#
# 창 이동: Ctrl+b 0..8   ·  분리: Ctrl+b d
# 처음 볼 곳은 fleet-link 다 — ros_bridge 가 죽으면 여기에만 사유가 찍히고,
# 그게 죽으면 모든 goal 이 "ROS 브리지가 활성화되지 않았습니다" 로 실패한다.
tmux select-window -t "$SESSION:fleet-link" 2>/dev/null || true
tmux_attach "$SESSION"
