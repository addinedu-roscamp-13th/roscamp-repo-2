#!/usr/bin/env bash
# 노트북/AI 서버에서 실행 — 추종 인지 서버(perception_server)를 띄우고, 추종 명령을
# 지정한 로봇으로 내려보낸다(--drive-host). 로봇 IP 는 .env 의 PINKY{N}_IP 에서 찾는다.
#
#   ./ai_follower_service.sh pinky3               # cmd_vel 을 pinky3 로
#   ./ai_follower_service.sh pinky3 --test-pattern  # 로봇 영상 없이 확인
#
# 로봇에서 image-sender.sh(영상) 와 follow-drive.sh(cmd_bridge) 가 함께 떠 있어야 실제로 움직인다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

resolve_pinky "${1:?사용법: ./ai_follower_service.sh <pinky1|pinky2|pinky3> [ai-server 인자...]}"
[ -n "$ROBOT_IP" ] || die "$ROBOT_ID 의 IP 가 .env 에 없습니다 ($(echo "$ROBOT_ID" | tr '[:lower:]' '[:upper:]')_IP=... 를 채우세요)."

cd "$REPO_ROOT"
# ai-server.sh 가 perception_server 기동·의존성 체크·주행 경고를 담당한다.
# --robot-host : 주인 검출을 로봇 libi_perception 으로 **직접** 보낸다(TCP:6000).
#   이게 없으면 `detection_sink=None` 이라 로봇이 검출을 한 번도 못 받고,
#   추종 제어 루프가 대상을 몰라 `/cmd_vel_follow` 를 한 번도 안 낸다.
#   화면에는 STATE: FOLLOWING 과 cmd_vel 이 멀쩡히 뜬다 — 그건 AI 서버가 자기 화면에
#   그리는 미리보기일 뿐이고, 로봇을 움직이는 값이 아니다.
#   실측 2026-07-28: 이것 때문에 "카메라는 잘 뜨는데 바퀴가 안 돈다".
#
# [2026-07-28] `--drive-host` 를 **뺐다.** "호환을 위해 남겨 둔다"가 안전하지 않았다.
#
#   그 인자를 주면 AI 서버가 프레임마다 자기 DrivePolicy 속도를 UDP:6002 로 쏜다
#   (perception_server.py). 로봇에 `cmd_bridge.py` 가 떠 있으면 그걸 받아
#   **`/cmd_vel_follow` 로 20Hz 발행**한다(cmd_bridge.py) — 명령이 없어도 zero Twist 를 낸다.
#
#   그때 그 다리는 로봇의 `follow_node`(libi_perception)와 **같은 토픽**(`/cmd_vel_follow`)에
#   발행했다. twist_mux 는 **토픽 단위**로 중재하므로 같은 토픽 안의 두 발행자는
#   구별도, 우선순위 부여도 못 한다 — 마지막에 도착한 게 이긴다.
#   (그 뒤 `cmd_bridge` 기본 토픽을 `/cmd_vel_ai`(twist_mux priority 40)로 갈랐다.
#    그래도 이 인자는 안 준다 — 제어원은 하나면 충분하다.)
#   제어기 두 개가 한 토픽을 두고 싸우면 반응이 늦고 흔들린다.
#   (2026-07-28 "통합 전보다 추종이 나빠졌다" 신고 — codex 적대적 검토가 찾았다)
#
#   지금 쓰는 `scripts/all/libi_pi.sh` 는 `cmd_bridge` 를 안 띄운다. 띄우는 건 옛 단독
#   추종 런처 `aba_ai_service/follower_perception/pi.sh` 뿐이다. 그러니 평소엔 안 물린다.
#   하지만 그 둘을 같이 올리는 순간 조용히 물리므로, **보내는 쪽에서 끊는다.**
#   속도는 로봇이 스스로 만든다 — 네트워크가 끊겨도 멈출 수 있어야 하기 때문이다.
exec "$REPO_ROOT/aba_ai_service/scripts/ai-server.sh" \
     --robot-host "$ROBOT_IP" "${@:2}"
