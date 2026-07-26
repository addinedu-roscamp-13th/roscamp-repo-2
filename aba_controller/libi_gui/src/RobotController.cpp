#include "RobotController.h"
#include "RosLink.h"

#include <QVariantMap>
#include <QTime>
#include <QEventLoop>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QUrl>
#include <QUrlQuery>
#include <QDebug>

// 종료 시 해제 보고를 기다리는 상한. 관제 기록 정리보다 창이 안 닫히는 게 더 나쁘다.
static constexpr int RELEASE_ON_EXIT_TIMEOUT_MS = 1200;
// ABA Service 도서 조회 상한 — 터치 입력마다 검색이 다시 나가므로 짧게 잡는다
// (응답이 늦어도 화면이 몇 초씩 멈추면 안 된다).
static constexpr int ABA_SERVICE_TIMEOUT_MS = 1500;

// 화면 카테고리 칩은 한글, ABA Service `/api/books` 는 영문 키를 쓴다
// (books.py 의 CATEGORIES 와 반드시 같아야 한다).
static QString korToApiCategory(const QString &kor) {
    if (kor == QLatin1String("과학")) return QStringLiteral("science");
    if (kor == QLatin1String("예술")) return QStringLiteral("art");
    if (kor == QLatin1String("문학")) return QStringLiteral("literature");
    if (kor == QLatin1String("인문학")) return QStringLiteral("humanities");
    return QString();   // "전체"/빈 값 등 — 필터 없음
}

static QString apiToKorCategory(const QString &api) {
    if (api == QLatin1String("science")) return QStringLiteral("과학");
    if (api == QLatin1String("art")) return QStringLiteral("예술");
    if (api == QLatin1String("literature")) return QStringLiteral("문학");
    if (api == QLatin1String("humanities")) return QStringLiteral("인문학");
    return api;
}

// ABA Service 의 BookOut(JSON) → 화면이 쓰는 book QVariantMap.
// 청구기호(call) 는 DB 에 아직 없어 서가 위치(shelf) 로 대신한다.
static QVariantMap bookFromJson(const QJsonObject &o) {
    QVariantMap m;
    m["title"] = o.value("title").toObject().value("KR").toString();
    m["author"] = o.value("author").toString();
    m["call"] = o.value("shelf").toString();
    m["category"] = apiToKorCategory(o.value("category").toString());
    m["available"] = o.value("inStock").toBool() && !o.value("unavailable").toBool();
    m["location"] = o.value("zone").toString();
    return m;
}

