#include "RobotController.h"

#include <QVariantMap>
#include <QTime>

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
    log(QStringLiteral("시스템 시작 — Libi GUI"));
    log(QStringLiteral("순찰 모드 진입 (대기 중)"));

    // 배터리 서서히 변동 (목): 충전중이면 +, 아니면 - / 15% 미만이면 자동충전 (SR-18)
    m_batteryTimer.setInterval(4000);
    connect(&m_batteryTimer, &QTimer::timeout, this, [this]() {
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
    else if (m == QLatin1String("guide")) setEmotion(QStringLiteral("interest"));
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
    setTaskStatus(QStringLiteral("명령 대기"));
    if (!m_patrol) { m_patrol = true; emit patrolActiveChanged(); }
    setEmotion(QStringLiteral("happy"));
    log(QStringLiteral("순찰 시작 — 순찰 모드 진입"));
    emit toast(QStringLiteral("순찰을 시작합니다."));
}

void RobotController::startGuide(const QString &destination) {
    if (m_estop) { emit toast(QStringLiteral("비상정지 상태입니다.")); return; }
    m_guideDest = destination; emit guideDestinationChanged();
    m_distance = 24.0; emit distanceToGoalChanged();
    setGuidePhase(QStringLiteral("guiding"));
    setRobotState(QStringLiteral("안내중"));
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
