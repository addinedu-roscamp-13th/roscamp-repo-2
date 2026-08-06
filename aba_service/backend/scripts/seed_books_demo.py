"""데모용 도서 카탈로그 보강 — 카테고리당 50권이 되도록 `cb_books` 를 채운다.

    cd aba_service/backend
    .venv/bin/python scripts/seed_books_demo.py            # 채우기
    .venv/bin/python scripts/seed_books_demo.py --dry-run  # 뭐가 들어갈지만 본다

## `seed_books.py` 와 무엇이 다른가

그쪽은 **테이블을 DROP 하고 다시 만든다.** 여기는 **지우지 않는다** — 사서가 화면에서
입력한 값(특히 `shelf_tier`/`shelf_row`)이 그 안에 있어서다. 같은 제목이 이미 있으면
건너뛰므로 여러 번 돌려도 안전하다.

## ⚠️ 이 데이터가 어디까지 진짜인가

- **제목·저자**: 실제 존재하는 책이다. 한국어/영어 제목은 통용되는 표기다.
- **중국어·베트남어 제목**: 널리 번역된 고전은 실제 출간 제목이지만, 그렇지 않은 책은
  **내가 옮긴 것**이라 현지 출간본과 다를 수 있다. 데모 표시용이다.
- **요약·태그**: 한국어만 채운다(사용자 결정 2026-08-07). 다른 언어로 화면을 바꾸면
  목록·검색은 정상이지만 **상세 설명이 빈칸**이다 — `books.py` 가 `or ""` 로 내보낸다.
- **`shelf_tier`/`shelf_row`**: **0(정보 없음)** 으로 넣는다. 팔이 실제로 손을 뻗는
  좌표라 지어내면 안 된다 — 0 이면 시각으로 찾는다(`models.Book` 의 그 필드 주석).
  사서가 화면에서 입력하면 그때 채워진다.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Book

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_books.json")

#: 카테고리 → (구역, 색 팔레트). 구역 이름은 `waypoint.yaml` 정점과 **같아야** 한다 —
#: 지어내면 탭했을 때 나가는 도서 조회·요청이 실제 로봇 목적지와 어긋난다.
#: (`LibraryMap.tsx` 의 `waypoints` 와 같은 문자열이다.)
ZONE = {
    "literature": "문학서가",
    "art": "예술서가",
    "science": "과학-인문학서가",
    "humanities": "과학-인문학서가",
}
PALETTE = {
    "literature": ["from-rose-200 to-rose-300", "from-rose-200 to-pink-300",
                   "from-orange-200 to-red-300"],
    "art": ["from-amber-200 to-orange-300", "from-yellow-200 to-amber-300",
            "from-orange-200 to-red-300"],
    "science": ["from-sky-200 to-blue-300", "from-blue-200 to-indigo-300",
                "from-emerald-200 to-teal-300"],
    "humanities": ["from-indigo-300 to-purple-400", "from-slate-300 to-zinc-400",
                   "from-emerald-200 to-teal-300"],
}
SHELF = ["첫째 줄", "둘째 줄", "셋째 줄"]

TARGET_PER_CATEGORY = 50


def build(entry: dict, category: str, i: int) -> dict:
    """JSON 한 줄 → `cb_books` 행. 색·선반은 순번에서 돌려 쓴다(데이터에 안 적는다)."""
    return dict(
        title_kr=entry["kr"], title_en=entry["en"],
        title_zh=entry["zh"], title_vi=entry["vi"],
        author=entry["author"], category=category,
        cover=entry["cover"], color=PALETTE[category][i % len(PALETTE[category])],
        zone=ZONE[category], shelf=SHELF[i % len(SHELF)],
        tier=0, row=0,                      # 지어내지 않는다 — 머리말 참고
        in_stock=entry.get("in_stock", True),
        summary_kr=entry["summary"],
        summary_en=None, summary_zh=None, summary_vi=None,
        for_whom_kr=json.dumps(entry["tags"], ensure_ascii=False),
        for_whom_en=None, for_whom_zh=None, for_whom_vi=None,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="넣지 않고 계획만 출력")
    args = ap.parse_args()

    with open(DATA, encoding="utf-8") as f:
        catalog = json.load(f)

    s = SessionLocal()
    try:
        have = {t for (t,) in s.query(Book.title_kr).all()}
        counts = dict(s.query(Book.category, func.count(Book.id))
                      .group_by(Book.category).all())
        added = 0
        for category, entries in catalog.items():
            cur = counts.get(category, 0)
            need = TARGET_PER_CATEGORY - cur
            if need <= 0:
                print(f"[{category}] 이미 {cur}권 — 건너뜀")
                continue
            fresh = [e for e in entries if e["kr"] not in have]
            if len(fresh) < need:
                print(f"[{category}] ⚠️ 후보 {len(fresh)}권뿐인데 {need}권이 필요하다 "
                      f"— 있는 만큼만 넣는다")
            take = fresh[:need]
            for i, e in enumerate(take):
                have.add(e["kr"])
                if not args.dry_run:
                    s.add(Book(**build(e, category, cur + i)))
            added += len(take)
            print(f"[{category}] {cur} → {cur + len(take)}권 (+{len(take)})")
        if args.dry_run:
            print(f"\n(dry-run) 총 {added}권을 넣을 예정")
            return 0
        s.commit()
        print(f"\n총 {added}권 추가 완료")
        for cat, n in sorted(s.query(Book.category, func.count(Book.id))
                             .group_by(Book.category).all()):
            print(f"  {cat}: {n}")
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
