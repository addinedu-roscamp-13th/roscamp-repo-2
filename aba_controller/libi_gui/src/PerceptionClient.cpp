#include "PerceptionClient.h"

#include <QtEndian>

namespace {
constexpr int RETRY_MS = 1500;
constexpr int HEADER_BYTES = 4;
// 길이 헤더가 깨졌을 때 무한정 버퍼를 키우지 않기 위한 상한. 640px JPEG 은 보통 수십 KB 라
// 8MB 면 정상 프레임을 자를 일이 없다.
constexpr quint32 MAX_FRAME_BYTES = 8u * 1024 * 1024;
const char LIDAR_PREFIX[] = "LIDR ";
}

PerceptionClient::PerceptionClient(QObject *parent) : QObject(parent) {
    // PERCEPTION_URL=<host>:<port> — 포트를 생략하면 perception_server 기본값 5007.
    const QString url = qEnvironmentVariable("PERCEPTION_URL");
    const int colon = url.lastIndexOf(QLatin1Char(':'));
    if (colon > 0) {
        m_host = url.left(colon);
        const quint16 parsed = url.mid(colon + 1).toUShort();
        if (parsed != 0) m_port = parsed;
    } else {
        m_host = url;
    }

    m_retry.setInterval(RETRY_MS);
    m_retry.setSingleShot(true);
    connect(&m_retry, &QTimer::timeout, this, [this]() {
        if (m_wanted) m_sock.connectToHost(m_host, m_port);
    });

    connect(&m_sock, &QTcpSocket::connected, this, &PerceptionClient::onConnected);
    connect(&m_sock, &QTcpSocket::disconnected, this, &PerceptionClient::onDisconnected);
    connect(&m_sock, &QTcpSocket::readyRead, this, &PerceptionClient::onReadyRead);
    // 연결 실패도 재시도로 이어져야 한다 — AI 서버가 GUI 보다 늦게 떠도 알아서 붙는다.
    connect(&m_sock, &QAbstractSocket::errorOccurred,
            this, [this](QAbstractSocket::SocketError) {
                setStatus(m_sock.errorString());
                onDisconnected();
            });
}

void PerceptionClient::start() {
    if (m_host.isEmpty()) {
        setStatus(QStringLiteral("PERCEPTION_URL 이 설정되지 않았습니다 (gui.sh 참조)"));
        return;
    }
    m_wanted = true;
    if (m_sock.state() == QAbstractSocket::UnconnectedState) {
        setStatus(QStringLiteral("연결 중… %1").arg(endpoint()));
        m_sock.connectToHost(m_host, m_port);
    }
}

void PerceptionClient::stop() {
    m_wanted = false;
    m_retry.stop();
    m_sock.abort();
    m_buf.clear();
    if (m_connected) { m_connected = false; emit connectedChanged(); }
    setStatus(QString());
}

void PerceptionClient::onConnected() {
    m_buf.clear();
    m_connected = true; emit connectedChanged();
    setStatus(QStringLiteral("연결됨 — %1").arg(endpoint()));
}

void PerceptionClient::onDisconnected() {
    if (m_connected) { m_connected = false; emit connectedChanged(); }
    if (m_wanted && !m_retry.isActive()) m_retry.start();
}

void PerceptionClient::onReadyRead() {
    m_buf.append(m_sock.readAll());

    QByteArray payload;
    while (takeFrame(payload)) {
        // 라이다 텔레메트리는 같은 스트림에 섞여 온다. JPEG 으로 디코딩하려 들면 매번
        // 실패하며 프레임이 깜빡이므로 접두사로 갈라서 따로 처리한다.
        if (payload.startsWith(LIDAR_PREFIX)) { applyLidar(payload); continue; }

        QImage img;
        if (!img.loadFromData(payload, "JPG")) continue;
        m_frame = img;
        ++m_frameCounter;
        emit frameChanged();
    }
}

void PerceptionClient::applyLidar(const QByteArray &payload) {
    // "LIDR <FL> <F> <FR> <L> <R> <BL> <B> <BR>" — 센티미터, -1 은 측정값 없음.
    // 순서는 perception_server.serve_loop 의 발신 순서와 같아야 한다.
    static const char *KEYS[] = {"frontLeft", "front", "frontRight", "left",
                                 "right", "backLeft", "back", "backRight"};
    constexpr int FIELDS = 8;

    const QList<QByteArray> parts = payload.simplified().split(' ');
    if (parts.size() != FIELDS + 1) return;   // 접두사 + 8개가 아니면 버린다

    QVariantMap next;
    for (int i = 0; i < FIELDS; ++i) {
        bool ok = false;
        const int cm = parts[i + 1].toInt(&ok);
        if (!ok) return;                      // 일부만 반영하면 화면이 뒤섞인 값을 보여준다
        next.insert(QLatin1String(KEYS[i]), cm);
    }
    if (next == m_lidar) return;
    m_lidar = next;
    emit lidarChanged();
}

bool PerceptionClient::takeFrame(QByteArray &payload) {
    if (m_buf.size() < HEADER_BYTES) return false;

    const quint32 len = qFromBigEndian<quint32>(
        reinterpret_cast<const uchar *>(m_buf.constData()));
    if (len > MAX_FRAME_BYTES) {
        // 스트림 동기가 깨진 것 — 이어 붙여봐야 계속 쓰레기라 끊고 다시 붙는다.
        setStatus(QStringLiteral("프레임 길이가 비정상입니다. 재연결합니다."));
        m_buf.clear();
        m_sock.abort();
        onDisconnected();
        return false;
    }
    if (static_cast<quint32>(m_buf.size()) < HEADER_BYTES + len) return false;  // 아직 덜 왔다

    payload = m_buf.mid(HEADER_BYTES, static_cast<int>(len));
    m_buf.remove(0, HEADER_BYTES + static_cast<int>(len));
    return true;
}

void PerceptionClient::registerTarget() { sendCommand("register\n"); }
void PerceptionClient::resetTarget()    { sendCommand("reset\n"); }

void PerceptionClient::sendCommand(const char *cmd) {
    if (m_sock.state() != QAbstractSocket::ConnectedState) {
        setStatus(QStringLiteral("연결되지 않아 명령을 보내지 못했습니다."));
        return;
    }
    m_sock.write(cmd);
}

void PerceptionClient::setStatus(const QString &s) {
    if (m_statusText == s) return;
    m_statusText = s;
    emit statusTextChanged();
}

QImage PerceptionImageProvider::requestImage(const QString &id, QSize *size, const QSize &) {
    Q_UNUSED(id)                          // id 는 캐시 무력화용 카운터라 값 자체는 안 쓴다
    QImage img = m_client->latestFrame();
    if (size) *size = img.size();
    return img;
}
