#!/usr/bin/env bash
# 팔 보드(Handy)에서 실행 — handy.sh 가 띄운 libi_handy_controller 노드 정리.
#
#   ./kill.sh
#
# handy.sh 는 tmux 없이 `exec ros2 run ...` 으로 포그라운드에 뜬다. Ctrl+C 로 껐으면
# 이 스크립트가 할 일은 없지만, SSH 세션이 끊기거나 창만 닫으면 노드가 살아남아
# handy_cmd 를 계속 구독한다 — 다음 기동 때 노드가 둘이 되어 명령이 두 번 실행된다.
#
# 패턴을 `handy_node` 가 아니라 경로까지 붙여 좁힌 이유: `pkill -f` 는 cmdline 전체를
# 보므로, 짧은 이름은 편집기·빌드·이 파일을 열어둔 셸까지 함께 잡는다.
# `ros2 run` 이 exec 한 실제 프로세스는
#   .../install/libi_handy_controller/lib/libi_handy_controller/handy_node
# 라서 아래 패턴 하나로 `ros2 run` 부모와 노드가 모두 걸린다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

kill_patterns "libi_handy_controller/handy_node"

# 프로세스를 죽여도 ros2 daemon 이 그래프 캐시를 들고 있어 `ros2 node list` 에 죽은 노드가
# 계속 보인다. daemon 을 멈춰 캐시를 비운다(다음 ros2 명령 때 살아있는 노드로 재구성).
if command -v ros2 >/dev/null 2>&1; then
  ros2 daemon stop >/dev/null 2>&1 && echo "ros2 daemon stop (유령 노드 캐시 비움)" || true
fi
