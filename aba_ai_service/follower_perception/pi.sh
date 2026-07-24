#!/usr/bin/env bash
# Launch the 3 Pi (robot) processes as separate tmux WINDOWS (one full-screen at a time):
#   win0: bringup   win1: camera (video -> AI)   win2: cmd (cmd -> /cmd_vel)
# Switch: Ctrl-b <0/1/2>   (or Ctrl-b n=next, p=prev, w=list)
#
#   ./pi.sh <AI_SERVER_IP>
#
# Detach: Ctrl-b d   |   Kill all: tmux kill-session -t libi_pi
set -euo pipefail
AI_IP="${1:?usage: ./pi.sh <AI_SERVER_IP>}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # follower_perception/
SESSION="libi_pi"

# ---- edit for your robot (or override via env) ----
ROS_SETUP="${ROS_SETUP:-/home/pinky/pinky_pro/install/setup.bash}"
BRINGUP_CMD="${BRINGUP_CMD:-ros2 launch pinky_bringup bringup_robot.launch.xml}"
# ROS_DOMAIN_ID MUST be identical for bringup + cmd, else cmd_bridge can't see
# /scan and /cmd_vel never reaches the motors. Force it so tmux windows can't drift.
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-119}"
VIDEO_PORT="${VIDEO_PORT:-6001}"
CMD_PORT="${CMD_PORT:-6002}"
CAM_ARGS="${CAM_ARGS:---picamera --fps 15}"
CMD_ARGS="${CMD_ARGS:---flip-180}"   # this robot's LiDAR is mounted rotated 180 deg
# ---------------------------------------------------
echo "pi.sh: ROS_DOMAIN_ID=$ROS_DOMAIN_ID (bringup + cmd must match)"

command -v tmux >/dev/null || { echo "tmux 없음: sudo apt install -y tmux"; exit 1; }
tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -c "$DIR" -n bringup
tmux send-keys -t "$SESSION:bringup" "source '$ROS_SETUP'; export ROS_DOMAIN_ID=$ROS_DOMAIN_ID; $BRINGUP_CMD" C-m

tmux new-window -t "$SESSION" -c "$DIR" -n camera
tmux send-keys -t "$SESSION:camera" "python3 scripts/camera_sender.py --host $AI_IP --port $VIDEO_PORT $CAM_ARGS" C-m

tmux new-window -t "$SESSION" -c "$DIR" -n cmd
tmux send-keys -t "$SESSION:cmd" "source '$ROS_SETUP'; export ROS_DOMAIN_ID=$ROS_DOMAIN_ID; python3 scripts/cmd_bridge.py --port $CMD_PORT $CMD_ARGS" C-m

tmux select-window -t "$SESSION:bringup"
tmux attach -t "$SESSION"
