import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "KR" | "EN" | "ZH" | "VI";

export const LANGS: { code: Lang; label: string; native: string; speech: string }[] = [
  { code: "KR", label: "한국어", native: "한국어", speech: "ko-KR" },
  { code: "EN", label: "English", native: "English", speech: "en-US" },
  { code: "ZH", label: "中文", native: "中文", speech: "zh-CN" },
  { code: "VI", label: "Tiếng Việt", native: "Tiếng Việt", speech: "vi-VN" },
];

type Dict = Record<string, Record<Lang, string>>;

export const t: Dict = {
  appName: { KR: "Labi Bot", EN: "Labi Bot", ZH: "Labi Bot", VI: "Labi Bot" },
  tagline: {
    KR: "책으로 다시 태어나다",
    EN: "Reborn through books",
    ZH: "因书重生",
    VI: "Tái sinh qua sách",
  },
  welcome: {
    KR: "반갑습니다! 어떤 책을 찾으시나요?",
    EN: "Welcome! What book are you looking for?",
    ZH: "您好!您在找什么书?",
    VI: "Xin chào! Bạn đang tìm cuốn sách nào?",
  },
  voiceStart: {
    KR: "🎙️ 음성으로 편하게 대화하기",
    EN: "🎙️ Talk to me by voice",
    ZH: "🎙️ 用语音轻松对话",
    VI: "🎙️ Trò chuyện bằng giọng nói",
  },
  textStart: {
    KR: "⌨️ 텍스트로 검색하기",
    EN: "⌨️ Search by text",
    ZH: "⌨️ 用文字搜索",
    VI: "⌨️ Tìm bằng văn bản",
  },
  chooseLang: {
    KR: "먼저 사용할 언어를 선택해 주세요",
    EN: "Choose your language to begin",
    ZH: "请先选择您的语言",
    VI: "Vui lòng chọn ngôn ngữ",
  },
  storeNow: {
    KR: "현재 매장",
    EN: "Current store",
    ZH: "当前门店",
    VI: "Cửa hàng hiện tại",
  },
  tapToTalk: {
    KR: "탭하여 말하기",
    EN: "Tap to talk",
    ZH: "点击说话",
    VI: "Chạm để nói",
  },
  listening: {
    KR: "듣고 있어요...",
    EN: "Listening...",
    ZH: "正在聆听...",
    VI: "Đang nghe...",
  },
  bestseller: { KR: "베스트셀러 / 신간", EN: "Bestsellers / New", ZH: "畅销 / 新书", VI: "Bán chạy / Mới" },
  storeMap: { KR: "로봇 관리자 지도", EN: "Robot Admin Map", ZH: "机器人管理地图", VI: "Bản đồ quản lý robot" },
  cafe: { KR: "북카페 & 편의시설", EN: "Cafe & Amenities", ZH: "书咖 & 设施", VI: "Cafe & Tiện ích" },
  navHome: { KR: "홈", EN: "Home", ZH: "首页", VI: "Trang chủ" },
  navSearch: { KR: "도서 검색", EN: "Search", ZH: "图书", VI: "Tìm sách" },
  navMap: { KR: "공간 안내", EN: "Map", ZH: "地图", VI: "Bản đồ" },
  navChat: { KR: "Labi Bot", EN: "Labi Bot AI", ZH: "Labi Bot", VI: "Labi Bot AI" },
  navMe: { KR: "설정", EN: "Settings", ZH: "设置", VI: "Cài đặt" },
  searchPh: {
    KR: "책 제목, 저자, 또는 '경제 신간'처럼",
    EN: "Title, author, or 'fiction new release'",
    ZH: "书名、作者或'经济新书'",
    VI: "Tên sách, tác giả hoặc chủ đề",
  },
  inStock: { KR: "재고 있음", EN: "In stock", ZH: "有货", VI: "Còn hàng" },
  soldOut: { KR: "품절", EN: "Sold out", ZH: "售罄", VI: "Hết hàng" },
  showOnMap: { KR: "🗺️ 위치 지도로 보기", EN: "🗺️ Show on map", ZH: "🗺️ 在地图上查看", VI: "🗺️ Xem trên bản đồ" },
  hotTitle: {
    KR: "로봇 관리자 주요 기능 TOP 5",
    EN: "Top 5 Robot Admin Features",
    ZH: "机器人管理员主要功能 TOP 5",
    VI: "Top 5 tính năng quản lý robot",
  },
  catAll: { KR: "종합", EN: "All", ZH: "综合", VI: "Tất cả" },
  catFiction: { KR: "소설", EN: "Fiction", ZH: "小说", VI: "Tiểu thuyết" },
  catSelf: { KR: "자기계발", EN: "Self-help", ZH: "自我提升", VI: "Phát triển" },
  catForeign: { KR: "외국도서", EN: "Foreign", ZH: "外文", VI: "Ngoại văn" },
  recommendFor: { KR: "이런 분께 추천해요!", EN: "Recommended for", ZH: "推荐给", VI: "Phù hợp với" },
  chatPh: {
    KR: "위로가 되는 시집 추천해줘",
    EN: "Where is the fiction section?",
    ZH: "推荐一本治愈的诗集",
    VI: "Gợi ý sách thơ chữa lành",
  },
  settings: { KR: "설정", EN: "Settings", ZH: "设置", VI: "Cài đặt" },
  voiceSettings: { KR: "음성 설정", EN: "Voice", ZH: "语音", VI: "Giọng nói" },
  voiceTone: { KR: "AI 목소리 톤", EN: "AI voice tone", ZH: "AI 声音", VI: "Giọng AI" },
  startNow: { KR: "시작하기", EN: "Get Started", ZH: "开始", VI: "Bắt đầu" },
  micDenied: {
    KR: "마이크 권한이 필요해요. 텍스트 입력은 계속 사용 가능합니다.",
    EN: "Mic permission needed. Text input still works.",
    ZH: "需要麦克风权限。仍可使用文字输入。",
    VI: "Cần quyền micro. Vẫn có thể nhập văn bản.",
  },
  noSpeechSupport: {
    KR: "이 브라우저는 음성 인식을 지원하지 않아요.",
    EN: "Voice recognition not supported in this browser.",
    ZH: "此浏览器不支持语音识别。",
    VI: "Trình duyệt không hỗ trợ nhận diện giọng nói.",
  },
};

type I18nCtx = { lang: Lang; setLang: (l: Lang) => void; tr: (key: keyof typeof t) => string };
const Ctx = createContext<I18nCtx | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("KR");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const saved = localStorage.getItem("rebook.lang") as Lang | null;
    if (saved && LANGS.some((l) => l.code === saved)) setLangState(saved);
  }, []);

  const setLang = (l: Lang) => {
    setLangState(l);
    if (typeof window !== "undefined") localStorage.setItem("rebook.lang", l);
  };

  const tr = (key: keyof typeof t) => t[key]?.[lang] ?? t[key]?.KR ?? String(key);
  return <Ctx.Provider value={{ lang, setLang, tr }}>{children}</Ctx.Provider>;
}

export function useI18n() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useI18n outside provider");
  return v;
}
