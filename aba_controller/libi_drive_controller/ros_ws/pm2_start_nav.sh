#!/usr/bin/env bash
# 터미널 2 — nav2 + 웹서버
set -e
source /home/robotPrj/controller/drive/ros_ws/ros_source.sh

# 하드웨어(로봇)가 먼저 떠서 TF/라이다가 올라올 때까지 대기.
# /scan 토픽이 보이면 하드웨어가 준비된 것으로 판단.
echo "[nav] 하드웨어(/scan) 대기 중..."
for i in $(seq 1 60); do
  if ros2 topic list 2>/dev/null | grep -q '/scan'; then
    echo "[nav] /scan 감지됨. nav2 시작."
    break
  fi
  sleep 1
done

exec ros2 launch pinky_navigation bringup_launch.xml map:=/home/robotPrj/controller/drive/ros_ws/mymap123.yaml
