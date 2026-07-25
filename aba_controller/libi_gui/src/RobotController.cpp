#include "RobotController.h"
#include "RosLink.h"

#include <QVariantMap>
#include <QTime>
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QUrl>

// 종료 시 해제 보고를 기다리는 상한. 관제 기록 정리보다 창이 안 닫히는 게 더 나쁘다.
static constexpr int RELEASE_ON_EXIT_TIMEOUT_MS = 1200;

// 목 데이터 헬퍼: 책 한 권을 QVariantMap 으로
static QVariantMap makeBook(const QString &title, const QString &author,
                            const QString &call, const QString &category,
                            bool available, const QString &location) {
    QVariantMap m;
    m["title"] = title;
    m["author"] = author;
    m["call"] = call;          // 청구기호
    m["category"] = category;  // 과학/예술/문학
    m["available"] = available; // 대여 가능 여부
    m["location"] = location;  // 서가 위치
    return m;
}

static QVariantMap makeFacility(const QString &name, const QString &icon, double x, double y) {
    QVariantMap m;
    m["name"] = name;
    m["icon"] = icon;     // 이모지/표시
    m["x"] = x;           // 지도 좌표 0..1
    m["y"] = y;
    return m;
}

RobotController::RobotController(QObject *parent) : QObject(parent) {
    // 이 패널이 어느 로봇의 것인지는 셸 환경변수로 주입받는다 (libi_modes 의 FSM_ROBOT_ID 와
    // 같은 방식). gui.sh 가 채워주며, robot_id 는 FMS 승인 요청의 키라 비어 있으면 요청 자체를
    // 보내지 않는다 — 빈 값으로 보내면 FMS 가 "알 수 없는 로봇"으로만 답해 원인이 안 드러난다.
    m_robotId = qEnvironmentVariable("ROBOT_ID");
    m_fmsUrl = qEnvironmentVariable("FMS_URL", QStringLiteral("http://127.0.0.1:9001"));
    while (m_fmsUrl.endsWith(QLatin1Char('/'))) m_fmsUrl.chop(1);

    m_net = new QNetworkAccessManager(this);

    log(QStringLiteral("시스템 시작 — Libi GUI"));
    // 도메인은 GUI 가 쓰진 않지만(아직 ROS2 연동 전), 어느 로봇 패널인지 확인할 수 있게 남긴다.
    log(QStringLiteral("robot_id=%1  domain=%2  fms=%3")
            .arg(m_robotId.isEmpty() ? QStringLiteral("(미설정)") : m_robotId,
                 qEnvironmentVariable("ROS_DOMAIN_ID", QStringLiteral("(미설정)")),
                 m_fmsUrl));
    log(QStringLiteral("순찰 모드 진입 (대기 중)"));

    // 배터리 서서히 변동 (목): 충전중이면 +, 아니면 - / 15% 미만이면 자동충전 (SR-18)
    m_batteryTimer.setInterval(4000);
    connect(&m_batteryTimer, &QTimer::timeout, this, [this]() {
        if (m_rosConnected) return;   // 실제 FSM 연결 시 목 배터리·상태변경 정지
        if (m_estop) return;
        if (m_charging) {
            if (m_battery < 100) { m_battery += 1; emit batteryChanged(); }
            if (m_battery >= 95) { m_charging = false; emit chargingChanged(); setRobotState(QStringLiteral("순찰")); }
        } else {
            if (m_battery > 0) { m_battery -= 1; emit batteryChanged(); }
            if (m_battery < 15) {
                m_charging = true; emit chargingChanged();
                setRobotState(QStringLiteral("충전중"));
                log(QStringLiteral("배터리 부족(15%) — 자동 충전 이동"));
            }
        }
    });
    m_batteryTimer.start();

    // 길잡이 거리 카운트다운 (목)
    m_guideTimer.setInterval(500);
    connect(&m_guideTimer, &QTimer::timeout, this, [this]() {
        if (m_estop) return;
        if (m_guidePhase != QLatin1String("guiding")) return;
        if (m_distance > 0.0) {
            m_distance -= 0.6;
            if (m_distance < 0.0) m_distance = 0.0;
            emit distanceToGoalChanged();
        }
        if (m_distance <= 0.0) {
            setGuidePhase(QStringLiteral("completed"));     // 목적지 도착 / 안내 종료
            setTaskStatus(QStringLiteral("명령 대기"));
            setRobotState(QStringLiteral("순찰"));
            log(QStringLiteral("길잡이 완료 — 목적지 도착, 순찰 재개"));
            m_guideTimer.stop();
        }
    });
}

