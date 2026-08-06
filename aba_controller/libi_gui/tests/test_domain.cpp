// src/domain.h 의 순수 변환들을 붙든다. 화면도 ROS 도 없이 도는 시험이다.
//
// 여기 있는 두 가지는 **조용히 틀리는** 종류다 — 코드는 멀쩡히 돌고 값만 거짓이 된다.
//   1. 도서 상태 판정: 한때 한글 비교에 QLatin1String 을 써서 분야 필터가 통째로 무시됐다.
//   2. 지도 좌표: 그림이나 지도를 갈아끼우면 하드코딩 상수가 조용히 어긋난다.
//
// ⚠️ "정점이 방 안에 들어온다" 만 보는 시험은 **부족하다.** x·y 를 둘 다 뒤집어도 모든
//    점은 여전히 방 안이다. 그래서 아래는 골든 좌표(실측 그림 기준)를 값으로 박는다.
//
//   cmake -S . -B build -DLIBI_GUI_TESTS=ON && cmake --build build -j
//   ./build/test_domain
#include "../src/domain.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QtGlobal>
#include <cmath>
#include <cstdio>

static int g_failed = 0;

static void check(bool ok, const char *what) {
    if (!ok) { std::printf("  FAIL  %s\n", what); ++g_failed; }
    else     { std::printf("  ok    %s\n", what); }
}

static void checkNear(double got, double want, double tol, const char *what) {
    const bool ok = std::fabs(got - want) <= tol;
    if (!ok) std::printf("  FAIL  %s  (got %.4f, want %.4f ±%.4f)\n", what, got, want, tol);
    else     std::printf("  ok    %s  (%.4f)\n", what, got);
    if (!ok) ++g_failed;
}

static QJsonObject book(bool inStock, bool unavailable) {
    return QJsonObject{
        {"id", "12"},
        {"title", QJsonObject{{"KR", "정의란 무엇인가"}}},
        {"author", "마이클 샌델"},
        {"category", "humanities"},
        {"zone", "과학-인문학서가"},
        {"shelf", "셋째 줄"},
        {"inStock", inStock},
        {"unavailable", unavailable},
        {"cover", "⚖️"},
        {"summary", QJsonObject{{"KR", "정의를 다룬 책."}}},
        {"forWhom", QJsonObject{{"KR", QJsonArray{"#정치철학"}}}},
    };
}

static void testBookStatus() {
    std::printf("[도서 상태] 회원 앱 book-status.ts 와 같은 세 갈래여야 한다\n");
    check(libi::bookStatus(true, false) == QStringLiteral("배치중"), "재고 있고 막히지 않음 → 배치중");
    check(libi::bookStatus(false, false) == QStringLiteral("대출 중"), "재고 없음 → 대출 중");
    // 서가에 꽂혀 있어도 사서가 막았으면 빌릴 수 없다 — unavailable 이 우선이다.
    check(libi::bookStatus(true, true) == QStringLiteral("대출 불가"), "막힘 우선(재고 있어도)");
    check(libi::bookStatus(false, true) == QStringLiteral("대출 불가"), "막힘 우선(재고 없어도)");

    const QVariantMap shelved = libi::bookFromJson(book(true, false));
    check(shelved["status"].toString() == QStringLiteral("배치중"), "JSON → 배치중");
    check(shelved["available"].toBool(), "배치중이면 빌릴 수 있다");
    check(shelved["inStock"].toBool(), "inStock 을 그대로 내보낸다(필터가 이걸 쓴다)");
    check(shelved["statusSentence"].toString().contains(QStringLiteral("진열")), "진열 문장");
    check(shelved["category"].toString() == QStringLiteral("인문학"), "분야 영→한");
    check(shelved["tags"].toStringList().size() == 1, "태그 파싱");

    // ⚠️ 이 한 줄이 필터 문구의 정직함을 지킨다. "지금 서가에 있는 책" 칩은 inStock 으로
    //    거르는데, available 로 거르면 이 책(꽂혀 있지만 막힌 훼손본)이 사라진다.
    const QVariantMap damaged = libi::bookFromJson(book(true, true));
    check(damaged["inStock"].toBool() && !damaged["available"].toBool(),
          "훼손본은 서가엔 있지만 빌릴 수는 없다");
}

