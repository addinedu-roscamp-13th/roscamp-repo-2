#pragma once

#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantList>
#include <QTimer>

class QNetworkAccessManager;
class QNetworkReply;

// RobotController
// -----------------------------------------------------------------------------
// libi_gui 의 단일 백엔드 파사드. QML 에 'controller' 컨텍스트 프로퍼티로 노출된다.
//
// 실제 시스템에서 Libi GUI 의 유일한 통신 상대는 'Libi Drive Controller' 이며
// 프로토콜은 ROS2(DDS) 이다 (System Architecture 기준). 문서에 토픽/서비스 이름이
// 정의돼 있지 않으므로, 여기서는 GUI 가 필요로 하는 인터페이스를 정의하고
// 동작 확인을 위한 목(mock) 데이터로 채운다. ROS2 연동 시 이 클래스의 슬롯/시그널
// 구현부만 rclcpp 노드로 교체하면 된다. (아래 // ROS2-SEAM 주석 참조)
class RobotController : public QObject {
    Q_OBJECT

    // 화면(모드) 네비게이션
    Q_PROPERTY(QString mode READ mode WRITE setMode NOTIFY modeChanged)

    // 이 패널이 어느 로봇의 것인지 (환경변수 ROBOT_ID, 기동 시 1회 결정 → 변경 시그널 없음)
    Q_PROPERTY(QString robotId READ robotId CONSTANT)

    // 권한 / 안전
    Q_PROPERTY(bool isAdmin READ isAdmin NOTIFY isAdminChanged)
    Q_PROPERTY(bool emergencyStopped READ emergencyStopped NOTIFY emergencyStoppedChanged)

    // 로봇 상태
    Q_PROPERTY(int battery READ battery NOTIFY batteryChanged)
    Q_PROPERTY(bool charging READ charging NOTIFY chargingChanged)
    Q_PROPERTY(QString robotState READ robotState NOTIFY robotStateChanged)   // 대기/순찰/안내중/작업중/에러/충전중
    Q_PROPERTY(bool patrolActive READ patrolActive NOTIFY patrolActiveChanged)
    Q_PROPERTY(bool following READ following NOTIFY followingChanged)   // 관리자 추종 중
    Q_PROPERTY(QString emotion READ emotion WRITE setEmotion NOTIFY emotionChanged)
    Q_PROPERTY(QString taskStatus READ taskStatus NOTIFY taskStatusChanged)   // SR-14 작업 알림 문구

    // 길잡이(Guide) FSM
    Q_PROPERTY(QString guidePhase READ guidePhase NOTIFY guidePhaseChanged)   // idle/guiding/requesterLost/completed/failed/cancelled
    Q_PROPERTY(QString guideDestination READ guideDestination NOTIFY guideDestinationChanged)
    Q_PROPERTY(double distanceToGoal READ distanceToGoal NOTIFY distanceToGoalChanged)

    // 관리자 수동조작 텔레메트리
    Q_PROPERTY(double linVel READ linVel NOTIFY linVelChanged)
    Q_PROPERTY(double angVel READ angVel NOTIFY angVelChanged)
    Q_PROPERTY(double joint1 READ joint1 NOTIFY joint1Changed)
    Q_PROPERTY(double joint2 READ joint2 NOTIFY joint2Changed)
    Q_PROPERTY(double gripper READ gripper NOTIFY gripperChanged)
    Q_PROPERTY(bool led READ led NOTIFY ledChanged)
    Q_PROPERTY(QStringList logs READ logs NOTIFY logsChanged)

public:
    explicit RobotController(QObject *parent = nullptr);

    QString mode() const { return m_mode; }
    QString robotId() const { return m_robotId; }
    bool isAdmin() const { return m_isAdmin; }
    bool emergencyStopped() const { return m_estop; }
    int battery() const { return m_battery; }
    bool charging() const { return m_charging; }
    QString robotState() const { return m_robotState; }
    bool patrolActive() const { return m_patrol; }
    bool following() const { return m_following; }
    QString emotion() const { return m_emotion; }
    QString taskStatus() const { return m_taskStatus; }
    QString guidePhase() const { return m_guidePhase; }
    QString guideDestination() const { return m_guideDest; }
    double distanceToGoal() const { return m_distance; }
    double linVel() const { return m_lin; }
    double angVel() const { return m_ang; }
    double joint1() const { return m_joint1; }
    double joint2() const { return m_joint2; }
    double gripper() const { return m_gripper; }
    bool led() const { return m_led; }
    QStringList logs() const { return m_logs; }

