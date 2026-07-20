#pragma once

#include <QByteArray>
#include <QImage>
#include <QObject>
#include <QQuickImageProvider>
#include <QString>
#include <QTcpSocket>
#include <QTimer>
#include <QVariantMap>

// PerceptionClient
// -----------------------------------------------------------------------------
// AI 서버의 perception_server(기본 TCP 5007)에 붙어 영상 프레임을 받고 등록/해제 명령을
// 보낸다. 주소는 환경변수 PERCEPTION_URL (host:port) 로 주입받는다 — ROBOT_ID/FMS_URL 과
// 같은 방식이며 gui.sh 가 넘겨준다.
//
// 프로토콜 (scripts/frame_proto.py, scripts/perception_server.py):
//   서버 -> 클라이언트 : [4바이트 빅엔디언 길이][페이로드]
//       페이로드는 JPEG 이거나, "LIDR <8개 정수>" 라이다 텔레메트리 텍스트다. 둘이 같은
//       스트림에 섞여 오므로 접두사로 갈라야 한다.
//   클라이언트 -> 서버 : "register\n" / "reset\n"
//
// bbox·OWNER 라벨·등록 대상 하이라이트는 **서버가 JPEG 안에 이미 그려서** 보낸다.
// 여기서는 그리지 않는다 — 같은 화면을 두 곳에서 그리면 반드시 어긋난다.
class PerceptionClient : public QObject {
    Q_OBJECT

    Q_PROPERTY(bool connected READ connected NOTIFY connectedChanged)
    Q_PROPERTY(QString endpoint READ endpoint CONSTANT)
    Q_PROPERTY(QString statusText READ statusText NOTIFY statusTextChanged)
    // 프레임이 바뀔 때마다 증가. QML Image 의 source 에 붙여 캐시를 무력화하는 용도다
    // (같은 URL 이면 Qt 가 다시 안 읽어서 화면이 첫 프레임에 멈춘다).
    Q_PROPERTY(int frameCounter READ frameCounter NOTIFY frameChanged)
    // 8방향 장애물 거리(cm). 키: front/frontLeft/frontRight/left/right/back/backLeft/backRight.
    // -1 은 측정값 없음. 서버가 --lidar-ros 로 떴을 때만 채워진다.
    Q_PROPERTY(QVariantMap lidar READ lidar NOTIFY lidarChanged)

public:
    explicit PerceptionClient(QObject *parent = nullptr);

    bool connected() const { return m_connected; }
    QString endpoint() const { return m_host + ":" + QString::number(m_port); }
    QString statusText() const { return m_statusText; }
    int frameCounter() const { return m_frameCounter; }
    QVariantMap lidar() const { return m_lidar; }

    QImage latestFrame() const { return m_frame; }

    Q_INVOKABLE void start();        // 연결 시작 (끊기면 자동 재시도)
    Q_INVOKABLE void stop();         // 연결 종료 + 재시도 중단
    Q_INVOKABLE void registerTarget();
    Q_INVOKABLE void resetTarget();

signals:
    void connectedChanged();
    void statusTextChanged();
    void frameChanged();
    void lidarChanged();

private:
    void onConnected();
    void onDisconnected();
    void onReadyRead();
    bool takeFrame(QByteArray &payload);      // 버퍼에서 완결된 프레임 하나를 꺼낸다
    void applyLidar(const QByteArray &payload);
    void sendCommand(const char *cmd);
    void setStatus(const QString &s);

    QTcpSocket m_sock;
    QTimer m_retry;
    QByteArray m_buf;
    QImage m_frame;

    QString m_host;
    quint16 m_port = 5007;
    bool m_connected = false;
    bool m_wanted = false;           // start() 로 켜둔 상태인지 (재시도 여부를 가른다)
    int m_frameCounter = 0;
    QString m_statusText;
    QVariantMap m_lidar;
};

// QML 에 QImage 를 넘기는 통로. Image { source: "image://perception/frame?<counter>" }
class PerceptionImageProvider : public QQuickImageProvider {
public:
    explicit PerceptionImageProvider(PerceptionClient *client)
        : QQuickImageProvider(QQuickImageProvider::Image), m_client(client) {}

    QImage requestImage(const QString &id, QSize *size, const QSize &requested) override;

private:
    PerceptionClient *m_client;
};