// `name` 은 화면에 보여줄 표시 문구(하이라이트 비교에도 이 값을 쓴다), `waypoint` 는
// 실제 waypoint.yaml 정점 이름이다 — 둘이 다른 경우가 있다("1번 테이블" 표시 ↔
// "1번테이블" 정점, "안내데스크" 표시 ↔ "안네데스크" 정점 등). 지금은 startGuide 가
// 목(mock)이라 표시 문구만 써도 동작하지만, 나중에 실제 ROS2 내비게이션을 붙일 때는
// 반드시 `waypoint` 값으로 목적지를 잡아야 한다 — `name` 을 정점 이름인 줄 알고 그대로
// 쓰면 이 프로젝트가 이미 여러 번 겪은 "화면 이름과 실제 정점 이름이 어긋나는" 버그가
// 똑같이 재현된다.
// `pillName` 은 지도 위 알약에만 쓰는 짧은 표기다(비우면 `name` 그대로) — 칩 목록·
// 안내 시작·하이라이트 비교는 전부 `name`(전체 표기)을 그대로 쓴다. 알약은 글자 폭만큼
// 스스로 커지는 자동 크기라, "1번 테이블"처럼 긴 이름이 오른쪽 끝에서 지도 밖으로
// 삐져나가는 걸 막는 용도다(실측 확인됨).
static QVariantMap makeFacility(const QString &name, const QString &waypoint,
                                 const QString &icon, double x, double y,
                                 const QString &pillName = QString()) {
    QVariantMap m;
    m["name"] = name;
    m["pillName"] = pillName.isEmpty() ? name : pillName;
    m["waypoint"] = waypoint;
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
    // 회원 앱(aba_service backend, 기본 :8000)과 같은 곳 — 도서 검색/추천이 여기로 붙는다.
    m_abaServiceUrl = qEnvironmentVariable("ABA_SERVICE_URL", QStringLiteral("http://127.0.0.1:8000"));
    while (m_abaServiceUrl.endsWith(QLatin1Char('/'))) m_abaServiceUrl.chop(1);

    m_net = new QNetworkAccessManager(this);

    log(QStringLiteral("시스템 시작 — Libi GUI"));
    // 도메인은 GUI 가 쓰진 않지만(아직 ROS2 연동 전), 어느 로봇 패널인지 확인할 수 있게 남긴다.
    log(QStringLiteral("robot_id=%1  domain=%2  fms=%3  aba=%4")
            .arg(m_robotId.isEmpty() ? QStringLiteral("(미설정)") : m_robotId,
                 qEnvironmentVariable("ROS_DOMAIN_ID", QStringLiteral("(미설정)")),
                 m_fmsUrl, m_abaServiceUrl));
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

// ---- 데이터 ----
// 이름·위치는 실제 로봇 내비 그래프(waypoint.yaml)와 회원 앱 지도(LibraryMap.tsx)를
// 그대로 따른다 — 예전엔 "과학 섹션"/"열람 테이블"(단수)/"대여 데스크"·"반납 데스크"(가상)처럼
// 실제 정점과 무관한 이름을 썼다. x,y 는 회원 앱과 같은 스키마 배치의 중심점(0..1)이다.
QVariantList RobotController::facilities() const {
    QVariantList f;
    f << makeFacility(QStringLiteral("화장실"), QStringLiteral("화장실"), QStringLiteral("🚻"), 0.185, 0.075);
    f << makeFacility(QStringLiteral("미술작품"), QStringLiteral("미술작품"), QStringLiteral("🖼"), 0.465, 0.075);
    f << makeFacility(QStringLiteral("수거함"), QStringLiteral("수거함"), QStringLiteral("📥"), 0.56, 0.23);
    f << makeFacility(QStringLiteral("1번 테이블"), QStringLiteral("1번테이블"), QStringLiteral("🪑"), 0.74, 0.255,
                       QStringLiteral("1번"));
    f << makeFacility(QStringLiteral("2번 테이블"), QStringLiteral("2번테이블"), QStringLiteral("🪑"), 0.87, 0.255,
                       QStringLiteral("2번"));
    f << makeFacility(QStringLiteral("예술서가"), QStringLiteral("예술서가"), QStringLiteral("🖌"), 0.13, 0.475);
    f << makeFacility(QStringLiteral("문학서가"), QStringLiteral("문학서가"), QStringLiteral("📖"), 0.13, 0.755);
    f << makeFacility(QStringLiteral("과학 서가"), QStringLiteral("과학-인문학서가"), QStringLiteral("🔬"), 0.37, 0.405);
    f << makeFacility(QStringLiteral("인문학서가"), QStringLiteral("과학-인문학서가"), QStringLiteral("🎓"), 0.47, 0.63);
    f << makeFacility(QStringLiteral("출입구"), QStringLiteral("도서관출입구"), QStringLiteral("🚪"), 0.86, 0.54);
    f << makeFacility(QStringLiteral("안내데스크"), QStringLiteral("안네데스크"), QStringLiteral("ℹ"), 0.815, 0.925);
    return f;
}

// ABA Service 에 GET 하나를 동기로 묻는다. QML 의 검색 결과 프로퍼티가 동기 호출
// (`Q_INVOKABLE ... const`)로 짜여 있어, 여기만 비동기로 바꾸면 화면 쪽도 다시 짜야
// 한다 — releaseFollowOnExit() 과 같은 방식(QEventLoop + 타임아웃)으로 짧게 기다린다.
// 실패/타임아웃이면 빈 QJsonDocument 를 돌려주고, 호출부는 그걸 빈 목록으로 받는다
// (doc.array() 는 무효 문서에도 빈 배열을 준다 — 별도 널 체크가 필요 없다).
QJsonDocument RobotController::httpGetJson(const QString &path, const QUrlQuery &query) const {
    QUrl url(m_abaServiceUrl + path);
    url.setQuery(query);
    QNetworkReply *reply = m_net->get(QNetworkRequest(url));

    QEventLoop loop;
    QTimer::singleShot(ABA_SERVICE_TIMEOUT_MS, &loop, &QEventLoop::quit);
    connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    loop.exec();

    QJsonDocument doc;
    if (!reply->isFinished()) {
        reply->abort();   // 타임아웃 — 응답을 안 기다리고 정리
        qWarning() << "[aba-service] GET" << path << "timed out";
    } else if (reply->error() == QNetworkReply::NoError) {
        doc = QJsonDocument::fromJson(reply->readAll());
    } else {
        qWarning() << "[aba-service] GET" << path << "failed:" << reply->errorString();
    }
    reply->deleteLater();
    return doc;
}

// 비동기 GET — id 를 즉시 반환하고(네트워크 응답은 안 기다림), 응답이 오면(성공/실패/
// 타임아웃 어느 경로든) onReady 를 정확히 한 번 부른다. 타임아웃은 abort() 로 처리하는데,
// abort() 자체가 finished() 를 내보내므로 정리 경로가 하나로 합쳐진다(성공/실패/타임아웃을
// 따로 처리할 필요가 없다). connect 의 context 를 this 로 줘서, RobotController 가 먼저
// 죽으면(main.cpp 에서 스택 객체) Qt 가 이 연결을 자동 해제 — 죽은 this 를 참조하는 사고를 막는다.
int RobotController::httpGetAsync(const QString &path, const QUrlQuery &query,
                                   std::function<void(int, bool, const QJsonDocument &)> onReady) {
    const int id = m_nextRequestId++;
    QUrl url(m_abaServiceUrl + path);
    url.setQuery(query);
    QNetworkReply *reply = m_net->get(QNetworkRequest(url));

    QTimer::singleShot(ABA_SERVICE_TIMEOUT_MS, reply, [reply]() {
        if (!reply->isFinished()) reply->abort();
    });

    connect(reply, &QNetworkReply::finished, this, [this, reply, path, id, onReady]() {
        const bool ok = reply->error() == QNetworkReply::NoError;
        QJsonDocument doc;
        if (ok) doc = QJsonDocument::fromJson(reply->readAll());
        else qWarning() << "[aba-service] GET" << path << "failed:" << reply->errorString();
        reply->deleteLater();
        onReady(id, ok, doc);
    });
    return id;
}

// searchBooks() 와 같은 조회, 화면 프로퍼티 바인딩에서 직접 부르지 않도록 비동기로 분리한
// 버전. SearchScreen.qml 전용 — onlyAvailable 필터도 동일하게 클라이언트에서 마저 거른다.
int RobotController::searchBooksAsync(const QString &query, const QString &category, bool onlyAvailable) {
    QUrlQuery q;
    if (!query.trimmed().isEmpty()) q.addQueryItem(QStringLiteral("q"), query.trimmed());
    const QString apiCat = korToApiCategory(category);
    if (!apiCat.isEmpty()) q.addQueryItem(QStringLiteral("category"), apiCat);
    q.addQueryItem(QStringLiteral("limit"), QStringLiteral("30"));

    return httpGetAsync(QStringLiteral("/api/books"), q,
        [this, onlyAvailable](int id, bool ok, const QJsonDocument &doc) {
            QVariantList out;
            if (ok) {
                for (const QJsonValue &v : doc.array()) {
                    QVariantMap m = bookFromJson(v.toObject());
                    if (onlyAvailable && !m["available"].toBool()) continue;
                    out << m;
                }
            }
            emit searchBooksReady(id, out, ok);
        });
}

// recommend() 와 같은 조회의 비동기 버전. RecommendScreen.qml 전용.
int RobotController::recommendAsync(const QString &purpose, const QString &interest) {
    QString cat = interest;
    if (interest == QStringLiteral("취미")) cat = QStringLiteral("예술");
    QUrlQuery q;
    const QString apiCat = korToApiCategory(cat);
    if (!apiCat.isEmpty()) q.addQueryItem(QStringLiteral("category"), apiCat);
    q.addQueryItem(QStringLiteral("limit"), QStringLiteral("4"));

    return httpGetAsync(QStringLiteral("/api/books/recommend"), q,
        [this, purpose](int id, bool ok, const QJsonDocument &doc) {
            QVariantList out;
            if (ok) {
                for (const QJsonValue &v : doc.array()) {
                    QVariantMap m = bookFromJson(v.toObject());
                    const QString category = m["category"].toString();
                    m["reason"] = (purpose == QStringLiteral("자기개발"))
                            ? QStringLiteral("성장에 도움이 되는 ") + category + QStringLiteral(" 추천")
                            : QStringLiteral("편안하게 읽기 좋은 ") + category + QStringLiteral(" 추천");
                    out << m;
                }
            }
            emit recommendReady(id, out, ok);
        });
}

// ABA Service 는 대여 가능 여부로 거르는 파라미터가 따로 없어 클라이언트에서 마저 거른다.
QVariantList RobotController::searchBooks(const QString &query, const QString &category, bool onlyAvailable) const {
    QUrlQuery q;
    if (!query.trimmed().isEmpty()) q.addQueryItem(QStringLiteral("q"), query.trimmed());
    const QString apiCat = korToApiCategory(category);
    if (!apiCat.isEmpty()) q.addQueryItem(QStringLiteral("category"), apiCat);
    q.addQueryItem(QStringLiteral("limit"), QStringLiteral("30"));

    QVariantList out;
    const QJsonDocument doc = httpGetJson(QStringLiteral("/api/books"), q);
    for (const QJsonValue &v : doc.array()) {
        QVariantMap m = bookFromJson(v.toObject());
        if (onlyAvailable && !m["available"].toBool()) continue;
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

// LiBi AI(회원 앱 챗봇)가 그라운딩에 쓰는 것과 같은 엔드포인트 — 대화형 LLM 전체를
// 다시 구현하는 대신, 그 추천이 근거로 삼는 실제 DB 조회만 그대로 재사용한다.
// 결과가 없으면 서버가 이미 재고 있는 책으로 대체해 내려준다(fallback 클라이언트에 없음).
QVariantList RobotController::recommend(const QString &purpose, const QString &interest) const {
    // 관심분야 → 카테고리 매핑 (취미는 예술로 근사)
    QString cat = interest;
    if (interest == QStringLiteral("취미")) cat = QStringLiteral("예술");

    QUrlQuery q;
    const QString apiCat = korToApiCategory(cat);
    if (!apiCat.isEmpty()) q.addQueryItem(QStringLiteral("category"), apiCat);
    q.addQueryItem(QStringLiteral("limit"), QStringLiteral("4"));

    QVariantList out;
    const QJsonDocument doc = httpGetJson(QStringLiteral("/api/books/recommend"), q);
    for (const QJsonValue &v : doc.array()) {
        QVariantMap m = bookFromJson(v.toObject());
        const QString category = m["category"].toString();
        m["reason"] = (purpose == QStringLiteral("자기개발"))
                ? QStringLiteral("성장에 도움이 되는 ") + category + QStringLiteral(" 추천")
                : QStringLiteral("편안하게 읽기 좋은 ") + category + QStringLiteral(" 추천");
        out << m;
    }
    return out;
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
