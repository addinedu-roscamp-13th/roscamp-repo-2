export type Book = {
  id: string;
  title: Record<string, string>;
  author: string;
  category: "literature" | "art" | "science";
  cover: string; // emoji cover
  color: string; // tailwind gradient class
  zone: string; // e.g. C-3
  shelf: string; // row
  inStock: boolean;
  summary: Record<string, string>;
  forWhom: Record<string, string[]>;
};

export const STORE = {
  name: { KR: "○○문고 강남점", EN: "○○ Books Gangnam", ZH: "○○书店 江南店", VI: "○○ Sách Gangnam" },
  short: "강남점",
};

export const BOOKS: Book[] = [
  {
    id: "b1",
    title: { KR: "데미안", EN: "Demian", ZH: "德米安", VI: "Demian" },
    author: "헤르만 헤세",
    category: "literature",
    cover: "🕊️",
    color: "from-rose-200 to-rose-300",
    zone: "A-1",
    shelf: "첫째 줄",
    inStock: true,
    summary: {
      KR: "자아를 찾아가는 청년 싱클레어의 내면 성장을 그린 성장소설.",
      EN: "A coming-of-age story of Sinclair's search for his true self.",
      ZH: "描写辛克莱寻找自我的成长小说。",
      VI: "Tiểu thuyết trưởng thành về hành trình tìm kiếm bản ngã.",
    },
    forWhom: {
      KR: ["#성장소설", "#자아탐구", "#헤르만헤세"],
      EN: ["#coming-of-age", "#self", "#classic"],
      ZH: ["#成长", "#自我", "#经典"],
      VI: ["#trưởng-thành", "#bản-ngã", "#kinh-điển"],
    },
  },
  {
    id: "b2",
    title: { KR: "노인과 바다", EN: "The Old Man and the Sea", ZH: "老人与海", VI: "Ông già và biển cả" },
    author: "어니스트 헤밍웨이",
    category: "literature",
    cover: "🐟",
    color: "from-sky-200 to-blue-300",
    zone: "A-2",
    shelf: "첫째 줄",
    inStock: true,
    summary: {
      KR: "거대한 청새치와 싸우는 노어부의 불굴의 의지를 그린 중편.",
      EN: "An old fisherman's unyielding struggle with a giant marlin.",
      ZH: "老渔夫与大马林鱼搏斗的不屈意志。",
      VI: "Ý chí kiên cường của ông lão đánh cá với con cá kiếm khổng lồ.",
    },
    forWhom: {
      KR: ["#고전", "#불굴의의지", "#헤밍웨이"],
      EN: ["#classic", "#perseverance", "#nobel"],
      ZH: ["#经典", "#毅力", "#诺贝尔"],
      VI: ["#kinh-điển", "#kiên-cường", "#nobel"],
    },
  },
  {
    id: "b3",
    title: { KR: "서양미술사", EN: "The Story of Art", ZH: "艺术的故事", VI: "Câu chuyện nghệ thuật" },
    author: "E.H. 곰브리치",
    category: "art",
    cover: "🖼️",
    color: "from-amber-200 to-orange-300",
    zone: "B-1",
    shelf: "첫째 줄",
    inStock: true,
    summary: {
      KR: "선사시대부터 현대까지 미술의 흐름을 한 권에 담은 명저.",
      EN: "The definitive one-volume survey of art from cave to modern.",
      ZH: "从史前到现代的艺术通史名著。",
      VI: "Cuốn sách kinh điển về lịch sử nghệ thuật từ cổ đến hiện đại.",
    },
    forWhom: {
      KR: ["#미술사", "#입문서", "#곰브리치"],
      EN: ["#art-history", "#intro", "#classic"],
      ZH: ["#美术史", "#入门", "#经典"],
      VI: ["#lịch-sử-mỹ-thuật", "#nhập-môn", "#kinh-điển"],
    },
  },
  {
    id: "b4",
    title: { KR: "반 고흐, 영혼의 편지", EN: "The Letters of Vincent van Gogh", ZH: "梵高手稿", VI: "Những lá thư của Van Gogh" },
    author: "빈센트 반 고흐",
    category: "art",
    cover: "🌻",
    color: "from-yellow-200 to-amber-300",
    zone: "B-2",
    shelf: "첫째 줄",
    inStock: false,
    summary: {
      KR: "동생 테오에게 보낸 편지로 읽는 고흐의 예술과 고독.",
      EN: "Van Gogh's art and solitude through letters to his brother Theo.",
      ZH: "通过写给弟弟提奥的书信读懂梵高的艺术与孤独。",
      VI: "Nghệ thuật và nỗi cô đơn của Van Gogh qua thư gửi em trai.",
    },
    forWhom: {
      KR: ["#화가의편지", "#반고흐", "#예술혼"],
      EN: ["#letters", "#van-gogh", "#painter"],
      ZH: ["#书信", "#梵高", "#画家"],
      VI: ["#thư-từ", "#van-gogh", "#họa-sĩ"],
    },
  },
  {
    id: "b5",
    title: { KR: "코스모스", EN: "Cosmos", ZH: "宇宙", VI: "Vũ trụ" },
    author: "칼 세이건",
    category: "science",
    cover: "🌌",
    color: "from-indigo-300 to-purple-400",
    zone: "C-1",
    shelf: "첫째 줄",
    inStock: true,
    summary: {
      KR: "우주와 생명, 과학의 경이를 시적으로 풀어낸 과학 교양의 고전.",
      EN: "A poetic classic on the cosmos, life and the wonder of science.",
      ZH: "诗意讲述宇宙、生命与科学之美的经典。",
      VI: "Tác phẩm kinh điển đầy chất thơ về vũ trụ và khoa học.",
    },
    forWhom: {
      KR: ["#천문학", "#과학교양", "#칼세이건"],
      EN: ["#astronomy", "#popular-science", "#sagan"],
      ZH: ["#天文学", "#科普", "#萨根"],
      VI: ["#thiên-văn", "#khoa-học", "#sagan"],
    },
  },
  {
    id: "b6",
    title: { KR: "시간의 역사", EN: "A Brief History of Time", ZH: "时间简史", VI: "Lược sử thời gian" },
    author: "스티븐 호킹",
    category: "science",
    cover: "⏳",
    color: "from-slate-300 to-zinc-400",
    zone: "C-2",
    shelf: "첫째 줄",
    inStock: true,
    summary: {
      KR: "빅뱅과 블랙홀, 시간의 비밀을 대중에게 전한 우주론 입문서.",
      EN: "A popular introduction to the Big Bang, black holes and time.",
      ZH: "向大众讲述大爆炸、黑洞与时间的宇宙学入门。",
      VI: "Dẫn nhập đại chúng về Big Bang, hố đen và thời gian.",
    },
    forWhom: {
      KR: ["#우주론", "#물리학", "#호킹"],
      EN: ["#cosmology", "#physics", "#hawking"],
      ZH: ["#宇宙学", "#物理", "#霍金"],
      VI: ["#vũ-trụ-học", "#vật-lý", "#hawking"],
    },
  },
];

