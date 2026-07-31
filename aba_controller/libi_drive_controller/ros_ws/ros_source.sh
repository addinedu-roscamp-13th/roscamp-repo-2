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

# [2026-07-07] 공유기 멀티캐스트 차단으로 서버(192.168.1.4)와 DDS 디스커버리 불가 → 유니캐스트 정적 피어로 해결
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
export ROS_STATIC_PEERS=192.168.1.4
