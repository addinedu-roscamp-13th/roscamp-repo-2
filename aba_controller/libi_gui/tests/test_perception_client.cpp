// PerceptionClient 의 스트림 파싱·명령 전송 검증.
//
// 길이 프레이밍을 잘못 다루면 화면이 조용히 멈추거나 깨진 프레임이 쌓이므로 눈으로 읽고
// 넘어갈 코드가 아니다. perception_server 와 같은 와이어 포맷을 내는 서버를 띄워놓고 돌린다:
//
//   python3 <scratchpad>/fake_perception.py 5099 /tmp/cmds.log &
//   ./build/test_perception_client 127.0.0.1:5099
//
// 서버가 실제로 register/reset 을 받았는지는 cmd 로그로 확인한다 (run_perception_test.sh).
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QString>

#include <cstdio>

#include "../src/PerceptionClient.h"

static int failures = 0;

static void check(bool ok, const QString &what) {
    std::printf("%s  %s\n", ok ? "  ok  " : "FAILED", qPrintable(what));
    if (!ok) ++failures;
}

// 조건이 만족될 때까지(또는 타임아웃) 이벤트 루프를 돌린다.
template <typename Pred>
static bool waitFor(Pred pred, int timeoutMs) {
    QElapsedTimer t;
    t.start();
    while (t.elapsed() < timeoutMs) {
        if (pred()) return true;
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
    }
    return pred();
}

static void settle(int ms) {
    QElapsedTimer t;
    t.start();
    while (t.elapsed() < ms)
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
}

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    const QByteArray url = argc > 1 ? QByteArray(argv[1]) : QByteArray("127.0.0.1:5099");

    qputenv("PERCEPTION_URL", url);
    PerceptionClient client;

    check(client.endpoint() == QString::fromLatin1(url), "PERCEPTION_URL 을 host:port 로 파싱한다");
    check(!client.connected(), "start() 전에는 연결돼 있지 않다");

    client.start();
    check(waitFor([&] { return client.connected(); }, 5000), "서버에 연결된다");
    check(waitFor([&] { return client.frameCounter() > 0; }, 5000), "프레임을 받는다");

    // 라이다 프레임(LIDR)이 JPEG 으로 디코딩되면 여기서 빈 이미지가 잡힌다.
    const QImage frame = client.latestFrame();
    check(!frame.isNull(), "받은 프레임이 유효한 이미지로 디코딩된다");
    check(frame.width() == 320 && frame.height() == 240,
          QStringLiteral("프레임 크기가 320x240 이다 (실제 %1x%2)")
              .arg(frame.width()).arg(frame.height()));

    // 3장마다 LIDR 이 섞여 오므로, 그걸 JPEG 으로 세면 카운터가 실제 영상보다 부풀고
    // 디코딩 실패 프레임이 화면에 깜빡인다. 계속 유효한 이미지만 오는지 본다.
    const int before = client.frameCounter();
    settle(600);
    check(client.frameCounter() > before, "프레임이 계속 들어온다");
    check(!client.latestFrame().isNull(), "LIDR 프레임이 섞여도 영상이 깨지지 않는다");

    // 라이다 텔레메트리는 버리지 않고 8방향 값으로 파싱한다.
    // 가짜 서버가 보내는 값: LIDR 100 120 110 300 310 400 410 420 (FL F FR L R BL B BR)
    const QVariantMap lidar = client.lidar();
    check(lidar.size() == 8, QStringLiteral("라이다 8방향이 모두 채워진다 (실제 %1개)").arg(lidar.size()));
    check(lidar.value("frontLeft").toInt() == 100 &&
          lidar.value("front").toInt() == 120 &&
          lidar.value("frontRight").toInt() == 110,
          "전방 3방향이 서버가 보낸 순서(FL F FR)대로 들어간다");
    check(lidar.value("backRight").toInt() == 420, "마지막 값(BR)까지 어긋나지 않는다");

    client.registerTarget();
    client.resetTarget();
    settle(400);                      // 서버가 받아 로그에 적을 시간

    client.stop();
    check(!client.connected(), "stop() 하면 연결이 끊긴다");
    const int afterStop = client.frameCounter();
    settle(400);
    check(client.frameCounter() == afterStop, "stop() 후에는 프레임을 더 받지 않는다");

    // 다시 붙을 수 있어야 한다 — 추종을 껐다 켜면 이 경로를 다시 탄다.
    client.start();
    check(waitFor([&] { return client.connected(); }, 5000), "stop() 후 다시 연결된다");
    client.stop();

    std::printf("\n%s (%d 실패)\n", failures == 0 ? "PASS" : "FAIL", failures);
    return failures == 0 ? 0 : 1;
}
