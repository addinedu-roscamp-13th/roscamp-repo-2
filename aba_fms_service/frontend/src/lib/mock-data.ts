export type Book = {
  id: string;
  title: Record<string, string>;
  author: string;
  category: "fiction" | "self" | "foreign" | "humanities" | "economy" | "poetry";
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
    title: { KR: "불편한 편의점", EN: "The Uncanny Convenience Store", ZH: "不便的便利店", VI: "Cửa hàng tiện lợi bất tiện" },
    author: "김호연",
    category: "fiction",
    cover: "🏪",
    color: "from-amber-200 to-orange-300",
    zone: "A-2",
    shelf: "셋째 줄",
    inStock: true,
    summary: {
      KR: "서울역 노숙인과 작은 편의점이 만들어내는 따뜻한 위로.",
      EN: "A heartwarming tale around a tiny convenience store in Seoul.",
      ZH: "首尔小便利店里的温暖故事。",
      VI: "Câu chuyện ấm áp về một cửa hàng tiện lợi nhỏ ở Seoul.",
    },
    forWhom: {
      KR: ["#일상의위로", "#따뜻한소설", "#밤에읽기좋은"],
      EN: ["#cozy", "#feelgood", "#nighttime-read"],
      ZH: ["#治愈", "#温暖", "#夜读"],
      VI: ["#chữa-lành", "#ấm-áp", "#đọc-đêm"],
    },
  },
  {
    id: "b2",
    title: { KR: "마흔에 읽는 쇼펜하우어", EN: "Schopenhauer at Forty", ZH: "四十读叔本华", VI: "Đọc Schopenhauer tuổi 40" },
    author: "강용수",
    category: "humanities",
    cover: "🕯️",
    color: "from-indigo-200 to-slate-300",
    zone: "C-3",
    shelf: "넷째 줄",
    inStock: true,
    summary: {
      KR: "삶의 고통을 직시한 철학자에게 듣는 어른의 자세.",
      EN: "Adult wisdom from a philosopher who stared down suffering.",
      ZH: "从直面苦难的哲学家那里学到的成熟智慧。",
      VI: "Sự khôn ngoan trưởng thành từ triết gia đối mặt với khổ đau.",
    },
    forWhom: {
      KR: ["#철학입문", "#중년의독서", "#삶의의미"],
      EN: ["#philosophy", "#midlife", "#meaning"],
      ZH: ["#哲学", "#中年", "#意义"],
      VI: ["#triết-học", "#trung-niên", "#ý-nghĩa"],
    },
  },
  {
    id: "b3",
    title: { KR: "트렌드 코리아 2026", EN: "Trend Korea 2026", ZH: "趋势韩国 2026", VI: "Xu hướng Hàn Quốc 2026" },
    author: "김난도 외",
    category: "economy",
    cover: "📈",
    color: "from-emerald-200 to-teal-300",
    zone: "E-1",
    shelf: "첫째 줄",
    inStock: true,
    summary: {
      KR: "내년 한국 소비 트렌드를 한 권으로 정리한 연례 보고서.",
      EN: "Annual report on next year's Korean consumer trends.",
      ZH: "韩国年度消费趋势报告。",
      VI: "Báo cáo xu hướng tiêu dùng Hàn Quốc.",
    },
    forWhom: {
      KR: ["#마케터", "#비즈니스", "#소비트렌드"],
      EN: ["#marketing", "#business", "#trends"],
      ZH: ["#营销", "#商业", "#趋势"],
      VI: ["#marketing", "#kinh-doanh", "#xu-hướng"],
    },
  },
  {
    id: "b4",
    title: { KR: "아주 작은 습관의 힘", EN: "Atomic Habits", ZH: "原子习惯", VI: "Thói quen nguyên tử" },
    author: "James Clear",
    category: "self",
    cover: "⚛️",
    color: "from-sky-200 to-indigo-300",
    zone: "D-1",
    shelf: "둘째 줄",
    inStock: false,
    summary: {
      KR: "1%의 변화가 만드는 거대한 성장의 과학.",
      EN: "The science of tiny changes that create remarkable results.",
      ZH: "微小改变带来巨大成长的科学。",
      VI: "Khoa học về những thay đổi nhỏ tạo kết quả lớn.",
    },
    forWhom: {
      KR: ["#습관형성", "#자기관리", "#목표달성"],
      EN: ["#habits", "#self-mgmt", "#goals"],
      ZH: ["#习惯", "#自律", "#目标"],
      VI: ["#thói-quen", "#kỷ-luật", "#mục-tiêu"],
    },
  },
  {
    id: "b5",
    title: { KR: "달과 6펜스", EN: "The Moon and Sixpence", ZH: "月亮与六便士", VI: "Mặt trăng và đồng sáu xu" },
    author: "Somerset Maugham",
    category: "foreign",
    cover: "🌙",
    color: "from-yellow-200 to-amber-300",
    zone: "F-2",
    shelf: "다섯째 줄",
    inStock: true,
    summary: {
      KR: "안정된 삶을 버리고 그림에 미친 사내의 이야기.",
      EN: "A man who abandons stability to chase art.",
      ZH: "为了艺术抛弃一切的男人的故事。",
      VI: "Người đàn ông từ bỏ ổn định để theo đuổi nghệ thuật.",
    },
    forWhom: {
      KR: ["#고전소설", "#예술가", "#원서읽기"],
      EN: ["#classic", "#artist", "#original"],
      ZH: ["#经典", "#艺术家", "#原版"],
      VI: ["#kinh-điển", "#nghệ-sĩ", "#nguyên-bản"],
    },
  },
];

export const ZONES = [
  { id: "A", label: "소설", x: 10, y: 10, w: 35, h: 25, color: "bg-rose-200" },
  { id: "B", label: "에세이", x: 50, y: 10, w: 40, h: 25, color: "bg-amber-200" },
  { id: "C", label: "인문", x: 10, y: 40, w: 30, h: 25, color: "bg-indigo-200" },
  { id: "D", label: "자기계발", x: 45, y: 40, w: 25, h: 25, color: "bg-sky-200" },
  { id: "E", label: "경제/경영", x: 75, y: 40, w: 15, h: 25, color: "bg-emerald-200" },
  { id: "F", label: "외국도서", x: 10, y: 70, w: 35, h: 22, color: "bg-yellow-200" },
  { id: "CAFE", label: "북카페 ☕", x: 50, y: 70, w: 25, h: 22, color: "bg-orange-200" },
  { id: "WC", label: "화장실", x: 78, y: 70, w: 12, h: 22, color: "bg-stone-200" },
];

export const QUICK_CHIPS = {
  KR: ["화장실 어디야?", "오늘 재고 있는 경제 서적", "외국어 서적 코너 위치", "위로가 되는 시집 추천해줘"],
  EN: ["Where is the restroom?", "Economics books in stock", "Foreign language section", "Recommend a comforting poetry book"],
  ZH: ["洗手间在哪?", "今天有货的经济书", "外文书区位置", "推荐治愈的诗集"],
  VI: ["Nhà vệ sinh ở đâu?", "Sách kinh tế còn hàng", "Khu sách ngoại văn", "Gợi ý thơ chữa lành"],
};
