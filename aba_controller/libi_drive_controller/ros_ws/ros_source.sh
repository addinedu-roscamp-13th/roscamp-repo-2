#!/usr/bin/env bash
# 모든 pm2 앱(robot-hw / nav2 / robot_agent)이 공통으로 사용하는 ROS2 환경 셋업.
#
# 부팅(systemd) 및 pm2 데몬은 ~/.bashrc 를 로드하지 않으므로,
# .bashrc 에 설정된 ROS_DOMAIN_ID 를 여기서 추출해 export 한다.
# → 도메인 값의 "단일 출처"는 항상 ~/.bashrc 이며, 세 프로세스가 동일 도메인을 쓴다.
_dom="$(grep -oE '^[[:space:]]*export[[:space:]]+ROS_DOMAIN_ID=[0-9]+' "$HOME/.bashrc" 2>/dev/null | tail -1 | grep -oE '[0-9]+$')"
if [ -n "$_dom" ]; then
  export ROS_DOMAIN_ID="$_dom"
fi

source /opt/ros/jazzy/setup.bash
source /home/pinky/pinky_pro/install/setup.bash

# [2026-07-07] 공유기 멀티캐스트 차단으로 서버(192.168.0.19)와 DDS 디스커버리 불가 → 유니캐스트 정적 피어로 해결
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
# 와이파이가 바뀌면 셸에서 미리 export 해서 덮어쓴다 (FastDDS 비교 실험용):
#   ROS_STATIC_PEERS="172.30.1.10;172.30.1.83"
export ROS_STATIC_PEERS=${ROS_STATIC_PEERS:-192.168.0.19}

# [2026-07-20] RMW: CycloneDDS (설치돼 있을 때만 — 없으면 FastDDS 로 자동 폴백).
#   Pi에서 FastDDS SHM 전송 스레드(dds.shm)가 CPU를 크게 먹어 CycloneDDS 로 교체.
#   ⚠️ 모든 머신(이 Pi + FMS 서버 192.168.0.19 + 조작 PC)이 같은 RMW 여야 통신됨.
#   ⚠️ CycloneDDS 는 위의 ROS_STATIC_PEERS 를 (버전에 따라) 안 볼 수 있어, 멀티캐스트
#      차단 환경에선 CYCLONEDDS_URI(cyclonedds.xml)로 피어를 따로 지정한다.
#   자세한 필수 조건은 같은 폴더의 RMW_CYCLONEDDS_필독.md 참고.
if [ -f /opt/ros/jazzy/lib/librmw_cyclonedds_cpp.so ]; then
  export RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}
  _rmw_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -f "$_rmw_dir/cyclonedds.xml" ] && export CYCLONEDDS_URI="file://$_rmw_dir/cyclonedds.xml"
fi
