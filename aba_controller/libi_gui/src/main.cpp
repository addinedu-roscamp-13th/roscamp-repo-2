#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QImage>
#include <QTimer>
#include <QEventLoop>
#include <QDir>
#include <QDebug>

#include "PerceptionClient.h"
#include "RobotController.h"
#include "RosLink.h"

// 검증용: 각 화면을 순회하며 PNG 로 캡처 (live 디스플레이에서 grabWindow)
// 사용:  libi_gui --shots [출력디렉토리]
static int runShots(QGuiApplication &app, QQmlApplicationEngine &engine,
                    RobotController &controller, const QString &outDir) {
    QDir().mkpath(outDir);
    auto *win = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    if (!win) { qWarning() << "root object is not a QQuickWindow"; return 2; }

    QTimer::singleShot(700, [&app, win, &controller, outDir]() {
        auto settle = [](int ms) { QEventLoop l; QTimer::singleShot(ms, &l, &QEventLoop::quit); l.exec(); };
        auto grab = [&](const QString &name) {
            settle(450);
            QImage img = win->grabWindow();
            const QString path = outDir + "/" + name + ".png";
            bool ok = img.save(path);
            qWarning() << "shot" << name << (ok ? "OK" : "FAIL") << img.size();
        };

        controller.setMode("home");            grab("01_home");
        controller.waveHand();                 grab("02_home_wave");
        controller.setMode("search");          grab("03_search");
        controller.setMode("recommend");       grab("04_recommend");
        controller.setMode("guide");           grab("05_guide_select");
        controller.startGuide(QStringLiteral("문학서가")); grab("06_guide_guiding");
        controller.setMode("adminLogin");      grab("08_adminLogin");
        controller.login("1234");
        settle(1700);                          // 로그인 토스트가 사라질 때까지 대기
        controller.setMode("adminControl");    grab("09_adminControl");
        // 추종 화면은 PERCEPTION_URL 이 가리키는 AI 서버에 붙어 영상을 띄운다. 서버가 없으면
        // "연결 중" 상태로 잡히는데, 그것도 확인 대상이라 그대로 캡처한다.
        controller.setMode("follow");
        settle(1200);                          // 첫 프레임이 도착할 시간
        grab("09b_follow");
        controller.emergencyStop();            grab("10_estop_banner");
        controller.setMode("home");            grab("11_estop_overlay");

        app.quit();
    });
    return app.exec();
}

int main(int argc, char *argv[]) {
    QCoreApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QGuiApplication app(argc, argv);
    app.setApplicationName("Libi GUI");

    RobotController controller;
    PerceptionClient perception;

    RosLink ros;
    controller.attachRos(&ros);

    // 추종 중에 GUI 가 꺼지면 FMS 에 승인 기록이 남아, 다음 기동 때 "이미 추종 중" 으로
    // 거부당한다. 종료 직전에 해제를 보내 그 상태를 만들지 않는다.
    QObject::connect(&app, &QGuiApplication::aboutToQuit,
                     &controller, &RobotController::releaseFollowOnExit);

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("controller", &controller);
    engine.rootContext()->setContextProperty("perception", &perception);
    // 엔진이 소유권을 가져간다 (문서화된 addImageProvider 계약) — delete 하지 않는다.
    engine.addImageProvider(QStringLiteral("perception"),
                            new PerceptionImageProvider(&perception));
    engine.load(QUrl(QStringLiteral("qrc:/qml/Main.qml")));
    if (engine.rootObjects().isEmpty()) {
        qWarning() << "Failed to load Main.qml";
        return -1;
    }

    const QStringList args = app.arguments();
    const int idx = args.indexOf(QStringLiteral("--shots"));
    if (idx >= 0) {
        QString outDir = QStringLiteral("/tmp/libi_shots");
        if (idx + 1 < args.size() && !args[idx + 1].startsWith(QStringLiteral("--")))
            outDir = args[idx + 1];
        return runShots(app, engine, controller, outDir);
    }

    return app.exec();
}
