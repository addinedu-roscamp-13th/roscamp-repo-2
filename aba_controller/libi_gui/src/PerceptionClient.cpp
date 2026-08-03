#include "PerceptionClient.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QtEndian>

namespace {
constexpr int RETRY_MS = 1500;
constexpr int HEADER_BYTES = 4;
// 길이 헤더가 깨졌을 때 무한정 버퍼를 키우지 않기 위한 상한. 640px JPEG 은 보통 수십 KB 라
// 8MB 면 정상 프레임을 자를 일이 없다.
constexpr quint32 MAX_FRAME_BYTES = 8u * 1024 * 1024;
const char LIDAR_PREFIX[] = "LIDR ";
const char POSE_PREFIX[] = "POSE ";
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
    // 예약된 reset 이 **실제로 다 나갔을 때만** 예약을 지운다 (resetTarget 머리말).
    connect(&m_sock, &QTcpSocket::bytesWritten, this, [this](qint64) {
        if (m_pendingReset && m_sock.bytesToWrite() == 0)
            m_pendingReset = false;
    });
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
    // 끊겨 있는 동안 예약된 등록 초기화를 지금 보낸다 (resetTarget 머리말 참고).
    // 플래그는 여기서 안 지운다 — 실제로 다 나간 것을 확인하는 onBytesWritten 이 지운다.
    if (m_pendingReset && sendCommand("reset\n"))
        setStatus(QStringLiteral("연결됨 — 예약된 등록 초기화를 보냈습니다."));
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
        if (payload.startsWith(POSE_PREFIX))  { applyPose(payload);  continue; }

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

void PerceptionClient::applyPose(const QByteArray &payload) {
    // "POSE <JSON>" — 자세·비율·주행 상태. 필드 구성은 perception_server._pose_payload.
    // 값이 없는 항목은 JSON null 로 오거나 키가 아예 빠진다. QVariantMap 으로 그대로
    // 넘겨 QML 이 undefined 검사로 거른다 — 여기서 기본값을 채우면 "값 없음"과
    // "0" 이 구분되지 않는다(비율 0 은 실제로 의미가 다르다).
    QJsonParseError err{};
    const QJsonDocument doc =
        QJsonDocument::fromJson(payload.mid(int(sizeof(POSE_PREFIX)) - 1), &err);
    // 깨진 JSON 이면 예전 값을 그대로 둔다. 지우면 화면이 한 프레임씩 깜빡인다.
    if (err.error != QJsonParseError::NoError || !doc.isObject()) return;

    const QVariantMap next = doc.object().toVariantMap();
    if (next == m_pose) return;
    m_pose = next;
    emit poseChanged();
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

// ⚠️ [2026-08-02] **reset 은 끊겨 있어도 버리면 안 된다.**
//
//   `reset` 은 "이전 사람의 등록을 지워라"는 뜻이다. 이게 유실되면 AI 서버가 옛
//   ReID/HSV 템플릿을 그대로 들고 있어서, **다음에 추종을 켜면 등록하지 않았는데도
//   옛 사람을 알아서 따라간다**(사용자 보고 2026-08-02). 추종은 보통 세션이 끝나는
//   시점에 해제되는데, 그때가 정확히 소켓이 끊기기 쉬운 순간이다.
//
//   `register` 와 달리 지연 재전송이 안전하다: register 는 "지금 화면 가운데 사람"을
//   집는 것이라 늦게 도착하면 **엉뚱한 사람이 등록된다.** reset 은 비우는 일이라
//   늦어도 결과가 같고, 두 번 실행돼도 무해하다(멱등).
//   ⚠️ 예약을 **보내기 전에** 세운다. `write()` 는 OS 로 넘겼다는 뜻이 아니라 Qt
//      송신 큐에 넣었다는 뜻일 뿐이라, 그 직후 연결이 끊기면 바이트가 조용히 사라진다
//      (codex 지적 2026-08-02). 그래서 "썼으니 됐다"로 지우지 않고, **실제로 다 나갔을
//      때**(`bytesWritten` 에서 `bytesToWrite()==0`) 지운다. 재연결 때 한 번 더 가도
//      reset 은 멱등이라 무해하다 — 유실보다 중복이 낫다.
void PerceptionClient::resetTarget() {
    m_pendingReset = true;
    if (!sendCommand("reset\n"))
        setStatus(QStringLiteral("연결이 끊겨 등록 초기화를 예약했습니다 — 연결되면 보냅니다."));
}

bool PerceptionClient::sendCommand(const char *cmd) {
    if (m_sock.state() != QAbstractSocket::ConnectedState) {
        setStatus(QStringLiteral("연결되지 않아 명령을 보내지 못했습니다."));
        return false;
    }
    return m_sock.write(cmd) >= 0;
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