void RobotController::log(const QString &line) {
    const QString stamped = QTime::currentTime().toString("HH:mm:ss") + "  " + line;
    m_logs.prepend(stamped);
    while (m_logs.size() > 60) m_logs.removeLast();
    emit logsChanged();
}

void RobotController::setRobotState(const QString &s) {
    if (m_robotState == s) return;
    m_robotState = s; emit robotStateChanged();
}

void RobotController::setTaskStatus(const QString &s) {
    if (m_taskStatus == s) return;
    m_taskStatus = s; emit taskStatusChanged();
}

void RobotController::setGuidePhase(const QString &p) {
    if (m_guidePhase == p) return;
    m_guidePhase = p; emit guidePhaseChanged();
}

void RobotController::setMode(const QString &m) {
    if (m_mode == m) return;
    // 비상정지 중에는 홈/비상화면만 허용 (관리자 화면은 해제용으로 허용)
    if (m_estop && m != QLatin1String("home") && m != QLatin1String("adminLogin") && m != QLatin1String("adminControl")) {
        emit toast(QStringLiteral("비상정지 상태입니다. 먼저 해제하세요."));
        return;
    }
    m_mode = m; emit modeChanged();
    // 화면별 기본 표정
    if (m == QLatin1String("home")) setEmotion(QStringLiteral("happy"));
    else if (m == QLatin1String("guide")) {
        setEmotion(QStringLiteral("interest"));
        if (m_robotState == QStringLiteral("대기") && m_rosConnected)
            requestTransition(QStringLiteral("PATROL"));   // IDLE→INTERACTING 직접 불가, PATROL 경유 (연결됐을 때만 — 목 모드서 HTTP 안 튀게)
    }
    else if (m == QLatin1String("search")) setEmotion(QStringLiteral("thinking"));
    else if (m == QLatin1String("recommend")) setEmotion(QStringLiteral("fun"));
}

bool RobotController::login(const QString &pin) {
    if (pin == QLatin1String("1234")) {            // 목 PIN
        m_isAdmin = true; emit isAdminChanged();
        log(QStringLiteral("관리자 로그인 성공"));
        emit toast(QStringLiteral("관리자 모드 진입"));
        return true;
    }
    log(QStringLiteral("관리자 로그인 실패 (PIN 불일치)"));
    return false;
}

void RobotController::logout() {
    if (!m_isAdmin) return;
    m_isAdmin = false; emit isAdminChanged();
    log(QStringLiteral("관리자 로그아웃"));
    setMode(QStringLiteral("home"));
}

void RobotController::emergencyStop() {
    if (m_estop) return;
    m_estop = true; emit emergencyStoppedChanged();
    m_lin = 0; m_ang = 0; emit linVelChanged(); emit angVelChanged();
    if (m_guidePhase == QLatin1String("guiding")) setGuidePhase(QStringLiteral("cancelled"));
    setRobotState(QStringLiteral("에러"));
    setTaskStatus(QStringLiteral("비상정지"));
    setEmotion(QStringLiteral("sad"));
    if (m_patrol) { m_patrol = false; emit patrolActiveChanged(); }
    // 실제 FSM 도 ERROR 로 — 이게 없으면 m_rosConnected(목 해제) 상태에서 화면만 정지고
    // 로봇 FSM 은 PATROL/WORKING 인 채 계속 동작한다. force=true: 어느 상태에서든 진입.
    requestTransition(QStringLiteral("ERROR"), /*force=*/true);
    log(QStringLiteral("⛔ 비상정지 — 모든 동작 중단, 명령 무시"));
}

