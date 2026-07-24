#!/usr/bin/env bash
# DB 시드 — 대화형 메뉴. aba_service(cb_*) 대상.
#
#   ./seed.sh
#
# 회원/관리자 시드는 멱등(이미 있으면 skip) — 여러 번 돌려도 안전.
# 도서 카탈로그 리셋은 DROP TABLE 방식이라, cb_loans/cb_delivery_requests 가
# book_id FK로 실 데이터를 물고 있으면 MariaDB가 DROP 자체를 막아 에러로 실패한다
# (조용히 안 지워짐). 필요해지면 seed_books.py 를 FK-aware 하게 고치자(지금은 보류).
set -eo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../_common.sh"

LIB="$REPO_ROOT/aba_service/backend"

confirm() {   # <문구> — y 아니면 스킵
  local ans
  read -r -p "$1 [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

run_lib() {   # <scripts/ 아래 파일명>
  ensure_venv "$LIB"
  ( cd "$LIB" && .venv/bin/python "scripts/$1" )
}

do_1() { confirm "[aba_service] 회원 추가시드 (member1~5, 안전·멱등) 실행?" && run_lib seed_members.py; }
do_2() { confirm "[aba_service] 관리자 계정 시드 (안전·있으면 skip) 실행?" && run_lib seed_admin.py; }
do_3() { confirm "[aba_service] 도서 카탈로그 리셋 ⚠ DROP 후 재생성 — 정말 실행?" && run_lib seed_books.py; }
do_4() { confirm "[aba_service] 데모용 허구 데이터 대량 생성 (대출/요청/예약 ~30일치, 멱등 아님·누를 때마다 더 쌓임) 실행?" && run_lib seed_demo_data.py; }

cat <<'EOF'
=== LiBi DB 시드 메뉴 ===
1) aba_service: 회원 추가시드
2) aba_service: 관리자 계정 시드
3) aba_service: 도서 카탈로그 리셋 (⚠ 삭제 후 재생성)
4) aba_service: 데모용 허구 데이터 대량 생성 (대시보드용, ~30일치)
EOF
read -r -p "번호 선택 (공백으로 여러 개, 예: 1 2 3): " -a choices

for c in "${choices[@]}"; do
  case "$c" in
    1|2|3|4) "do_$c" ;;
    *) echo "[skip] 알 수 없는 번호: $c" ;;
  esac
done
