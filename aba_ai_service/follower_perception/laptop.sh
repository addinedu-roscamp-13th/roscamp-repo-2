#!/usr/bin/env bash
# Launch the laptop (AI server + Qt viewer) as separate tmux WINDOWS (one at a time):
#   win0: server    win1: viewer
# Switch: Ctrl-b <0/1>   (or Ctrl-b n=next, p=prev, w=list)
#
#   ./laptop.sh <PI_IP>
#
# Detach: Ctrl-b d   |   Kill all: tmux kill-session -t libi_laptop
set -euo pipefail
PI_IP="${1:?usage: ./laptop.sh <PI_IP>}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # follower_perception/
REPO="$(cd "$DIR/.." && pwd)"
SESSION="libi_laptop"

# ---- edit for your machine (or override via env) ----
VENV_PY="${VENV_PY:-/home/ane/personal_repo/labeling_sam3/.venv/bin/python}"
VIDEO_PORT="${VIDEO_PORT:-6001}"
CMD_PORT="${CMD_PORT:-6002}"
VIEWER_PORT="${VIEWER_PORT:-5007}"
VIEWER="${VIEWER:-$REPO/qt_demo/build/viewer}"
# LiDAR view: server subscribes to /scan via ROS2 (needs ROS sourced + same domain).
# Set LIDAR_ARGS="" to turn the viewer's distance readout off.
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"   # 이 프로젝트는 Jazzy (ros_ws/README.md 참조)
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-119}"      # MUST match the Pi (pi.sh forces 119)
LIDAR_ARGS="${LIDAR_ARGS:---lidar-ros --lidar-flip}"   # flip = LiDAR mounted 180 deg
# -----------------------------------------------------

command -v tmux >/dev/null || { echo "tmux 없음: sudo apt install -y tmux"; exit 1; }
tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -c "$DIR" -n server
tmux send-keys -t "$SESSION:server" \
  "source '$ROS_SETUP' 2>/dev/null; export ROS_DOMAIN_ID=$ROS_DOMAIN_ID; $VENV_PY scripts/perception_server.py --udp --udp-port $VIDEO_PORT --drive-host $PI_IP --drive-port $CMD_PORT $LIDAR_ARGS" C-m

tmux new-window -t "$SESSION" -c "$DIR" -n viewer
# viewer auto-retries until the server opens :5007, so launch order doesn't matter
tmux send-keys -t "$SESSION:viewer" "'$VIEWER' 127.0.0.1 $VIEWER_PORT" C-m

tmux select-window -t "$SESSION:server"
tmux attach -t "$SESSION"
