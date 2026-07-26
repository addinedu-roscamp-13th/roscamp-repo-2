"""Drop and re-seed the `cb_books` table with a curated catalog across four
fields: 문학(literature) · 예술(art) · 과학(science) · 인문학(humanities).

Run from chatbot/backend:  .venv/bin/python scripts.bak/seed_books.py
WARNING: this DROPs the existing `cb_books` table and recreates it.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.models import Book  # noqa: F401  (ensures the table is registered)


def B(title, author, category, cover, color, zone, shelf, in_stock, summary, tags):
    """Build a Book row dict from per-language dicts."""
    return dict(
        title_kr=title["KR"], title_en=title["EN"], title_zh=title["ZH"], title_vi=title["VI"],
        author=author, category=category, cover=cover, color=color, zone=zone, shelf=shelf,
        in_stock=in_stock,
        summary_kr=summary["KR"], summary_en=summary["EN"], summary_zh=summary["ZH"], summary_vi=summary["VI"],
        for_whom_kr=json.dumps(tags["KR"], ensure_ascii=False),
        for_whom_en=json.dumps(tags["EN"], ensure_ascii=False),
        for_whom_zh=json.dumps(tags["ZH"], ensure_ascii=False),
        for_whom_vi=json.dumps(tags["VI"], ensure_ascii=False),
    )


BOOKS = [
    # ───────────────────────── 문학 (literature) · 문학서가 ─────────────────────────
    B({"KR": "데미안", "EN": "Demian", "ZH": "德米安", "VI": "Demian"}, "헤르만 헤세",
      "literature", "🕊️", "from-rose-200 to-rose-300", "문학서가", "셋째 줄", True,
      {"KR": "자아를 찾아가는 청년 싱클레어의 내면 성장을 그린 성장소설.",
       "EN": "A coming-of-age story of Sinclair's search for his true self.",
       "ZH": "描写辛克莱寻找自我的成长小说。",
       "VI": "Tiểu thuyết trưởng thành về hành trình tìm kiếm bản ngã."},
      {"KR": ["#성장소설", "#자아탐구", "#헤르만헤세"], "EN": ["#coming-of-age", "#self", "#classic"],
       "ZH": ["#成长", "#自我", "#经典"], "VI": ["#trưởng-thành", "#bản-ngã", "#kinh-điển"]}),

    B({"KR": "노인과 바다", "EN": "The Old Man and the Sea", "ZH": "老人与海", "VI": "Ông già và biển cả"}, "어니스트 헤밍웨이",
      "literature", "🐟", "from-sky-200 to-blue-300", "문학서가", "셋째 줄", True,
      {"KR": "거대한 청새치와 싸우는 노어부의 불굴의 의지를 그린 중편.",
       "EN": "An old fisherman's unyielding struggle with a giant marlin.",
       "ZH": "老渔夫与大马林鱼搏斗的不屈意志。",
       "VI": "Ý chí kiên cường của ông lão đánh cá với con cá kiếm khổng lồ."},
      {"KR": ["#고전", "#불굴의의지", "#헤밍웨이"], "EN": ["#classic", "#perseverance", "#nobel"],
       "ZH": ["#经典", "#毅力", "#诺贝尔"], "VI": ["#kinh-điển", "#kiên-cường", "#nobel"]}),

    B({"KR": "어린 왕자", "EN": "The Little Prince", "ZH": "小王子", "VI": "Hoàng tử bé"}, "생텍쥐페리",
      "literature", "🌹", "from-yellow-200 to-amber-300", "문학서가", "셋째 줄", True,
      {"KR": "어른들이 잃어버린 순수와 사랑을 일깨우는 우화.",
       "EN": "A fable reawakening the innocence and love adults forget.",
       "ZH": "唤醒大人遗失的纯真与爱的寓言。",
       "VI": "Câu chuyện ngụ ngôn đánh thức sự trong sáng và tình yêu."},
      {"KR": ["#우화", "#순수", "#필독서"], "EN": ["#fable", "#innocence", "#must-read"],
       "ZH": ["#寓言", "#纯真", "#必读"], "VI": ["#ngụ-ngôn", "#trong-sáng", "#nên-đọc"]}),

    # ───────────────────────── 예술 (art) · 예술서가 ─────────────────────────
    B({"KR": "서양미술사", "EN": "The Story of Art", "ZH": "艺术的故事", "VI": "Câu chuyện nghệ thuật"}, "E.H. 곰브리치",
      "art", "🖼️", "from-amber-200 to-orange-300", "예술서가", "셋째 줄", True,
      {"KR": "선사시대부터 현대까지 미술의 흐름을 한 권에 담은 명저.",
       "EN": "The definitive one-volume survey of art from cave to modern.",
       "ZH": "从史前到现代的艺术通史名著。",
       "VI": "Cuốn sách kinh điển về lịch sử nghệ thuật từ cổ đến hiện đại."},
      {"KR": ["#미술사", "#입문서", "#곰브리치"], "EN": ["#art-history", "#intro", "#classic"],
       "ZH": ["#美术史", "#入门", "#经典"], "VI": ["#lịch-sử-mỹ-thuật", "#nhập-môn", "#kinh-điển"]}),

    B({"KR": "다른 방식으로 보기", "EN": "Ways of Seeing", "ZH": "观看之道", "VI": "Các phương cách nhìn"}, "존 버거",
      "art", "👁️", "from-rose-200 to-pink-300", "예술서가", "셋째 줄", True,
      {"KR": "이미지를 보는 관습을 비판적으로 해부한 미술 비평의 고전.",
       "EN": "A classic critique of how we are taught to see images.",
       "ZH": "批判性解析观看习惯的艺术评论经典。",
       "VI": "Phê bình kinh điển về cách chúng ta được dạy để nhìn."},
      {"KR": ["#미술비평", "#시각문화", "#존버거"], "EN": ["#art-criticism", "#visual-culture", "#essay"],
       "ZH": ["#艺术评论", "#视觉文化", "#约翰伯格"], "VI": ["#phê-bình", "#văn-hóa-thị-giác", "#tiểu-luận"]}),

    B({"KR": "반 고흐, 영혼의 편지", "EN": "The Letters of Vincent van Gogh", "ZH": "梵高手稿", "VI": "Những lá thư của Van Gogh"}, "빈센트 반 고흐",
      "art", "🌻", "from-yellow-200 to-amber-300", "예술서가", "셋째 줄", True,
      {"KR": "동생 테오에게 보낸 편지로 읽는 고흐의 예술과 고독.",
       "EN": "Van Gogh's art and solitude through letters to his brother Theo.",
       "ZH": "通过写给弟弟提奥的书信读懂梵高的艺术与孤独。",
       "VI": "Nghệ thuật và nỗi cô đơn của Van Gogh qua thư gửi em trai."},
      {"KR": ["#화가의편지", "#반고흐", "#예술혼"], "EN": ["#letters", "#van-gogh", "#painter"],
       "ZH": ["#书信", "#梵高", "#画家"], "VI": ["#thư-từ", "#van-gogh", "#họa-sĩ"]}),

    # ───────────────────────── 과학 (science) · 과학-인문학서가 ─────────────────────────
    B({"KR": "코스모스", "EN": "Cosmos", "ZH": "宇宙", "VI": "Vũ trụ"}, "칼 세이건",
      "science", "🌌", "from-indigo-300 to-purple-400", "과학-인문학서가", "셋째 줄", True,
      {"KR": "우주와 생명, 과학의 경이를 시적으로 풀어낸 과학 교양의 고전.",
       "EN": "A poetic classic on the cosmos, life and the wonder of science.",
       "ZH": "诗意讲述宇宙、生命与科学之美的经典。",
       "VI": "Tác phẩm kinh điển đầy chất thơ về vũ trụ và khoa học."},
      {"KR": ["#천문학", "#과학교양", "#칼세이건"], "EN": ["#astronomy", "#popular-science", "#sagan"],
       "ZH": ["#天文学", "#科普", "#萨根"], "VI": ["#thiên-văn", "#khoa-học", "#sagan"]}),

    B({"KR": "이기적 유전자", "EN": "The Selfish Gene", "ZH": "自私的基因", "VI": "Gen vị kỷ"}, "리처드 도킨스",
      "science", "🧬", "from-emerald-200 to-teal-300", "과학-인문학서가", "셋째 줄", True,
      {"KR": "유전자의 관점에서 진화와 이타성을 재해석한 명저.",
       "EN": "Evolution and altruism seen from the gene's point of view.",
       "ZH": "从基因视角重新诠释进化与利他的名著。",
       "VI": "Tiến hóa và lòng vị tha nhìn từ góc độ của gen."},
      {"KR": ["#진화생물학", "#유전자", "#도킨스"], "EN": ["#evolution", "#biology", "#dawkins"],
       "ZH": ["#进化论", "#生物学", "#道金斯"], "VI": ["#tiến-hóa", "#sinh-học", "#dawkins"]}),

    B({"KR": "시간의 역사", "EN": "A Brief History of Time", "ZH": "时间简史", "VI": "Lược sử thời gian"}, "스티븐 호킹",
      "science", "⏳", "from-slate-300 to-zinc-400", "과학-인문학서가", "셋째 줄", True,
      {"KR": "빅뱅과 블랙홀, 시간의 비밀을 대중에게 전한 우주론 입문서.",
       "EN": "A popular introduction to the Big Bang, black holes and time.",
       "ZH": "向大众讲述大爆炸、黑洞与时间的宇宙学入门。",
       "VI": "Dẫn nhập đại chúng về Big Bang, hố đen và thời gian."},
      {"KR": ["#우주론", "#물리학", "#호킹"], "EN": ["#cosmology", "#physics", "#hawking"],
       "ZH": ["#宇宙学", "#物理", "#霍金"], "VI": ["#vũ-trụ-học", "#vật-lý", "#hawking"]}),

    # ───────────────────────── 인문학 (humanities) · 과학-인문학서가 ─────────────────────────
    B({"KR": "사피엔스", "EN": "Sapiens", "ZH": "人类简史", "VI": "Sapiens: Lược sử loài người"}, "유발 하라리",
      "humanities", "🧠", "from-orange-200 to-red-300", "과학-인문학서가", "셋째 줄", True,
      {"KR": "인지혁명부터 현재까지 인류의 역사를 통찰한 베스트셀러.",
       "EN": "A bestselling sweep of human history from cognition to now.",
       "ZH": "从认知革命到当下的人类历史畅销书。",
       "VI": "Cuốn sách bán chạy về lịch sử loài người từ cách mạng nhận thức."},
      {"KR": ["#빅히스토리", "#인류학", "#하라리"], "EN": ["#big-history", "#anthropology", "#harari"],
       "ZH": ["#大历史", "#人类学", "#赫拉利"], "VI": ["#đại-lịch-sử", "#nhân-học", "#harari"]}),

    B({"KR": "총, 균, 쇠", "EN": "Guns, Germs, and Steel", "ZH": "枪炮、病菌与钢铁", "VI": "Súng, vi trùng và thép"}, "재레드 다이아몬드",
      "humanities", "🌍", "from-amber-200 to-orange-300", "과학-인문학서가", "셋째 줄", True,
      {"KR": "문명의 불평등이 어디서 비롯됐는지 지리와 환경으로 풀어낸 역작.",
       "EN": "Why civilizations diverged, explained through geography and environment.",
       "ZH": "从地理与环境解释文明差异之源的巨著。",
       "VI": "Lý giải sự khác biệt của các nền văn minh qua địa lý và môi trường."},
      {"KR": ["#문명사", "#인류학", "#다이아몬드"], "EN": ["#big-history", "#anthropology", "#diamond"],
       "ZH": ["#文明史", "#人类学", "#戴蒙德"], "VI": ["#lịch-sử-văn-minh", "#nhân-học", "#diamond"]}),

    B({"KR": "정의란 무엇인가", "EN": "Justice: What's the Right Thing to Do?", "ZH": "公正：该如何做是好", "VI": "Phải trái đúng sai"}, "마이클 샌델",
      "humanities", "⚖️", "from-blue-200 to-indigo-300", "과학-인문학서가", "셋째 줄", True,
      {"KR": "정의·자유·공동선을 둘러싼 철학적 딜레마를 대중 강의로 풀어낸 베스트셀러.",
       "EN": "A bestseller unpacking philosophical dilemmas of justice, freedom and the common good.",
       "ZH": "以通俗讲座解读正义、自由与公共善之哲学难题的畅销书。",
       "VI": "Cuốn sách bán chạy giải thích các nghịch lý triết học về công lý, tự do và lợi ích chung."},
      {"KR": ["#정치철학", "#윤리학", "#샌델"], "EN": ["#political-philosophy", "#ethics", "#sandel"],
       "ZH": ["#政治哲学", "#伦理学", "#桑德尔"], "VI": ["#triết-học-chính-trị", "#đạo-đức", "#sandel"]}),
]


def main() -> None:
    with engine.begin() as conn:
        # cb_loans/cb_requests 등이 cb_books 를 FK 로 참조하므로, 행이 0개라도
        # FK 제약 자체가 남아있으면 DROP TABLE 이 거부된다 — 재생성 동안만 잠깐 끈다.
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        conn.execute(text("DROP TABLE IF EXISTS cb_books"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    Book.__table__.create(bind=engine)

    db = SessionLocal()
    try:
        db.bulk_insert_mappings(Book, BOOKS)
        db.commit()
        by_cat: dict[str, int] = {}
        for b in BOOKS:
            by_cat[b["category"]] = by_cat.get(b["category"], 0) + 1
        print(f"[seed_books] inserted {len(BOOKS)} books")
        for cat, n in sorted(by_cat.items()):
            print(f"  {cat:<12} {n}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