void RobotController::clearEmergencyStop() {
    if (!m_estop) return;
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 해제할 수 있습니다.")); return; }
    m_estop = false; emit emergencyStoppedChanged();
    setRobotState(QStringLiteral("순찰"));
    setTaskStatus(QStringLiteral("명령 대기"));
    setEmotion(QStringLiteral("happy"));
    if (!m_patrol) { m_patrol = true; emit patrolActiveChanged(); }
    log(QStringLiteral("비상정지 해제 — 정상 복귀"));
}

// ---- 관리자 작업상태/에러 복구 (관리자 수동조작 대체) ----
void RobotController::clearError() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (m_robotState != QStringLiteral("에러")) return;   // 한글 비교는 QStringLiteral (QLatin1String 은 ASCII 전용)
    if (m_estop) { clearEmergencyStop(); return; }   // 비상정지로 인한 에러는 해제 흐름으로
    setGuidePhase(QStringLiteral("idle"));
    setRobotState(QStringLiteral("대기"));
    requestTransition(QStringLiteral("IDLE"), /*force=*/true);
    setTaskStatus(QStringLiteral("명령 대기"));
    if (m_patrol) { m_patrol = false; emit patrolActiveChanged(); }
    setEmotion(QStringLiteral("happy"));
    log(QStringLiteral("에러 해제 — 대기(idle) 상태로 복귀"));
    emit toast(QStringLiteral("에러를 해제했습니다."));
}

void RobotController::resetToIdle() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (m_estop) { emit toast(QStringLiteral("비상정지 상태입니다. 먼저 에러를 해제하세요.")); return; }
    if (m_guidePhase == QLatin1String("guiding")) m_guideTimer.stop();
    if (!m_guideDest.isEmpty()) { m_guideDest.clear(); emit guideDestinationChanged(); }
    if (m_distance != 0.0) { m_distance = 0.0; emit distanceToGoalChanged(); }
    setGuidePhase(QStringLiteral("idle"));
    stopDrive();
    setRobotState(QStringLiteral("대기"));
    requestTransition(QStringLiteral("IDLE"));
    setTaskStatus(QStringLiteral("명령 대기"));
    if (m_patrol) { m_patrol = false; emit patrolActiveChanged(); }
    setEmotion(QStringLiteral("happy"));
    log(QStringLiteral("작업 초기화 — 대기(idle) 상태로 복귀"));
    emit toast(QStringLiteral("대기 상태로 복귀했습니다."));
}

void RobotController::startPatrol() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (m_estop) { emit toast(QStringLiteral("비상정지 상태입니다. 먼저 에러를 해제하세요.")); return; }
    if (m_guidePhase == QLatin1String("guiding")) { emit toast(QStringLiteral("안내 중에는 순찰을 시작할 수 없습니다.")); return; }
    setGuidePhase(QStringLiteral("idle"));
    setRobotState(QStringLiteral("순찰"));
    // 실제 FSM 도 PATROL 로 — 이게 없으면 로컬만 "순찰"이고 다음 /libi/fsm_state 발행에
    // IDLE 로 되돌아간다(그러면 IDLE 엔 ui_touch 간선이 없어 이후 터치도 거부됨).
    // IDLE→PATROL 은 수동 patrol_request 정규 간선이라 force 불필요.
    requestTransition(QStringLiteral("PATROL"));
    setTaskStatus(QStringLiteral("명령 대기"));
    if (!m_patrol) { m_patrol = true; emit patrolActiveChanged(); }
    setEmotion(QStringLiteral("happy"));
    log(QStringLiteral("순찰 시작 — 순찰 모드 진입"));
    emit toast(QStringLiteral("순찰을 시작합니다."));
}

