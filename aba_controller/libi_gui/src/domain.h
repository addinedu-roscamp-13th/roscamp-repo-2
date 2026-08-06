#pragma once
//
// 화면도 ROS 도 모르는 순수 변환들. RobotController 에서 떼어낸 이유는 테스트 때문이다 —
// 여기가 조용히 틀리면(좌표 부호, 상태 판정) 화면은 멀쩡히 그려지고 값만 거짓이 된다.
// tests/test_domain.cpp 가 이 파일만 링크한다(QtCore 만 필요, QtGui·rclcpp 불필요).
//
#include <QJsonArray>
#include <QJsonObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>
#include <cmath>

namespace libi {

// ── 도서 ────────────────────────────────────────────────────────────────────
//
// ⚠️ 한글 리터럴 비교에 QLatin1String 을 쓰면 **항상 거짓**이다 — 소스가 UTF-8 이라
// "과학" 은 6바이트인데 QLatin1String 은 1바이트=1글자로 봐서 길이부터 어긋난다.
// 예전엔 아래 카테고리 변환이 그래서, 분야 칩을 눌러도 필터가 빠진 전체 목록이 왔다.

/** 화면 칩(한글) → ABA Service `/api/books` 의 category 값(영문). 없으면 빈 문자열. */
inline QString korToApiCategory(const QString &kor) {
    if (kor == QStringLiteral("과학")) return QStringLiteral("science");
    if (kor == QStringLiteral("예술")) return QStringLiteral("art");
    if (kor == QStringLiteral("문학")) return QStringLiteral("literature");
    if (kor == QStringLiteral("인문학")) return QStringLiteral("humanities");
    return QString();   // "전체"/빈 값 등 — 필터 없음
}

inline QString apiToKorCategory(const QString &api) {
    if (api == QLatin1String("science")) return QStringLiteral("과학");
    if (api == QLatin1String("art")) return QStringLiteral("예술");
    if (api == QLatin1String("literature")) return QStringLiteral("문학");
    if (api == QLatin1String("humanities")) return QStringLiteral("인문학");
    return api;
}

// 도서 상태 — 회원 앱 `book-status.ts` 와 **같은 판정·같은 문구**를 쓴다. 두 화면이 같은
// 책을 다르게 말하면 이용자가 사서에게 묻는 상황이 된다.
//   in_stock=false  → 대출 중(서가에 없음)
//   unavailable=true → 훼손·분실로 사서가 막음. in_stock 과 무관하게 이쪽이 우선.
inline QString bookStatus(bool inStock, bool unavailable) {
    if (unavailable) return QStringLiteral("대출 불가");
    return inStock ? QStringLiteral("배치중") : QStringLiteral("대출 중");
}

// 표시 문구(한글)로 다시 갈래를 타지 않고 원래 플래그로 판단한다 — 문구는 바뀌기 쉽고,
// 바뀌면 이 분기가 조용히 어긋난다.
inline QString bookStatusSentence(bool inStock, bool unavailable,
                                  const QString &zone, const QString &shelf) {
    if (unavailable) return QStringLiteral("훼손·분실로 사서가 대출을 막아둔 도서예요.");
    if (!inStock) return QStringLiteral("지금은 대출 중이라 서가에 없어요.");
    return zone + QStringLiteral(" ") + shelf + QStringLiteral("에 진열돼 있어요.");
}

/** ABA Service 의 BookOut(JSON) → 화면이 쓰는 book QVariantMap.
 *  청구기호(call)는 DB 에 아직 없어 서가 위치(shelf)로 대신한다. */
inline QVariantMap bookFromJson(const QJsonObject &o) {
    const QString zone = o.value("zone").toString();
    const QString shelf = o.value("shelf").toString();
    const bool inStock = o.value("inStock").toBool();
    const bool unavailable = o.value("unavailable").toBool();

    QVariantMap m;
    m["id"] = o.value("id").toString();
    m["title"] = o.value("title").toObject().value("KR").toString();
    m["author"] = o.value("author").toString();
    m["call"] = shelf;
    m["shelf"] = shelf;
    m["category"] = apiToKorCategory(o.value("category").toString());
    m["inStock"] = inStock;
    m["unavailable"] = unavailable;
    // "빌릴 수 있는가". **"서가에 있는가"와 다르다** — 훼손본은 꽂혀 있어도 빌릴 수 없다.
    m["available"] = inStock && !unavailable;
    m["status"] = bookStatus(inStock, unavailable);
    m["statusSentence"] = bookStatusSentence(inStock, unavailable, zone, shelf);
    m["location"] = zone;
    m["cover"] = o.value("cover").toString();
    m["summary"] = o.value("summary").toObject().value("KR").toString();
    QStringList tags;
    for (const QJsonValue &t : o.value("forWhom").toObject().value("KR").toArray())
        tags << t.toString();
    m["tags"] = tags;
    return m;
}

// ── 지도 좌표 ───────────────────────────────────────────────────────────────
//
// 실제 nav 좌표(map 프레임, m) → 안내판 그림(artemap.png) 안의 0..1 비율.
//
// ## 어떻게 구한 값인가
// 안내판은 점유격자를 보고 그린 **양식화된 그림**이라 가구가 1:1 로 맞지 않는다.
// 믿을 수 있는 대응은 **방의 외벽** 하나뿐이라, 점유격자의 벽 경계상자를 그림의 벽
// 안쪽 사각형에 그대로 대응시킨다. 축은 90° 돌아 있다(그림 가로 = -map_y, 세로 = -map_x).
//
//   벽 실좌표  arte2.pgm(63x108, res 0.020, origin[-0.184,-1.949]) 의 점유셀 경계상자
//              x -0.144..0.876 (1.020m)   y -1.909..0.111 (2.020m)   비율 1.980
//              (arte3.pgm 도 완전히 같다)
//   그림 방    artemap.png(1672x941, [2026-08-06] 화장실 남/녀 분리판으로 교체) 남색 벽
//              **안쪽**  x 62..1609px   y 68..880px (1547x812, 비율 1.905 — 실제와 3.8% 차이.
//              축별로 따로 맞춰 흡수한다)
//
// waypoint.yaml 정점 11개를 이 변환으로 찍어 전부 제 구역 옆에 떨어지는 걸 확인했다.
// **그림이나 지도를 갈아끼우면 tools/measure_map_boxes.py 로 다시 재서 여기를 고쳐야 한다.**
// test_domain 이 golden 좌표로 이 값을 붙들고 있다.
constexpr double MAP_X_MIN = -0.144;
constexpr double MAP_X_MAX =  0.876;
constexpr double MAP_Y_MIN = -1.909;
constexpr double MAP_Y_MAX =  0.111;

constexpr double IMG_LEFT   = 62.0  / 1672.0;
constexpr double IMG_RIGHT  = 1609.0 / 1672.0;
constexpr double IMG_TOP    = 68.0  / 941.0;
constexpr double IMG_BOTTOM = 880.0 / 941.0;

/** map 프레임 (x,y)[m] → 그림 가로 비율 0..1. */
inline double mapToImageU(double y) {
    return IMG_LEFT + (IMG_RIGHT - IMG_LEFT) * (MAP_Y_MAX - y) / (MAP_Y_MAX - MAP_Y_MIN);
}

/** map 프레임 (x,y)[m] → 그림 세로 비율 0..1. */
inline double mapToImageV(double x) {
    return IMG_TOP + (IMG_BOTTOM - IMG_TOP) * (MAP_X_MAX - x) / (MAP_X_MAX - MAP_X_MIN);
}

/** map 프레임 yaw[rad] → 위를 향해 그린 마커에 줄 QML rotation[도].
 *
 *  그림 오른쪽 = map -y, 아래 = map -x 이므로 heading (cos,sin) 은 화면에서
 *  (-sin yaw, -cos yaw) 가 된다. 위를 향한 화살표 (0,-1) 을 시계방향 θ 만큼 돌리면
 *  (sin θ, -cos θ) 이므로 θ = -yaw. **부호를 빼먹으면 좌우가 뒤집힌다.** */
inline double mapYawToImageRotationDeg(double yawRad) {
    return -yawRad * 180.0 / M_PI;
}

}   // namespace libi
