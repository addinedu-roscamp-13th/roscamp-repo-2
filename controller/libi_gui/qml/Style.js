.pragma library

// Libi 디자인 토큰 — pingdergarten(유치원 파스텔/크림/둥근모서리) 디자인 언어 참고
// 레퍼런스 admin theme.py / colors.ts 의 팔레트를 도서관 로봇 'Libi' 에 맞게 정리

// 배경 / 표면
var bg          = "#FBF4E9";   // 따뜻한 크림
var bgAlt       = "#F6EAD7";
var surface     = "#FFFFFF";
var surfaceSoft = "#FFFBF4";
var border      = "#F0E0CD";
var borderStrong= "#E7C9A8";

// 텍스트
var text        = "#3F3530";
var textSoft    = "#6E5F54";
var textMuted   = "#9C8A7C";
var onPrimary   = "#FFFFFF";

// 강조 / 포인트
var primary     = "#FF8FAB";   // 핑크 (Libi 메인)
var primaryDim  = "#FFD3DE";
var accent      = "#FFC371";   // 허니
var sky         = "#7EC8E3";
var mint        = "#A8E6CF";
var lavender    = "#C5B0E8";
var sun         = "#FFD56B";

// 상태색
var success     = "#7DCEA0";
var warning     = "#FFAB76";
var danger      = "#FF6B6B";   // 비상정지

// 카테고리색 (과학/예술/문학)
function categoryColor(cat) {
    if (cat === "과학") return sky;
    if (cat === "예술") return lavender;
    if (cat === "문학") return mint;
    return accent;
}

// 폰트 (Noto Sans CJK KR 설치 확인됨)
var fontFamily  = "Noto Sans CJK KR";

// 라운딩
var radCard   = 24;
var radButton = 20;
var radPill   = 999;

// 간격
var pad     = 24;
var gap     = 16;

// 그림자 색 (반투명)
var shadow  = "#1A8A6A4A";

// 화면 기준 해상도 (10" 태블릿 가로)
var screenW = 1280;
var screenH = 800;
