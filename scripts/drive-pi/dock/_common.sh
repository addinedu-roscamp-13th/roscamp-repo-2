# 주차 디버깅 스크립트들이 공유하는 부분. source 전용.
#
# robot_agent(FastAPI) 의 /park/* 라우터를 HTTP 로 부른다.
# ⚠️ pi.sh 는 그 서버를 안 띄운다(ROS 스레드 두 개만). 먼저 띄울 것:
#       cd ~/controller/drive/robot_agent && pm2 start ecosystem.config.js
#
# ⚠️ 포트 9001 이 기계마다 다른 것을 가리킨다:
#       로봇   robot_agent (주차 라우터가 여기 있다)
#       노트북 FMS 백엔드  (주차 라우터가 **없다**)
#    노트북에서 실수로 돌리면 `{"detail":"Not Found"}` 가 온다 — 로봇이 아니라 서버를
#    부른 것이다. 원격으로 로봇을 부르려면 ROBOT_AGENT_HOST 를 준다.
BASE="http://${ROBOT_AGENT_HOST:-127.0.0.1}:${ROBOT_AGENT_PORT:-9001}/api"

# 주차 진입각(도). 입구(0.581,-0.033) → 주차장(-0.001,-0.033) 은 y 가 같아 180° 직선이다.
# ⚠️ waypoint.yaml 의 `입구` yaw 는 90° 라 그대로 도착하면 옆을 본다. 그래서 회전이 필요하다.
APPROACH_YAW="${DOCK_APPROACH_YAW_DEG:-180}"

# 진입각을 무엇으로 잡을지.
#   odom   (기본) TF yaw. 카메라를 안 쓴다 — 실제로 배선된 경로다.
#   marker 아르코 마커를 **각도 센서로만** 쓴다. 도킹 컨트롤러는 여전히 라인이다.
#          DOCK_MARKER_ID 를 주면 이쪽이 된다.
ROTATE_REF="${DOCK_ROTATE_REF:-odom}"
MARKER_ID="${DOCK_MARKER_ID:-}"
MARKER_DICT="${DOCK_MARKER_DICT:-DICT_4X4_50}"

dock_cfg() {
  if [ -n "$MARKER_ID" ]; then
    printf '{"approach_yaw_deg": %s, "rotate_ref": "marker", "marker_id": %s, "marker_dict": "%s"}' \
      "$APPROACH_YAW" "$MARKER_ID" "$MARKER_DICT"
  else
    printf '{"approach_yaw_deg": %s, "rotate_ref": "%s"}' "$APPROACH_YAW" "$ROTATE_REF"
  fi
}

dock_post() {   # dock_post <경로> [json]
  curl -s -m 200 -X POST "$BASE/$1" -H 'Content-Type: application/json' \
       -d "${2:-$(dock_cfg)}"
  echo
}
