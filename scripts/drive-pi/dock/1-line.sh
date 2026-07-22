#!/usr/bin/env bash
# 1단계 — 바닥 테이프가 보이나. **로봇은 움직이지 않는다.**
# 주차의 본체가 라인 추종이므로, 여기서 라인이 안 잡히면 뒤 단계는 볼 필요가 없다.
# (조명·테이프 대비·카메라 각도부터 본다)
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
echo "[주차 1/3] 테이프 감지 — IR 센서 값과 라인 판정"
curl -s -m 20 "$BASE/line/detect"; echo
