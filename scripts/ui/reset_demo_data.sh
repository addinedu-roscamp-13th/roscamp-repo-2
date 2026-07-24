#!/usr/bin/env bash
# 대출·요청·예약 이력 + 데모 도서 전부 삭제. aba_service(cb_*) 대상.
#
#   ./reset_demo_data.sh
#
# ⚠️ cb_loans/cb_delivery_requests/cb_reservations 를 통째로 비운다. seed_demo_data.py 가
# 만든 가짜 이력과 실제 이력을 구분할 표시가 없어서, 진짜 대출/승인 이력(예: member1 의
# 데미안 대출)도 같이 지워진다. 회원 계정·관리자·진짜 도서 카탈로그(15권)는 안 건드린다.
# 자세한 삭제 범위는 aba_service/backend/scripts/reset_demo_data.py 참고.
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

LIB="$REPO_ROOT/aba_service/backend"

echo "=== 대출/요청/예약 이력 + 데모 도서 삭제 ==="
echo "⚠️  실제 대출/승인 이력도 함께 지워집니다 (구분할 표시가 없음). 되돌릴 수 없습니다."
read -r -p "정말 삭제할까요? [y/N] " ans
if [[ ! "$ans" =~ ^[Yy]$ ]]; then
  echo "취소했습니다."
  exit 0
fi

ensure_venv "$LIB"
( cd "$LIB" && .venv/bin/python scripts/reset_demo_data.py )