    // --- QML 에서 호출 (Q_INVOKABLE) ---
    Q_INVOKABLE void setMode(const QString &m);
    Q_INVOKABLE bool login(const QString &pin);      // 관리자 로그인 (목 PIN: 1234)
    Q_INVOKABLE void logout();

    Q_INVOKABLE void emergencyStop();                // SR-20: 즉시 정지 + 모든 명령 무시
    Q_INVOKABLE void clearEmergencyStop();           // 관리자만 해제 가능

    // 관리자 작업상태/에러 복구 (수동조작 대체)
    Q_INVOKABLE void clearError();                   // 에러 상태 해제 → 대기(idle)
    Q_INVOKABLE void resetToIdle();                  // 진행 작업 취소 후 대기(idle) 복귀
    Q_INVOKABLE void startPatrol();                  // 대기(idle) → 순찰 시작
    Q_INVOKABLE void startAdminFollow();             // 관리자 추종 시작 (FMS 승인 후)
    Q_INVOKABLE void stopAdminFollow();              // 관리자 추종 종료 (FMS 에 해제 보고)

    // 길잡이
    Q_INVOKABLE void startGuide(const QString &destination);
    Q_INVOKABLE void cancelGuide();

    // 친밀감(SR-17) / 표정
    Q_INVOKABLE void setEmotion(const QString &e);
    Q_INVOKABLE void waveHand();                     // 손인사
    Q_INVOKABLE void bow();                           // 배꼽인사

    // 관리자 수동조작 (SR-21)
    Q_INVOKABLE void drive(double lin, double ang);
    Q_INVOKABLE void stopDrive();
    Q_INVOKABLE void setJoint1(double v);
    Q_INVOKABLE void setJoint2(double v);
    Q_INVOKABLE void setGripper(double v);
    Q_INVOKABLE void setLed(bool on);
    Q_INVOKABLE void buzz();

    // 데이터 조회 (실제로는 Drive Controller→ABA Service 경유; 여기선 목)
    Q_INVOKABLE QVariantList facilities() const;     // 시설물 목록 (+지도 좌표)
    Q_INVOKABLE QVariantList searchBooks(const QString &query, const QString &category, bool onlyAvailable) const;
    Q_INVOKABLE QVariantList searchFacilities(const QString &query) const;
    Q_INVOKABLE QVariantList recommend(const QString &purpose, const QString &interest) const;

signals:
    void modeChanged();
    void isAdminChanged();
    void emergencyStoppedChanged();
    void batteryChanged();
    void chargingChanged();
    void robotStateChanged();
    void patrolActiveChanged();
    void followingChanged();
    void emotionChanged();
    void taskStatusChanged();
    void guidePhaseChanged();
    void guideDestinationChanged();
    void distanceToGoalChanged();
    void linVelChanged();
    void angVelChanged();
    void joint1Changed();
    void joint2Changed();
    void gripperChanged();
    void ledChanged();
    void logsChanged();

    void faceGesture(const QString &kind);  // 얼굴 애니메이션 트리거 (wave/bow/nod)
    void toast(const QString &message);      // 일시 알림 문구

private:
    void log(const QString &line);
    // FMS 추종 승인/해제 HTTP. 응답 처리는 콜백에서 하므로 UI 스레드를 막지 않는다.
    void requestFollowGrant();
    void reportFollowRelease();
    void onFollowGrantReply(QNetworkReply *reply);
    void beginFollowing();
    void setRobotState(const QString &s);
    void setTaskStatus(const QString &s);
    void setGuidePhase(const QString &p);
    QVariantList allBooks() const;

    // 상태
    QString m_mode = "home";
    bool m_isAdmin = false;
    bool m_estop = false;
    int m_battery = 78;
    bool m_charging = false;
    QString m_robotState = QStringLiteral("순찰");
    bool m_patrol = true;
    bool m_following = false;
    QString m_emotion = QStringLiteral("happy");
    QString m_taskStatus = QStringLiteral("명령 대기");
    QString m_guidePhase = "idle";
    QString m_guideDest;
    double m_distance = 0.0;
    double m_lin = 0.0, m_ang = 0.0;
    double m_joint1 = 0.0, m_joint2 = 0.0, m_gripper = 50.0;
    bool m_led = false;
    QStringList m_logs;

    // 로봇 식별 + FMS 접속 (환경변수, 기동 시 1회 — gui.sh 참조)
    QString m_robotId;
    QString m_fmsUrl;
    bool m_followPending = false;   // 승인 요청 왕복 중 (연타로 중복 요청되는 것 방지)

    QNetworkAccessManager *m_net = nullptr;

    // 목 시뮬레이션 타이머
    QTimer m_batteryTimer;   // 배터리 서서히 변동
    QTimer m_guideTimer;     // 길잡이 거리 카운트다운
};