// ---- 관리자 추종 ----
// 로봇이 로컬에서 임의로 시작하지 않고 FMS /api/robot/admin-follow/request 승인을 거친다.
// FMS 가 이 로봇이 작업 중임을 계속 알고 있어야 중단 시 task_cancelled 보고가 성립하고,
// 추종 제어 자체는 FSM 을 안 거치므로(ai_service↔로봇 직결) 이 승인 기록이 아니면 관제가
// 추종 중인 걸 알 방법이 없다.
//
// 승인 실패·통신 실패는 전부 "시작 안 함"으로 떨어진다(fail-closed). FMS 가 모르는 추종이
// 도는 것이 이 승인 절차가 막으려는 바로 그 상황이다.
void RobotController::startAdminFollow() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (m_estop) { emit toast(QStringLiteral("비상정지 상태입니다. 먼저 에러를 해제하세요.")); return; }
    if (m_robotState == QLatin1String("에러")) { emit toast(QStringLiteral("에러 상태에서는 추종을 시작할 수 없습니다.")); return; }
    if (m_following) { emit toast(QStringLiteral("이미 추종 중입니다.")); return; }
    if (m_followPending) { emit toast(QStringLiteral("승인 요청 중입니다.")); return; }
    if (m_robotId.isEmpty()) {
        log(QStringLiteral("추종 요청 불가 — ROBOT_ID 가 설정되지 않았습니다 (gui.sh 참조)"));
        emit toast(QStringLiteral("이 패널의 로봇 ID가 설정되지 않았습니다."));
        return;
    }
    requestFollowGrant();
}

