#!/usr/bin/env bash
# 지금 주차가 어디까지 갔나. 아무것도 움직이지 않는다.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
curl -s -m 10 "$BASE/park/status"; echo
