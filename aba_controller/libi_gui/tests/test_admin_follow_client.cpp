// RobotController 의 FMS 추종 승인 클라이언트 검증.
//
// 이 코드는 네트워크 응답에 따라 추종을 시작하거나 말거나를 가르는 지점이라, 눈으로 읽고
// 넘어가면 안 된다. 실제 FMS admin_follow 라우터를 띄워놓고 이 바이너리를 돌린다:
//
//   cmake -S . -B build -DLIBI_GUI_TESTS=ON && cmake --build build -j
//   ./build/test_admin_follow_client <fms_url>
//
// QCoreApplication 으로 충분하다 — RobotController 는 QObject/QTimer 만 쓰고 QML 을 모른다.
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QString>
#include <QUrl>

#include <cstdio>
#include <cstdlib>

#include "../src/RobotController.h"

static int failures = 0;

static void check(bool ok, const QString &what) {
    std::printf("%s  %s\n", ok ? "  ok  " : "FAILED", qPrintable(what));
    if (!ok) ++failures;
}

// 응답이 올 때까지(또는 타임아웃) 이벤트 루프를 돌린다. 요청이 비동기라 그냥 값을 읽으면
// 항상 요청 전 값이 보인다.
static void settle(int ms) {
    QElapsedTimer t;
    t.start();
    while (t.elapsed() < ms)
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
}

static RobotController *makeAdminController() {
    auto *c = new RobotController;
    c->login(QStringLiteral("1234"));      // 목 PIN — 관리자가 아니면 추종 요청 자체가 막힌다
    return c;
}

// FMS 의 승인 기록은 프로세스가 살아있는 한 남는다. 앞선 실행(또는 손으로 친 curl)이 남긴
// grant 가 있으면 이 테스트의 첫 요청이 "이미 추종 중"으로 거부돼, 멀쩡한 코드가 실패한
// 것처럼 보인다. 그래서 쓰는 로봇들을 먼저 비우고 시작한다.
static void clearGrant(QNetworkAccessManager &net, const QString &fmsUrl, const QString &robotId) {
    QNetworkRequest req{QUrl(fmsUrl + QStringLiteral("/api/robot/admin-follow/release"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("robot_id")] = robotId;
    QNetworkReply *reply = net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    while (!reply->isFinished())
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
    reply->deleteLater();
}

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    const QString fmsUrl = argc > 1 ? QString::fromLocal8Bit(argv[1])
                                    : QStringLiteral("http://127.0.0.1:9987");

    QNetworkAccessManager net;
    clearGrant(net, fmsUrl, QStringLiteral("pinky3"));
    clearGrant(net, fmsUrl, QStringLiteral("pinky9"));

    // ── 1. 승인되면 추종이 시작된다 ─────────────────────────────────────────
    qputenv("FMS_URL", fmsUrl.toLocal8Bit());
    qputenv("ROBOT_ID", "pinky3");                  // live_fms.py 가 IDLE 로 유지하는 로봇
    {
        auto *c = makeAdminController();
        check(c->robotId() == QStringLiteral("pinky3"), "ROBOT_ID 를 환경변수에서 읽는다");
        check(!c->following(), "요청 전에는 추종 중이 아니다");
        c->startAdminFollow();
        check(!c->following(), "응답 오기 전에 먼저 시작해버리지 않는다 (낙관적 전환 금지)");
        settle(1500);
        check(c->following(), "accepted=true 응답을 받으면 추종이 시작된다");

        c->stopAdminFollow();
        check(!c->following(), "종료하면 추종이 멈춘다");
        settle(800);                                 // release 보고가 나갈 시간
        delete c;
    }

    // ── 2. 거부되면 시작하지 않는다 ─────────────────────────────────────────
    qputenv("ROBOT_ID", "pinky9");                  // live_fms.py 가 WORKING 으로 유지하는 로봇
    {
        auto *c = makeAdminController();
        c->startAdminFollow();
        settle(1500);
        check(!c->following(), "accepted=false 면 추종을 시작하지 않는다");
        delete c;
    }

    // ── 3. FMS 에 못 닿으면 시작하지 않는다 (fail-closed) ───────────────────
    // 관제가 모르는 추종이 도는 것이 이 승인 절차가 막으려는 바로 그 상황이다.
    qputenv("FMS_URL", "http://127.0.0.1:9");       // 아무도 안 듣는 포트
    qputenv("ROBOT_ID", "pinky3");
    {
        auto *c = makeAdminController();
        c->startAdminFollow();
        settle(2500);
        check(!c->following(), "FMS 통신 실패 시 추종을 시작하지 않는다 (fail-closed)");
        delete c;
    }

    // ── 4. ROBOT_ID 가 없으면 요청 자체를 안 보낸다 ─────────────────────────
    qputenv("FMS_URL", fmsUrl.toLocal8Bit());
    qunsetenv("ROBOT_ID");
    {
        auto *c = makeAdminController();
        check(c->robotId().isEmpty(), "ROBOT_ID 미설정이면 빈 값이다");
        c->startAdminFollow();
        settle(1000);
        check(!c->following(), "ROBOT_ID 없이는 추종을 시작하지 않는다");
        delete c;
    }

    std::printf("\n%s (%d 실패)\n", failures == 0 ? "PASS" : "FAIL", failures);
    return failures == 0 ? 0 : 1;
}