export const ZONES = [
  { id: "A", label: "문학", x: 8, y: 10, w: 40, h: 32, color: "bg-rose-200" },
  { id: "B", label: "예술", x: 52, y: 10, w: 40, h: 32, color: "bg-amber-200" },
  { id: "C", label: "과학", x: 8, y: 48, w: 40, h: 32, color: "bg-sky-200" },
  { id: "CAFE", label: "북카페 ☕", x: 52, y: 48, w: 24, h: 32, color: "bg-orange-200" },
  { id: "WC", label: "화장실", x: 80, y: 48, w: 12, h: 32, color: "bg-stone-200" },
];

export const QUICK_CHIPS = {
  KR: ["📚 추천도서 알려줘", "과학 책 추천해줘", "예술 책 추천해줘", "문학 책 추천해줘", "화장실 어디야?"],
  EN: ["📚 Recommend a book", "Recommend a science book", "Recommend an art book", "Recommend a literature book", "Where is the restroom?"],
  ZH: ["📚 推荐一本书", "推荐一本科学书", "推荐一本艺术书", "推荐一本文学书", "洗手间在哪?"],
  VI: ["📚 Gợi ý một cuốn sách", "Gợi ý sách khoa học", "Gợi ý sách nghệ thuật", "Gợi ý sách văn học", "Nhà vệ sinh ở đâu?"],
};