void RobotController::requestFollowGrant() {
    m_followPending = true;
    log(QStringLiteral("관리자 추종 — FMS 승인 요청 (robot_id=%1)").arg(m_robotId));

    QNetworkRequest req{QUrl(m_fmsUrl + QStringLiteral("/api/robot/admin-follow/request"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    QJsonObject body;
    body[QStringLiteral("robot_id")] = m_robotId;

    QNetworkReply *reply = m_net->post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() { onFollowGrantReply(reply); });
}

void RobotController::onFollowGrantReply(QNetworkReply *reply) {
    reply->deleteLater();
    m_followPending = false;

    if (reply->error() != QNetworkReply::NoError) {
        log(QStringLiteral("추종 승인 실패 — FMS 통신 오류: %1").arg(reply->errorString()));
        emit toast(QStringLiteral("관제 서버에 연결할 수 없어 추종을 시작하지 않았습니다."));
        return;
    }

    const QJsonObject body = QJsonDocument::fromJson(reply->readAll()).object();
    if (!body.value(QStringLiteral("accepted")).toBool()) {
        // reason 은 FMS 가 판단 근거를 담아 보내준다(상태·중복 등). 그대로 보여준다.
        const QString reason = body.value(QStringLiteral("reason")).toString();
        log(QStringLiteral("추종 승인 거부 — %1").arg(reason.isEmpty() ? QStringLiteral("사유 없음") : reason));
        emit toast(reason.isEmpty() ? QStringLiteral("관제 서버가 추종을 승인하지 않았습니다.") : reason);
        return;
    }

    beginFollowing();
}

void RobotController::beginFollowing() {
    m_following = true; emit followingChanged();
    setRobotState(QStringLiteral("작업중"));
    setTaskStatus(QStringLiteral("관리자 추종 중"));
    requestTransition(QStringLiteral("WORKING"));
    log(QStringLiteral("관리자 추종 시작 — FMS 승인됨"));
    emit toast(QStringLiteral("관리자 추종을 시작합니다."));
}

// 종료는 fail-open 이다: FMS 응답을 기다리지 않고 로컬 추종을 먼저 멈춘다. 관제 서버가
// 죽었다고 해서 추종을 멈출 수 없게 되는 편이 훨씬 위험하다. 해제 보고가 실패하면 FMS 에
// grant 가 남는데, 그건 관제 화면에서 정리할 수 있는 문제라 로그로만 남긴다.
void RobotController::stopAdminFollow() {
    if (!m_isAdmin) { emit toast(QStringLiteral("관리자만 조작할 수 있습니다.")); return; }
    if (!m_following) return;
    m_following = false; emit followingChanged();
    setRobotState(QStringLiteral("대기"));
    setTaskStatus(QStringLiteral("명령 대기"));
    log(QStringLiteral("관리자 추종 종료"));
    emit toast(QStringLiteral("관리자 추종을 종료했습니다."));
    reportFollowRelease();
}

// 종료 직전에 부른다. 여기서 해제를 안 보내면 FMS 에 승인 기록이 남아, 다음에 GUI 를 다시
// 켰을 때 "이미 추종 중입니다" 로 거부당한다 — 로컬 following 은 초기화되는데 FMS 기록만
// 살아남기 때문이다. 실제로 이 상황에 걸려서 추종을 다시 시작할 수 없었다.
//
// stopAdminFollow() 를 못 쓰는 이유: 그건 비동기라 요청이 나가기 전에 이벤트 루프가 끝난다.
// 여기서는 응답까지 짧게 기다린다(종료가 몇 초씩 늘어지면 안 되므로 상한을 둔다).
void RobotController::releaseFollowOnExit() {
    if (!m_following || m_robotId.isEmpty()) return;
    m_following = false;

    QNetworkRequest req{QUrl(m_fmsUrl + QStringLiteral("/api/robot/admin-follow/release"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    QJsonObject body;
    body[QStringLiteral("robot_id")] = m_robotId;

    QNetworkReply *reply = m_net->post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    QEventLoop loop;
    QTimer::singleShot(RELEASE_ON_EXIT_TIMEOUT_MS, &loop, &QEventLoop::quit);
    connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    loop.exec();
    reply->deleteLater();
}

void RobotController::reportFollowRelease() {
    if (m_robotId.isEmpty()) return;   // 승인 자체를 받은 적이 없다

    QNetworkRequest req{QUrl(m_fmsUrl + QStringLiteral("/api/robot/admin-follow/release"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    QJsonObject body;
    body[QStringLiteral("robot_id")] = m_robotId;

    QNetworkReply *reply = m_net->post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError)
            log(QStringLiteral("추종 종료 보고 실패 — 관제에 추종 중으로 남을 수 있습니다: %1")
                    .arg(reply->errorString()));
    });
}

// 관리자 전이 요청. 검증·감사는 FMS /api/fsm/transition 이 한다(로컬은 optimistic,
// /libi/fsm_state 구독이 실제 상태로 교정). ERROR 이탈은 error_code 가 필요하므로
// error 해제는 force=true 로 보낸다.
void RobotController::requestTransition(const QString &target, bool force) {
    if (m_robotId.isEmpty()) {
        log(QStringLiteral("전이 요청 불가 — ROBOT_ID 미설정")); return;
    }
    QNetworkRequest req{QUrl(m_fmsUrl + QStringLiteral("/api/fsm/transition"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("robot_id")] = m_robotId;
    body[QStringLiteral("target_state")] = target;
    body[QStringLiteral("force")] = force;
    QNetworkReply *reply = m_net->post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, target]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError)
            log(QStringLiteral("전이 실패(%1) — %2").arg(target, reply->errorString()));
    });
}

void RobotController::startGuide(const QString &destination) {
    if (m_estop) { emit toast(QStringLiteral("비상정지 상태입니다.")); return; }
    m_guideDest = destination; emit guideDestinationChanged();
    m_distance = 24.0; emit distanceToGoalChanged();
    setGuidePhase(QStringLiteral("guiding"));
    setRobotState(QStringLiteral("안내중"));
    requestTransition(QStringLiteral("WORKING"));
    setTaskStatus(QStringLiteral("사용자 명령 수행 중"));
    setEmotion(QStringLiteral("interest"));
    log(QStringLiteral("길잡이 시작 → ") + destination);
    if (m_mode != QLatin1String("guide")) setMode(QStringLiteral("guide"));
    m_guideTimer.start();
}

void RobotController::cancelGuide() {
    if (m_guidePhase != QLatin1String("guiding")) return;
    setGuidePhase(QStringLiteral("cancelled"));
    setTaskStatus(QStringLiteral("명령 대기"));
    setRobotState(QStringLiteral("순찰"));
    if (!m_patrol) { m_patrol = true; emit patrolActiveChanged(); }
    m_guideTimer.stop();
    log(QStringLiteral("길잡이 취소 (사용자)"));
}

void RobotController::setEmotion(const QString &e) {
    if (m_emotion == e) return;
    m_emotion = e; emit emotionChanged();
}

void RobotController::waveHand() {
    setEmotion(QStringLiteral("hello"));
    emit faceGesture(QStringLiteral("wave"));
    log(QStringLiteral("손인사 👋"));
}

void RobotController::bow() {
    setEmotion(QStringLiteral("happy"));
    emit faceGesture(QStringLiteral("bow"));
    log(QStringLiteral("배꼽인사 🙇"));
}

// ---- 관리자 수동조작 (SR-21) : ROS2-SEAM (실제론 cmd_vel/관절 토픽 publish) ----
void RobotController::drive(double lin, double ang) {
    if (m_estop) return;
    if (!m_isAdmin) return;
    m_lin = lin; m_ang = ang;
    emit linVelChanged(); emit angVelChanged();
}

void RobotController::stopDrive() {
    m_lin = 0; m_ang = 0; emit linVelChanged(); emit angVelChanged();
}

void RobotController::setJoint1(double v) { if (!m_isAdmin) return; m_joint1 = v; emit joint1Changed(); }
void RobotController::setJoint2(double v) { if (!m_isAdmin) return; m_joint2 = v; emit joint2Changed(); }
void RobotController::setGripper(double v) { if (!m_isAdmin) return; m_gripper = v; emit gripperChanged(); }

void RobotController::setLed(bool on) {
    if (!m_isAdmin) return;
    m_led = on; emit ledChanged();
    log(on ? QStringLiteral("LED 켜짐") : QStringLiteral("LED 꺼짐"));
}

void RobotController::buzz() {
    if (!m_isAdmin) return;
    log(QStringLiteral("부저 울림 🔔"));
    emit toast(QStringLiteral("부저 🔔"));
}

// ---- 데이터 (목) ----
QVariantList RobotController::allBooks() const {
    QVariantList b;
    b << makeBook(QStringLiteral("코스모스"), QStringLiteral("칼 세이건"), "440.9 ㅅ", QStringLiteral("과학"), true,  QStringLiteral("A-1 서가"));
    b << makeBook(QStringLiteral("이기적 유전자"), QStringLiteral("리처드 도킨스"), "472 ㄷ", QStringLiteral("과학"), false, QStringLiteral("A-2 서가"));
    b << makeBook(QStringLiteral("시간의 역사"), QStringLiteral("스티븐 호킹"), "440 ㅎ", QStringLiteral("과학"), true,  QStringLiteral("A-2 서가"));
    b << makeBook(QStringLiteral("서양미술사"), QStringLiteral("E.H. 곰브리치"), "609 ㄱ", QStringLiteral("예술"), true,  QStringLiteral("B-1 서가"));
    b << makeBook(QStringLiteral("디자인의 디자인"), QStringLiteral("하라 켄야"), "658.4 ㅎ", QStringLiteral("예술"), true,  QStringLiteral("B-2 서가"));
    b << makeBook(QStringLiteral("데미안"), QStringLiteral("헤르만 헤세"), "853 ㅎ", QStringLiteral("문학"), true,  QStringLiteral("C-1 서가"));
    b << makeBook(QStringLiteral("1984"), QStringLiteral("조지 오웰"), "843 ㅇ", QStringLiteral("문학"), false, QStringLiteral("C-1 서가"));
    b << makeBook(QStringLiteral("토지"), QStringLiteral("박경리"), "811.3 ㅂ", QStringLiteral("문학"), true,  QStringLiteral("C-3 서가"));
    return b;
}

QVariantList RobotController::facilities() const {
    QVariantList f;
    f << makeFacility(QStringLiteral("안내 데스크"), QStringLiteral("ℹ"), 0.50, 0.85);
    f << makeFacility(QStringLiteral("화장실"), QStringLiteral("🚻"), 0.12, 0.20);
    f << makeFacility(QStringLiteral("과학 섹션"), QStringLiteral("🔬"), 0.25, 0.55);
    f << makeFacility(QStringLiteral("예술 섹션"), QStringLiteral("🎨"), 0.55, 0.50);
    f << makeFacility(QStringLiteral("문학 섹션"), QStringLiteral("📖"), 0.80, 0.55);
    f << makeFacility(QStringLiteral("열람 테이블"), QStringLiteral("🪑"), 0.50, 0.32);
    f << makeFacility(QStringLiteral("수거함"), QStringLiteral("📥"), 0.85, 0.82);
    f << makeFacility(QStringLiteral("대여 데스크"), QStringLiteral("📚"), 0.35, 0.85);
    f << makeFacility(QStringLiteral("반납 데스크"), QStringLiteral("↩"), 0.65, 0.85);
    return f;
}

QVariantList RobotController::searchBooks(const QString &query, const QString &category, bool onlyAvailable) const {
    QVariantList out;
    const QString q = query.trimmed();
    for (const QVariant &v : allBooks()) {
        const QVariantMap m = v.toMap();
        if (!category.isEmpty() && category != QStringLiteral("전체") && m["category"].toString() != category) continue;
        if (onlyAvailable && !m["available"].toBool()) continue;
        if (!q.isEmpty() &&
            !m["title"].toString().contains(q, Qt::CaseInsensitive) &&
            !m["author"].toString().contains(q, Qt::CaseInsensitive)) continue;
        out << m;
    }
    return out;
}

QVariantList RobotController::searchFacilities(const QString &query) const {
    QVariantList out;
    const QString q = query.trimmed();
    for (const QVariant &v : facilities()) {
        const QVariantMap m = v.toMap();
        if (q.isEmpty() || m["name"].toString().contains(q, Qt::CaseInsensitive)) out << m;
    }
    return out;
}

QVariantList RobotController::recommend(const QString &purpose, const QString &interest) const {
    // 관심분야 → 카테고리 매핑 (취미는 예술로 근사)
    QString cat = interest;
    if (interest == QStringLiteral("취미")) cat = QStringLiteral("예술");
    QVariantList out;
    for (const QVariant &v : allBooks()) {
        QVariantMap m = v.toMap();
        if (!cat.isEmpty() && m["category"].toString() != cat) continue;
        QString reason = (purpose == QStringLiteral("자기개발"))
                ? QStringLiteral("성장에 도움이 되는 ") + m["category"].toString() + QStringLiteral(" 추천")
                : QStringLiteral("편안하게 읽기 좋은 ") + m["category"].toString() + QStringLiteral(" 추천");
        m["reason"] = reason;
        out << m;
    }
    if (out.isEmpty()) out = allBooks().mid(0, 3);
    return out.mid(0, 4);
}

// 8종 canonical(EN) → 패널 한글 표시어휘. INTERACTING/SECURITY_PATROL/RETURNING 은
// 기존 어휘에 없어 새 라벨을 준다.
QString RobotController::mapState(const QString &c) {
    if (c == "IDLE")            return QStringLiteral("대기");
    if (c == "PATROL")          return QStringLiteral("순찰");
    if (c == "SECURITY_PATROL") return QStringLiteral("보안순찰");
    if (c == "WORKING")         return QStringLiteral("작업중");
    if (c == "INTERACTING")     return QStringLiteral("응대중");
    if (c == "CHARGING")        return QStringLiteral("충전중");
    if (c == "RETURNING")       return QStringLiteral("복귀중");
    if (c == "ERROR")           return QStringLiteral("에러");
    return c;   // 미지 상태는 원문 표시
}

void RobotController::attachRos(RosLink *ros) {
    m_ros = ros;
    connect(ros, &RosLink::fsmStateReceived, this,
        [this](QString state, double remaining, QString errorCode,
               double battery, bool docked) {
            Q_UNUSED(errorCode); Q_UNUSED(docked);
            m_rosConnected = true;
            setRobotState(mapState(state));
            const int r = static_cast<int>(remaining + 0.5);
            if (r != m_interactingRemaining) { m_interactingRemaining = r; emit interactingRemainingChanged(); }
            if (battery >= 0) {
                const int b = static_cast<int>(battery + 0.5);
                if (b != m_battery) { m_battery = b; emit batteryChanged(); }
            }
            const bool ch = (state == "CHARGING");
            if (ch != m_charging) { m_charging = ch; emit chargingChanged(); }
        });
}

// QML 전역 탭에서 호출. 매 탭이 ui_last_touch_at 발행 → 로봇 20초 타이머 리셋/유지.
// 순찰 중 첫 탭은 fleet_cmd ui_touch 로 INTERACTING 진입을 튕긴다.
void RobotController::onScreenTouch() {
    if (!m_ros) return;
    m_ros->publishTouch();
    if (m_robotState == QStringLiteral("순찰")) {
        m_ros->publishFleetCmd(QStringLiteral("{\"action\":\"ui_touch\"}"));
        log(QStringLiteral("화면 터치 — 응대 진입 요청(ui_touch)"));
    }
}
