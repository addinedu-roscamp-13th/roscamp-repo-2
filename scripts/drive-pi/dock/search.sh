#!/usr/bin/env bash
# 테이프를 못 찾았을 때 — 제자리에서 좌우로 훑어 테이프를 다시 잡는다.
# 전체 절차 안에서는 "이미 테이프 위면 생략" 으로 들어간다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
echo "[주차] 테이프 탐색"
dock_post "park/search_line"