static void testCategoryRoundTrip() {
    std::printf("\n[분야 변환] 한글 리터럴 비교가 QLatin1String 이면 여기서 전부 깨진다\n");
    check(libi::korToApiCategory(QStringLiteral("과학")) == QStringLiteral("science"), "과학 → science");
    check(libi::korToApiCategory(QStringLiteral("예술")) == QStringLiteral("art"), "예술 → art");
    check(libi::korToApiCategory(QStringLiteral("문학")) == QStringLiteral("literature"), "문학 → literature");
    check(libi::korToApiCategory(QStringLiteral("인문학")) == QStringLiteral("humanities"), "인문학 → humanities");
    check(libi::korToApiCategory(QStringLiteral("전체")).isEmpty(), "전체 → 필터 없음");
    check(libi::apiToKorCategory(QStringLiteral("science")) == QStringLiteral("과학"), "science → 과학");
}

// waypoint.yaml 정점 → 그림 좌표(0..1) 골든값.
// 값의 출처: tools/measure_map_boxes.py 로 잰 방 사각형 + arte2.pgm 벽 경계상자.
// 그림이나 지도를 바꾸면 이 표를 다시 만들어야 한다 — 여기서 깨지는 게 그 신호다.
struct Golden { const char *name; double mx, my, u, v; };
static const Golden GOLDEN[] = {
    {"남자화장실",      0.7518, -0.3615, 0.2535, 0.1773},
    {"여자화장실",      0.7518, -0.0333, 0.1032, 0.1773},
    {"수거함",         0.7518, -1.0010, 0.5464, 0.1773},
    {"예술서가",        0.3000, -0.3615, 0.2535, 0.5596},
    {"문학서가",        0.0263, -0.3615, 0.2535, 0.7911},
    {"과학-인문학서가",  0.3000, -0.6600, 0.3902, 0.5596},
    {"도서관출입구",     0.3000, -1.7040, 0.8684, 0.5596},
    {"안네데스크",      0.0263, -1.7040, 0.8684, 0.7911},
    // sim 실측 pose. 이 값이 그림 어디에 찍히는지 눈으로 확인한 자리다(스크린샷 검증됨).
    {"sim 실측 pose",   0.6105, -0.4116, 0.2765, 0.2969},
};

static void testMapProjection() {
    std::printf("\n[지도 좌표] 실좌표 → 그림 비율 (골든값)\n");
    for (const Golden &g : GOLDEN) {
        checkNear(libi::mapToImageU(g.my), g.u, 0.005, g.name);
        checkNear(libi::mapToImageV(g.mx), g.v, 0.005, g.name);
    }

    std::printf("\n[지도 좌표] 축 방향 — 뒤집히면 로봇이 반대편에 그려진다\n");
    // 그림 가로 = -map_y : y 가 커지면 왼쪽으로 간다
    check(libi::mapToImageU(0.0) < libi::mapToImageU(-1.0), "map +y 는 그림 왼쪽");
    // 그림 세로 = -map_x : x 가 커지면 위로 간다 (화장실·미술작품 쪽)
    check(libi::mapToImageV(0.8) < libi::mapToImageV(0.0), "map +x 는 그림 위쪽");

    std::printf("\n[지도 좌표] 벽 끝이 방 사각형 끝에 닿는다\n");
    checkNear(libi::mapToImageU(libi::MAP_Y_MAX), libi::IMG_LEFT, 1e-9, "y_max → 방 왼쪽 끝");
    checkNear(libi::mapToImageU(libi::MAP_Y_MIN), libi::IMG_RIGHT, 1e-9, "y_min → 방 오른쪽 끝");
    checkNear(libi::mapToImageV(libi::MAP_X_MAX), libi::IMG_TOP, 1e-9, "x_max → 방 위쪽 끝");
    checkNear(libi::mapToImageV(libi::MAP_X_MIN), libi::IMG_BOTTOM, 1e-9, "x_min → 방 아래쪽 끝");

    std::printf("\n[지도 좌표] yaw — 부호를 빼먹으면 좌우가 뒤집힌다\n");
    checkNear(libi::mapYawToImageRotationDeg(0.0), 0.0, 1e-9, "yaw 0(+x) → 화살표 위");
    // map +y(yaw=90°)는 그림 왼쪽이다. 위를 향한 화살표를 왼쪽으로 돌리려면 반시계 90°.
    checkNear(libi::mapYawToImageRotationDeg(M_PI / 2), -90.0, 1e-9, "yaw 90°(+y) → -90°(왼쪽)");
    checkNear(libi::mapYawToImageRotationDeg(-M_PI / 2), 90.0, 1e-9, "yaw -90°(-y) → +90°(오른쪽)");
}

int main() {
    testBookStatus();
    testCategoryRoundTrip();
    testMapProjection();
    std::printf("\n%s\n", g_failed == 0 ? "전부 통과" : "실패 있음");
    return g_failed == 0 ? 0 : 1;
}
