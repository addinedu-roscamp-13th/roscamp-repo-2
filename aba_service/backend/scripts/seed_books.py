"""Drop and re-seed the `cb_books` table with a curated catalog focused on three
fields: 문학(literature) · 예술(art) · 과학(science).

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
    # ───────────────────────── 문학 (literature) · zone A ─────────────────────────
    B({"KR": "데미안", "EN": "Demian", "ZH": "德米安", "VI": "Demian"}, "헤르만 헤세",
      "literature", "🕊️", "from-rose-200 to-rose-300", "A-1", "첫째 줄", True,
      {"KR": "자아를 찾아가는 청년 싱클레어의 내면 성장을 그린 성장소설.",
       "EN": "A coming-of-age story of Sinclair's search for his true self.",
       "ZH": "描写辛克莱寻找自我的成长小说。",
       "VI": "Tiểu thuyết trưởng thành về hành trình tìm kiếm bản ngã."},
      {"KR": ["#성장소설", "#자아탐구", "#헤르만헤세"], "EN": ["#coming-of-age", "#self", "#classic"],
       "ZH": ["#成长", "#自我", "#经典"], "VI": ["#trưởng-thành", "#bản-ngã", "#kinh-điển"]}),

    B({"KR": "1984", "EN": "1984", "ZH": "一九八四", "VI": "1984"}, "조지 오웰",
      "literature", "🌃", "from-slate-200 to-slate-300", "A-1", "둘째 줄", True,
      {"KR": "전체주의 감시 사회의 공포를 그린 디스토피아 고전.",
       "EN": "A dystopian classic on totalitarian surveillance.",
       "ZH": "描写极权监控社会的反乌托邦经典。",
       "VI": "Tác phẩm phản địa đàng kinh điển về xã hội giám sát."},
      {"KR": ["#디스토피아", "#고전", "#조지오웰"], "EN": ["#dystopia", "#classic", "#politics"],
       "ZH": ["#反乌托邦", "#经典", "#政治"], "VI": ["#phản-địa-đàng", "#kinh-điển", "#chính-trị"]}),

    B({"KR": "노인과 바다", "EN": "The Old Man and the Sea", "ZH": "老人与海", "VI": "Ông già và biển cả"}, "어니스트 헤밍웨이",
      "literature", "🐟", "from-sky-200 to-blue-300", "A-2", "첫째 줄", True,
      {"KR": "거대한 청새치와 싸우는 노어부의 불굴의 의지를 그린 중편.",
       "EN": "An old fisherman's unyielding struggle with a giant marlin.",
       "ZH": "老渔夫与大马林鱼搏斗的不屈意志。",
       "VI": "Ý chí kiên cường của ông lão đánh cá với con cá kiếm khổng lồ."},
      {"KR": ["#고전", "#불굴의의지", "#헤밍웨이"], "EN": ["#classic", "#perseverance", "#nobel"],
       "ZH": ["#经典", "#毅力", "#诺贝尔"], "VI": ["#kinh-điển", "#kiên-cường", "#nobel"]}),

    B({"KR": "위대한 개츠비", "EN": "The Great Gatsby", "ZH": "了不起的盖茨比", "VI": "Đại gia Gatsby"}, "F. 스콧 피츠제럴드",
      "literature", "🥂", "from-amber-200 to-yellow-300", "A-2", "둘째 줄", False,
      {"KR": "재즈 시대 아메리칸 드림의 빛과 허무를 그린 명작.",
       "EN": "The dazzle and emptiness of the Jazz Age American Dream.",
       "ZH": "爵士时代美国梦的华丽与虚无。",
       "VI": "Sự hào nhoáng và trống rỗng của giấc mơ Mỹ thời Jazz."},
      {"KR": ["#고전", "#아메리칸드림", "#재즈시대"], "EN": ["#classic", "#american-dream", "#jazz-age"],
       "ZH": ["#经典", "#美国梦", "#爵士时代"], "VI": ["#kinh-điển", "#giấc-mơ-mỹ", "#jazz"]}),

    B({"KR": "어린 왕자", "EN": "The Little Prince", "ZH": "小王子", "VI": "Hoàng tử bé"}, "생텍쥐페리",
      "literature", "🌹", "from-yellow-200 to-amber-300", "A-3", "첫째 줄", True,
      {"KR": "어른들이 잃어버린 순수와 사랑을 일깨우는 우화.",
       "EN": "A fable reawakening the innocence and love adults forget.",
       "ZH": "唤醒大人遗失的纯真与爱的寓言。",
       "VI": "Câu chuyện ngụ ngôn đánh thức sự trong sáng và tình yêu."},
      {"KR": ["#우화", "#순수", "#필독서"], "EN": ["#fable", "#innocence", "#must-read"],
       "ZH": ["#寓言", "#纯真", "#必读"], "VI": ["#ngụ-ngôn", "#trong-sáng", "#nên-đọc"]}),

    B({"KR": "죄와 벌", "EN": "Crime and Punishment", "ZH": "罪与罚", "VI": "Tội ác và trừng phạt"}, "도스토옙스키",
      "literature", "🪓", "from-stone-200 to-stone-300", "A-3", "둘째 줄", True,
      {"KR": "살인을 저지른 청년의 죄의식과 구원을 파고든 심리 대작.",
       "EN": "A psychological masterpiece on guilt and redemption.",
       "ZH": "剖析罪与救赎的心理巨著。",
       "VI": "Kiệt tác tâm lý về tội lỗi và sự cứu rỗi."},
      {"KR": ["#러시아문학", "#심리소설", "#고전"], "EN": ["#russian-lit", "#psychological", "#classic"],
       "ZH": ["#俄国文学", "#心理小说", "#经典"], "VI": ["#văn-học-nga", "#tâm-lý", "#kinh-điển"]}),

    B({"KR": "변신", "EN": "The Metamorphosis", "ZH": "变形记", "VI": "Hóa thân"}, "프란츠 카프카",
      "literature", "🪲", "from-lime-200 to-emerald-300", "A-4", "첫째 줄", True,
      {"KR": "벌레로 변한 남자를 통해 소외와 부조리를 그린 단편.",
       "EN": "Alienation and absurdity through a man turned into an insect.",
       "ZH": "通过变成虫子的人描写疏离与荒诞。",
       "VI": "Sự tha hóa và phi lý qua người đàn ông hóa thành côn trùng."},
      {"KR": ["#실존주의", "#부조리", "#카프카"], "EN": ["#existential", "#absurd", "#kafka"],
       "ZH": ["#存在主义", "#荒诞", "#卡夫卡"], "VI": ["#hiện-sinh", "#phi-lý", "#kafka"]}),

    B({"KR": "햄릿", "EN": "Hamlet", "ZH": "哈姆雷特", "VI": "Hamlet"}, "윌리엄 셰익스피어",
      "literature", "💀", "from-indigo-200 to-slate-300", "A-4", "둘째 줄", True,
      {"KR": "복수와 존재를 고뇌하는 덴마크 왕자의 비극.",
       "EN": "The tragedy of a Danish prince torn by revenge and being.",
       "ZH": "为复仇与存在而苦恼的丹麦王子的悲剧。",
       "VI": "Bi kịch của hoàng tử Đan Mạch giằng xé giữa báo thù và tồn tại."},
      {"KR": ["#희곡", "#비극", "#셰익스피어"], "EN": ["#drama", "#tragedy", "#shakespeare"],
       "ZH": ["#戏剧", "#悲剧", "#莎士比亚"], "VI": ["#kịch", "#bi-kịch", "#shakespeare"]}),

    # ───────────────────────── 예술 (art) · zone B ─────────────────────────
    B({"KR": "서양미술사", "EN": "The Story of Art", "ZH": "艺术的故事", "VI": "Câu chuyện nghệ thuật"}, "E.H. 곰브리치",
      "art", "🖼️", "from-amber-200 to-orange-300", "B-1", "첫째 줄", True,
      {"KR": "선사시대부터 현대까지 미술의 흐름을 한 권에 담은 명저.",
       "EN": "The definitive one-volume survey of art from cave to modern.",
       "ZH": "从史前到现代的艺术通史名著。",
       "VI": "Cuốn sách kinh điển về lịch sử nghệ thuật từ cổ đến hiện đại."},
      {"KR": ["#미술사", "#입문서", "#곰브리치"], "EN": ["#art-history", "#intro", "#classic"],
       "ZH": ["#美术史", "#入门", "#经典"], "VI": ["#lịch-sử-mỹ-thuật", "#nhập-môn", "#kinh-điển"]}),

    B({"KR": "다른 방식으로 보기", "EN": "Ways of Seeing", "ZH": "观看之道", "VI": "Các phương cách nhìn"}, "존 버거",
      "art", "👁️", "from-rose-200 to-pink-300", "B-1", "둘째 줄", True,
      {"KR": "이미지를 보는 관습을 비판적으로 해부한 미술 비평의 고전.",
       "EN": "A classic critique of how we are taught to see images.",
       "ZH": "批判性解析观看习惯的艺术评论经典。",
       "VI": "Phê bình kinh điển về cách chúng ta được dạy để nhìn."},
      {"KR": ["#미술비평", "#시각문화", "#존버거"], "EN": ["#art-criticism", "#visual-culture", "#essay"],
       "ZH": ["#艺术评论", "#视觉文化", "#约翰伯格"], "VI": ["#phê-bình", "#văn-hóa-thị-giác", "#tiểu-luận"]}),

    B({"KR": "반 고흐, 영혼의 편지", "EN": "The Letters of Vincent van Gogh", "ZH": "梵高手稿", "VI": "Những lá thư của Van Gogh"}, "빈센트 반 고흐",
      "art", "🌻", "from-yellow-200 to-amber-300", "B-2", "첫째 줄", True,
      {"KR": "동생 테오에게 보낸 편지로 읽는 고흐의 예술과 고독.",
       "EN": "Van Gogh's art and solitude through letters to his brother Theo.",
       "ZH": "通过写给弟弟提奥的书信读懂梵高的艺术与孤独。",
       "VI": "Nghệ thuật và nỗi cô đơn của Van Gogh qua thư gửi em trai."},
      {"KR": ["#화가의편지", "#반고흐", "#예술혼"], "EN": ["#letters", "#van-gogh", "#painter"],
       "ZH": ["#书信", "#梵高", "#画家"], "VI": ["#thư-từ", "#van-gogh", "#họa-sĩ"]}),

    B({"KR": "카메라 루시다", "EN": "Camera Lucida", "ZH": "明室", "VI": "Buồng sáng"}, "롤랑 바르트",
      "art", "📷", "from-zinc-200 to-slate-300", "B-2", "둘째 줄", False,
      {"KR": "사진의 본질과 기억을 사유한 바르트의 사진론.",
       "EN": "Barthes' meditation on photography, memory and loss.",
       "ZH": "巴特关于摄影、记忆与死亡的沉思。",
       "VI": "Suy tư của Barthes về nhiếp ảnh, ký ức và mất mát."},
      {"KR": ["#사진론", "#롤랑바르트", "#미학"], "EN": ["#photography", "#theory", "#barthes"],
       "ZH": ["#摄影论", "#巴特", "#美学"], "VI": ["#nhiếp-ảnh", "#lý-thuyết", "#mỹ-học"]}),

    B({"KR": "디자인의 디자인", "EN": "Designing Design", "ZH": "设计中的设计", "VI": "Thiết kế của thiết kế"}, "하라 켄야",
      "art", "✏️", "from-neutral-200 to-stone-300", "B-3", "첫째 줄", True,
      {"KR": "무인양품 아트디렉터가 말하는 비움과 디자인 철학.",
       "EN": "The emptiness-driven design philosophy of MUJI's art director.",
       "ZH": "无印良品艺术总监讲述的留白与设计哲学。",
       "VI": "Triết lý thiết kế tối giản của giám đốc nghệ thuật MUJI."},
      {"KR": ["#디자인", "#미니멀", "#하라켄야"], "EN": ["#design", "#minimal", "#muji"],
       "ZH": ["#设计", "#极简", "#原研哉"], "VI": ["#thiết-kế", "#tối-giản", "#muji"]}),

    B({"KR": "색채의 예술", "EN": "The Art of Color", "ZH": "色彩艺术", "VI": "Nghệ thuật màu sắc"}, "요하네스 이텐",
      "art", "🌈", "from-fuchsia-200 to-purple-300", "B-3", "둘째 줄", True,
      {"KR": "바우하우스 거장이 정리한 색채 이론의 고전.",
       "EN": "The Bauhaus master's classic theory of color.",
       "ZH": "包豪斯大师整理的色彩理论经典。",
       "VI": "Lý thuyết màu sắc kinh điển của bậc thầy Bauhaus."},
      {"KR": ["#색채이론", "#바우하우스", "#디자인"], "EN": ["#color-theory", "#bauhaus", "#design"],
       "ZH": ["#色彩理论", "#包豪斯", "#设计"], "VI": ["#lý-thuyết-màu", "#bauhaus", "#thiết-kế"]}),

    B({"KR": "미학 오디세이", "EN": "Aesthetics Odyssey", "ZH": "美学奥德赛", "VI": "Hành trình mỹ học"}, "진중권",
      "art", "🎭", "from-violet-200 to-indigo-300", "B-1", "셋째 줄", True,
      {"KR": "예술과 철학을 넘나들며 미학을 쉽게 풀어낸 교양서.",
       "EN": "An accessible journey through art and the philosophy of beauty.",
       "ZH": "穿梭于艺术与哲学之间的美学入门书。",
       "VI": "Hành trình dễ tiếp cận qua nghệ thuật và triết học cái đẹp."},
      {"KR": ["#미학", "#교양", "#예술철학"], "EN": ["#aesthetics", "#humanities", "#philosophy"],
       "ZH": ["#美学", "#通识", "#艺术哲学"], "VI": ["#mỹ-học", "#khai-phóng", "#triết-nghệ-thuật"]}),

    # ───────────────────────── 과학 (science) · zone C ─────────────────────────
    B({"KR": "코스모스", "EN": "Cosmos", "ZH": "宇宙", "VI": "Vũ trụ"}, "칼 세이건",
      "science", "🌌", "from-indigo-300 to-purple-400", "C-1", "첫째 줄", True,
      {"KR": "우주와 생명, 과학의 경이를 시적으로 풀어낸 과학 교양의 고전.",
       "EN": "A poetic classic on the cosmos, life and the wonder of science.",
       "ZH": "诗意讲述宇宙、生命与科学之美的经典。",
       "VI": "Tác phẩm kinh điển đầy chất thơ về vũ trụ và khoa học."},
      {"KR": ["#천문학", "#과학교양", "#칼세이건"], "EN": ["#astronomy", "#popular-science", "#sagan"],
       "ZH": ["#天文学", "#科普", "#萨根"], "VI": ["#thiên-văn", "#khoa-học", "#sagan"]}),

    B({"KR": "이기적 유전자", "EN": "The Selfish Gene", "ZH": "自私的基因", "VI": "Gen vị kỷ"}, "리처드 도킨스",
      "science", "🧬", "from-emerald-200 to-teal-300", "C-1", "둘째 줄", True,
      {"KR": "유전자의 관점에서 진화와 이타성을 재해석한 명저.",
       "EN": "Evolution and altruism seen from the gene's point of view.",
       "ZH": "从基因视角重新诠释进化与利他的名著。",
       "VI": "Tiến hóa và lòng vị tha nhìn từ góc độ của gen."},
      {"KR": ["#진화생물학", "#유전자", "#도킨스"], "EN": ["#evolution", "#biology", "#dawkins"],
       "ZH": ["#进化论", "#生物学", "#道金斯"], "VI": ["#tiến-hóa", "#sinh-học", "#dawkins"]}),

    B({"KR": "시간의 역사", "EN": "A Brief History of Time", "ZH": "时间简史", "VI": "Lược sử thời gian"}, "스티븐 호킹",
      "science", "⏳", "from-slate-300 to-zinc-400", "C-2", "첫째 줄", True,
      {"KR": "빅뱅과 블랙홀, 시간의 비밀을 대중에게 전한 우주론 입문서.",
       "EN": "A popular introduction to the Big Bang, black holes and time.",
       "ZH": "向大众讲述大爆炸、黑洞与时间的宇宙学入门。",
       "VI": "Dẫn nhập đại chúng về Big Bang, hố đen và thời gian."},
      {"KR": ["#우주론", "#물리학", "#호킹"], "EN": ["#cosmology", "#physics", "#hawking"],
       "ZH": ["#宇宙学", "#物理", "#霍金"], "VI": ["#vũ-trụ-học", "#vật-lý", "#hawking"]}),

    B({"KR": "종의 기원", "EN": "On the Origin of Species", "ZH": "物种起源", "VI": "Nguồn gốc các loài"}, "찰스 다윈",
      "science", "🐒", "from-amber-200 to-lime-300", "C-2", "둘째 줄", False,
      {"KR": "자연선택에 의한 진화를 정립한 과학사의 기념비.",
       "EN": "The landmark that established evolution by natural selection.",
       "ZH": "确立自然选择进化论的科学史里程碑。",
       "VI": "Cột mốc xác lập thuyết tiến hóa qua chọn lọc tự nhiên."},
      {"KR": ["#진화론", "#고전", "#다윈"], "EN": ["#evolution", "#classic", "#darwin"],
       "ZH": ["#进化论", "#经典", "#达尔文"], "VI": ["#tiến-hóa", "#kinh-điển", "#darwin"]}),

    B({"KR": "사피엔스", "EN": "Sapiens", "ZH": "人类简史", "VI": "Sapiens: Lược sử loài người"}, "유발 하라리",
      "science", "🧠", "from-orange-200 to-red-300", "C-3", "첫째 줄", True,
      {"KR": "인지혁명부터 현재까지 인류의 역사를 통찰한 베스트셀러.",
       "EN": "A bestselling sweep of human history from cognition to now.",
       "ZH": "从认知革命到当下的人类历史畅销书。",
       "VI": "Cuốn sách bán chạy về lịch sử loài người từ cách mạng nhận thức."},
      {"KR": ["#빅히스토리", "#인류학", "#하라리"], "EN": ["#big-history", "#anthropology", "#harari"],
       "ZH": ["#大历史", "#人类学", "#赫拉利"], "VI": ["#đại-lịch-sử", "#nhân-học", "#harari"]}),

    B({"KR": "페르마의 마지막 정리", "EN": "Fermat's Last Theorem", "ZH": "费马大定理", "VI": "Định lý cuối cùng của Fermat"}, "사이먼 싱",
      "science", "♾️", "from-sky-200 to-indigo-300", "C-3", "둘째 줄", True,
      {"KR": "350년 난제를 푼 수학자들의 드라마를 그린 교양서.",
       "EN": "The human drama behind solving a 350-year-old math puzzle.",
       "ZH": "讲述破解350年数学难题的人间戏剧。",
       "VI": "Câu chuyện kịch tính giải bài toán 350 năm tuổi."},
      {"KR": ["#수학", "#교양과학", "#사이먼싱"], "EN": ["#mathematics", "#popular-science", "#singh"],
       "ZH": ["#数学", "#科普", "#辛格"], "VI": ["#toán-học", "#khoa-học", "#singh"]}),

    B({"KR": "침묵의 봄", "EN": "Silent Spring", "ZH": "寂静的春天", "VI": "Mùa xuân vắng lặng"}, "레이첼 카슨",
      "science", "🌿", "from-green-200 to-emerald-300", "C-1", "셋째 줄", True,
      {"KR": "살충제의 위험을 고발해 환경운동을 일으킨 기념비적 저작.",
       "EN": "The landmark exposé on pesticides that sparked environmentalism.",
       "ZH": "揭露杀虫剂危害、引发环保运动的里程碑之作。",
       "VI": "Tác phẩm phơi bày hiểm họa thuốc trừ sâu, khơi nguồn phong trào môi trường."},
      {"KR": ["#환경", "#생태", "#레이첼카슨"], "EN": ["#environment", "#ecology", "#carson"],
       "ZH": ["#环境", "#生态", "#卡森"], "VI": ["#môi-trường", "#sinh-thái", "#carson"]}),

    B({"KR": "부분과 전체", "EN": "Physics and Beyond", "ZH": "部分与整体", "VI": "Phần và toàn thể"}, "베르너 하이젠베르크",
      "science", "⚛️", "from-cyan-200 to-blue-300", "C-2", "셋째 줄", True,
      {"KR": "양자역학의 창시자가 들려주는 과학과 철학의 대화.",
       "EN": "The quantum pioneer's dialogues on science and philosophy.",
       "ZH": "量子力学奠基者讲述科学与哲学的对话。",
       "VI": "Đối thoại về khoa học và triết học của cha đẻ cơ học lượng tử."},
      {"KR": ["#양자역학", "#과학철학", "#하이젠베르크"], "EN": ["#quantum", "#philosophy", "#heisenberg"],
       "ZH": ["#量子力学", "#科学哲学", "#海森堡"], "VI": ["#lượng-tử", "#triết-học", "#heisenberg"]}),
]


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS cb_books"))
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
