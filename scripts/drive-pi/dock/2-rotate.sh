#!/usr/bin/env bash
# 2단계 — 주차장 방향으로 제자리 회전만. 접근은 안 한다.
# 기본은 odom(TF yaw). 각도가 안 맞으면 DOCK_APPROACH_YAW_DEG 로 바꿔 가며 본다.
# 마커로 각도를 잡고 싶으면: DOCK_MARKER_ID=7 ./2-rotate.sh
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
echo "[주차 2/3] ${APPROACH_YAW}° 로 제자리 회전 (기준=${MARKER_ID:+marker}${MARKER_ID:-$ROTATE_REF})"
dock_post "park/rotate"
