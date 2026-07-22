#!/usr/bin/env bash
# 3단계 — 벽까지 직선 접근만. 초음파 거리로 멈춘다.
# 라인 추종 전체가 아니라 마지막 정지 동작만 떼어 본다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
echo "[주차 3/3] 벽까지 직선 접근 (초음파)"
dock_post "park/wall_approach"
