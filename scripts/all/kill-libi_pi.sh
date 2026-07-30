#!/usr/bin/env bash
# libi_pi.sh 가 띄운 것 전부 정리 — 주행 스택 + 앞/뒤 카메라 송출 + cmd 브리지.
#
#   ./kill-libi_pi.sh
#
# 실제 종료는 scripts/drive-pi/kill.sh 가 한다. 거기가 이미 셋 다 담당하기 때문이다:
#   · tmux 세션 pinky_pi + launch 자식 노드  → ros_ws/scripts/kill.sh 로 위임
#   · camera_sender.py (앞·뒤 두 벌 모두)    → pkill 패턴 하나가 둘 다 잡는다
#   · cmd_bridge.py                          → 같은 방식
#
# 여기서 한 번 더 감싸는 이유는 하나다 — libi_pi.sh 로 띄운 사람이 정리도 같은 폴더에서
# 찾게 하려는 것. 로직을 복사하면 한쪽만 고쳐져 조용히 어긋난다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

exec "$REPO_ROOT/scripts/drive-pi/kill.sh"
