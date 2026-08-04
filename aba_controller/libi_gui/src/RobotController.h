#pragma once

#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantList>
#include <QTimer>
#include <QElapsedTimer>
#include <functional>

class QNetworkAccessManager;
class QNetworkReply;
class QJsonDocument;
class QUrlQuery;
class RosLink;

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
    Q_PROPERTY(int interactingRemaining READ interactingRemaining NOTIFY interactingRemainingChanged)
    // 로봇 FSM 과 실제로 붙어 있는가(= /libi/fsm_state 를 한 번이라도 받았는가).
    // 화면이 "화면을 누르면 계속 응대해요" 같은 **로봇이 해줘야 하는 약속**을 말하기 전에
    // 이걸 봐야 한다 — 목 모드에서는 터치를 발행할 상대가 없어 그 말이 거짓이 된다
    // (onScreenTouch 는 m_ros 가 없으면 그냥 돌아간다).
    Q_PROPERTY(bool rosConnected READ rosConnected NOTIFY rosConnectedChanged)

    // 지도 위 로봇 위치 — 안내판 그림 안의 0..1 비율과, 위를 향한 마커에 줄 회전각[도].
    // `poseValid` 가 false 면 화면은 마커를 **안 그린다**: 위치를 모를 때 마지막으로 알던
    // 자리를 계속 보여주면 "로봇이 저기 있다"는 거짓말이 된다. AMCL 이 죽어도 마지막 값은
    // 남으므로 도착 여부가 아니라 **수신 신선도**로 판정한다(POSE_TTL_MS).
    Q_PROPERTY(bool poseValid READ poseValid NOTIFY poseChanged)
    Q_PROPERTY(double mapX READ mapX NOTIFY poseChanged)          // 그림 가로 0..1
    Q_PROPERTY(double mapY READ mapY NOTIFY poseChanged)          // 그림 세로 0..1
    Q_PROPERTY(double mapHeadingDeg READ mapHeadingDeg NOTIFY poseChanged)

    // odom 실측 바퀴 속도[m/s, rad/s] — pinky_bringup 이 엔코더로 계산해 30Hz 로 쏜다.
    // AI 서버가 화면에 주던 cmd_vel 미리보기(bbox 만 보고 계산, 실제 바퀴와 무관)를 대신한다.
    Q_PROPERTY(double odomLinVel READ odomLinVel NOTIFY odomChanged)
    Q_PROPERTY(double odomAngVel READ odomAngVel NOTIFY odomChanged)
    Q_PROPERTY(bool patrolActive READ patrolActive NOTIFY patrolActiveChanged)
    Q_PROPERTY(bool following READ following NOTIFY followingChanged)   // 관리자 추종 중
    // 주행 중 앞을 막는 사람 때문에 서 있는가 (/libi/fsm_state 의 person_blocked).
    // 비상정지·에러·명령 대기 같은 다른 정지는 여기 안 들어온다.
    Q_PROPERTY(bool personBlocked READ personBlocked NOTIFY personBlockedChanged)
    // 앞캠에 보이는 가장 큰 사람의 크기(sqrt(area) px @320). 0 = 안 보임.
    // 임계값(person_stop_size)을 실기에서 맞추려면 지금 몇 px 인지가 보여야 한다.
    Q_PROPERTY(double frontPersonSize READ frontPersonSize NOTIFY frontPersonSizeChanged)
    // 사람 때문에 경로 재탐색이 걸리기까지 남은 초. 음수 = 세고 있지 않음.
    // 관제 화면에서 재계획이 **사람 때문인지 지연 때문인지** 구분하려고 띄운다.
    Q_PROPERTY(double personBlockIn READ personBlockIn NOTIFY personBlockInChanged)
    // 야간순찰 IntruderChase 하위 상태("idle"/"chasing"/"release"/"backoff").
    // FMS 상위 상태(robotState="보안순찰")와 별개 — 그 안에서 도는 값이다.
    Q_PROPERTY(QString chaseState READ chaseState NOTIFY chaseStateChanged)
    Q_PROPERTY(QString emotion READ emotion WRITE setEmotion NOTIFY emotionChanged)
    Q_PROPERTY(QString taskStatus READ taskStatus NOTIFY taskStatusChanged)   // SR-14 작업 알림 문구

    // 길잡이(Guide) FSM
    Q_PROPERTY(QString guidePhase READ guidePhase NOTIFY guidePhaseChanged)   // idle/guiding/requesterLost/completed/failed/cancelled
    Q_PROPERTY(QString guideDestination READ guideDestination NOTIFY guideDestinationChanged)
    Q_PROPERTY(double distanceToGoal READ distanceToGoal NOTIFY distanceToGoalChanged)
    // 등록 단계: idle / registering(영상 보고 탭) / calibrating(자세 측정) / ready
    Q_PROPERTY(QString guideRegPhase READ guideRegPhase NOTIFY guideRegPhaseChanged)
    // 지금 패널에 보이는 카메라. 전환하면 시점이 뒤집혀 보이므로 화면에 라벨로 띄운다.
    Q_PROPERTY(QString currentCamera READ currentCamera NOTIFY currentCameraChanged)

    // 관리자 수동조작 텔레메트리
    Q_PROPERTY(double linVel READ linVel NOTIFY linVelChanged)
    Q_PROPERTY(double angVel READ angVel NOTIFY angVelChanged)
    Q_PROPERTY(double joint1 READ joint1 NOTIFY joint1Changed)
    Q_PROPERTY(double joint2 READ joint2 NOTIFY joint2Changed)
    Q_PROPERTY(double gripper READ gripper NOTIFY gripperChanged)
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
    int interactingRemaining() const { return m_interactingRemaining; }
    bool rosConnected() const { return m_rosConnected; }
    bool poseValid() const { return m_poseValid; }
    double mapX() const { return m_mapX; }
    double mapY() const { return m_mapY; }
    double mapHeadingDeg() const { return m_mapHeadingDeg; }
    double odomLinVel() const { return m_odomLinVel; }
    double odomAngVel() const { return m_odomAngVel; }
    /** 현재 pose 와 목적지 정점(map 좌표) 사이 거리[m]. pose 가 없으면 -1. */
    double distanceTo(double x, double y) const;
    /** 화면 표시명("과학 서가") → 실제 정점 이름("과학-인문학서가"). 없으면 빈 문자열. */
    QString waypointOf(const QString &displayName) const;
    bool patrolActive() const { return m_patrol; }
    bool following() const { return m_following; }
    bool personBlocked() const { return m_personBlocked; }
    double frontPersonSize() const { return m_frontPersonSize; }
    QString chaseState() const { return m_chaseState; }
    double personBlockIn() const { return m_personBlockIn; }
    QString emotion() const { return m_emotion; }
    QString taskStatus() const { return m_taskStatus; }
    QString guidePhase() const { return m_guidePhase; }
    QString guideRegPhase() const { return m_guideRegPhase; }
    QString currentCamera() const { return m_currentCamera; }
    QString guideDestination() const { return m_guideDest; }
    double distanceToGoal() const { return m_distance; }
    double linVel() const { return m_lin; }
    double angVel() const { return m_ang; }
    double joint1() const { return m_joint1; }
    double joint2() const { return m_joint2; }
    double gripper() const { return m_gripper; }
    QStringList logs() const { return m_logs; }

    // --- QML 에서 호출 (Q_INVOKABLE) ---
    Q_INVOKABLE void setMode(const QString &m);
    Q_INVOKABLE bool login(const QString &pin);      // 관리자 로그인 (목 PIN: 1234)
    Q_INVOKABLE void logout();
    Q_INVOKABLE void clearLogs();                    // 최근 기록 비우기 (화면 전용, 로봇과 무관)

    Q_INVOKABLE void emergencyStop();                // SR-20: 즉시 정지 + 모든 명령 무시
    Q_INVOKABLE void clearEmergencyStop();           // 관리자만 해제 가능

    // 관리자 작업상태/에러 복구 (수동조작 대체)
    Q_INVOKABLE void clearError();                   // 에러 상태 해제 → 대기(idle)
    Q_INVOKABLE void resetToIdle();                  // 진행 작업 취소 후 대기(idle) 복귀
    Q_INVOKABLE void startPatrol();                  // 대기(idle) → 순찰 시작
    Q_INVOKABLE void startAdminFollow();             // 관리자 추종 시작 (FMS 승인 후)
    Q_INVOKABLE void stopAdminFollow();              // 관리자 추종 종료 (FMS 에 해제 보고)
    void releaseFollowOnExit();                      // 종료 직전 해제 (main.cpp 의 aboutToQuit)

    void attachRos(RosLink *ros);                    // RosLink 의 fsm_state 시그널을 배선 (Task 5)

    // QML 전역 탭에서 호출. ui_last_touch_at 발행 + 순찰 중이면 ui_touch fleet_cmd (Task 6)
    Q_INVOKABLE void onScreenTouch();

    // 길잡이
    Q_INVOKABLE void startGuide(const QString &destination);
    Q_INVOKABLE void cancelGuide();
    // 등록 화면 감시 세션 — **패널이 직접 연다.**
    //
    // 등록하려면 카메라 영상이 필요한데, 카메라는 세션이 켜고 세션은 등록이 끝나야
    // 시작된다. 게다가 등록 시점의 미션 상태는 INTERACTING 이라 WORKING 브랜치의
    // GuideExec 은 tick 되지도 않는다. 그래서 여기서 `/fleet_cmd{watch}` 를 낸다.
    Q_INVOKABLE void startGuideRegistration();
    Q_INVOKABLE void cancelGuideRegistration();
    Q_INVOKABLE void confirmGuideRegistration();   // 이용자를 탭해 등록이 끝났다
    //: 학습 3초 동안 **주인이 실제로 잡혔다**고 패널이 알린다. 근거는 AI 서버가 POSE
    //: JSON 에 싣는 `isOwner` 이고, 이 신호가 없으면 `confirmGuideRegistration` 의
    //: 타이머가 등록을 실패로 처리한다 — 그 주석에 이유가 있다.
    Q_INVOKABLE void reportRegistrationOwnerSeen();
    // ⚠️ [2026-08-02] **끝난 안내의 단계를 되감는다 — 안 되감으면 길잡이를 두 번 못 쓴다.**
    //
    //   `guidePhase` 는 안내가 끝나도 `completed`(또는 `failed`/`cancelled`)로 남는다.
    //   그 상태로 길잡이 화면에 다시 들어오면 `GuideScreen.qml:108` 의
    //   `guidePhase !== "completed"` 가 목적지 고르는 화면을 통째로 숨겨서
    //   **아무것도 못 고른다**(실측 2026-08-02).
    //
    //   `idle` 로 되돌리는 다른 두 곳(`clearError`·`resetToIdle`)은 **관리자 전용**이라
    //   이용자 경로에는 되감을 길이 없었다. 그래서 화면을 나가는 자리에서 되감는다.
    Q_INVOKABLE void resetGuidePhase();

private:
    bool destinationXY(const QString &displayName, double *x, double *y) const;
    void updateGuideDistance();

public:

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

    // 데이터 조회. 도서 관련(검색·추천)은 ABA Service(:8000, cb_books) 를 HTTP 로 그대로
    // 묻는다 — 지도 시설물 목록만 아직 목(mock)이다(로봇 좌표라 서버가 모른다).
    Q_INVOKABLE QVariantList facilities() const;     // 시설물 목록 (+지도 좌표)
    Q_INVOKABLE QVariantList searchBooks(const QString &query, const QString &category, bool onlyAvailable) const;
    Q_INVOKABLE QVariantList searchFacilities(const QString &query) const;
    Q_INVOKABLE QVariantList recommend(const QString &purpose, const QString &interest) const;

    // 비동기 버전 — SearchScreen/RecommendScreen 전용. QML 프로퍼티 바인딩 안에서 동기
    // HTTP(위 searchBooks/recommend, httpGetJson 의 QEventLoop)를 직접 부르면 응답을
    // 기다리는 동안 UI 스레드가 최대 ABA_SERVICE_TIMEOUT_MS 만큼 멈춘다 — 검색창 키 입력마다,
    // 추천 칩 클릭마다 이 정지가 반복된다. 대신 여기서는 요청을 던지고 즉시 id 를 돌려주고
    // (네트워크 응답은 기다리지 않는다), 응답이 오면 ...Ready 신호로 알린다. id 는
    // RobotController 수명 전체에서 유일하다(QML 쪽 Loader 가 화면을 파괴·재생성해도 화면별
    // 카운터가 아니라 이 값을 그대로 비교하면 되므로, 화면 재진입 시 재사용되는 화면-로컬
    // id와 옛 응답의 id가 우연히 같아 stale 데이터를 받아들이는 사고를 막는다).
    // zone 을 주면 그 서가 정점에 꽂힌 책만 서버가 걸러 준다(`/api/books?zone=`) —
    // 지도에서 서가를 탭했을 때 쓰는 경로다. 비우면 기존 검색 그대로.
    Q_INVOKABLE int searchBooksAsync(const QString &query, const QString &category, bool onlyAvailable,
                                     const QString &zone = QString());
    Q_INVOKABLE int recommendAsync(const QString &purpose, const QString &interest);

signals:
    // ok=false 는 "결과 없음"과 구분되는 네트워크/타임아웃 실패 — 화면이 다른 문구를 보여준다.
    void searchBooksReady(int requestId, QVariantList results, bool ok);
    void recommendReady(int requestId, QVariantList results, bool ok);
    void modeChanged();
    void isAdminChanged();
    void emergencyStoppedChanged();
    void batteryChanged();
    void chargingChanged();
    void robotStateChanged();
    void interactingRemainingChanged();
    void rosConnectedChanged();
    void poseChanged();
    void odomChanged();
    void patrolActiveChanged();
    void personBlockedChanged();
    void frontPersonSizeChanged();
    void chaseStateChanged();
    void personBlockInChanged();
    void followingChanged();
    //: 관리자가 「해제」를 누른 것이 **아니라**, 로봇이 스스로 추종을 끝냈다.
    //  (사람을 못 찾아 회복이 포기했거나, 관제가 상태를 바꿨거나, 에러)
    //  화면은 이때 홈으로 돌아간다 — 조작하던 관리자는 이미 자리를 떴을 가능성이 크고,
    //  로봇도 순찰로 복귀하므로(FollowExec._release 가 PATROL 을 예약) 관리자 화면에
    //  남아 있으면 실제 상태와 화면이 어긋난다.
    void followEndedByRobot();
    void emotionChanged();
    void taskStatusChanged();
    void guidePhaseChanged();
    void guideRegPhaseChanged();
    void currentCameraChanged();
    void guideDestinationChanged();
    void distanceToGoalChanged();
    void linVelChanged();
    void angVelChanged();
    void joint1Changed();
    void joint2Changed();
    void gripperChanged();
    void logsChanged();

    void faceGesture(const QString &kind);  // 얼굴 애니메이션 트리거 (wave/bow/nod)
    void toast(const QString &message);      // 일시 알림 문구

private:
    void log(const QString &line);
    // FMS 추종 승인/해제 HTTP. 응답 처리는 콜백에서 하므로 UI 스레드를 막지 않는다.
    void requestFollowGrant();
    void reportFollowRelease();
    void requestTransition(const QString &target, bool force = false);
    void onFollowGrantReply(bool ok, const QJsonObject &body);

    // FMS 요청 — ROS2(`/panel_request`) 우선, 링크가 없으면 HTTP 로 폴백한다.
    // cb(ok, body): ok=false 는 **통신 실패/타임아웃**이고, 거절은 ok=true + body 안의
    // granted/accepted=false 다. 둘을 섞으면 "관제가 죽음" 과 "관제가 거절함" 이 같은
    // 화면으로 보여 원인을 못 찾는다.
    void fmsCall(const QString &op, const QString &httpPath, const QJsonObject &body,
                 std::function<void(bool, QJsonObject)> cb);
    void beginFollowing();
    void setRobotState(const QString &s);
    static QString mapState(const QString &canonical);   // ROS FSM canonical(EN) → 패널 한글 표시어휘
    void setTaskStatus(const QString &s);
    void finishGuideIfLeftWorking(const QString &state);
    void setGuidePhase(const QString &p);
    void setGuideRegPhase(const QString &p);
    void setCurrentCamera(const QString &c);
    void publishWatch(const QString &camera);
    QJsonDocument httpGetJson(const QString &path, const QUrlQuery &query) const;
    // GET 을 던지고 즉시 새 id 를 반환. onReady(ok, doc) 는 성공/실패/타임아웃 어느 경로든
    // 정확히 한 번, reply->deleteLater() 이후 불린다.
    int httpGetAsync(const QString &path, const QUrlQuery &query,
                      std::function<void(int requestId, bool ok, const QJsonDocument &doc)> onReady);

    // 상태
    QString m_mode = "home";
    bool m_isAdmin = false;
    bool m_estop = false;
    int m_battery = 78;
    bool m_charging = false;
    bool m_personBlocked = false;
    double m_frontPersonSize = 0.0;
    QString m_chaseState = "idle";
    double m_personBlockIn = -1.0;
    // 마지막으로 본 사람-차단 보고 번호. -1 = 아직 fsm_state 를 못 받았다.
    // 첫 수신 때는 기록을 남기지 않는다 — 로봇이 이미 여러 번 보고한 뒤에 패널을 켜면
    // 옛 사건이 "방금 일어난 일" 로 찍힌다.
    int m_personBlockSeq = -1;
    // 마지막으로 본 FMS 재계획 seq. -1 = 아직 못 받았다. 첫 수신은 기준만 잡는다 —
    // `/replan_reason` 은 래치라 패널이 늦게 떠도 마지막 값이 배달된다.
    int m_replanSeq = -1;
    QString m_robotState = QStringLiteral("순찰");
    RosLink *m_ros = nullptr;
    int m_interactingRemaining = 0;
    bool m_rosConnected = false;
    bool m_poseValid = false;
    double m_mapX = 0.5, m_mapY = 0.5, m_mapHeadingDeg = 0.0;
    double m_poseWorldX = 0.0, m_poseWorldY = 0.0;   // map 프레임 원본(거리 계산용)
    double m_odomLinVel = 0.0, m_odomAngVel = 0.0;
    QTimer m_poseFreshness;
    QTimer m_fsmFreshness;   // 상태 수신이 끊기면 rosConnected 를 되돌린다
    // FMS 가 알려준 목적지 정점 좌표(map 프레임). 남은 거리는 이것과 현재 pose 로 잰다.
    bool m_guideTargetValid = false;
    double m_guideTargetX = 0.0, m_guideTargetY = 0.0;
    QString m_guideTargetWaypoint;
    bool m_patrol = true;
    bool m_following = false;
    //: 추종을 켠 뒤 로봇이 WORKING 인 것을 **한 번이라도 봤나.**
    //
    //  아래 종료 감시가 "WORKING 을 벗어났다"로 판정하는데, 승인 직후에는 아직
    //  WORKING 이 **도착하기 전**이라 그 조건이 참이다. 그대로 두면 켜자마자 스스로
    //  해제를 보내고, FMS 가 로봇을 IDLE 로 되돌린다 — 관리자 추종이 안 되는 것처럼 보인다.
    //  (실측 2026-07-28: FMS 를 직접 부르면 WORKING 이 18초+ 유지되는데, 패널로 켜면
    //   몇 초 만에 IDLE 로 돌아왔다. 차이는 이 감시뿐이었다.)
    bool m_followSawWorking = false;
    //  ⚠️ [2026-08-02] **`m_followSawWorking` 하나에만 매달리면 안 된다.**
    //
    //  `WORKING` 상태 메시지를 한 번이라도 놓치면(브릿지 재연결·DDS 유실·패널이 늦게
    //  붙음) 이 값이 영영 false 로 남는다. 그러면 로봇이 추종을 끝내고 PATROL 로
    //  가도 종료 감시가 성립하지 않아 **홈 복귀도, 등록 초기화(resetTarget)도 둘 다
    //  건너뛴다** — 사용자가 겪은 "다시 추종 누르면 옛 사람이 그대로 등록돼 있다"와
    //  "회복 BT 가 끝나도 홈으로 안 간다"가 같은 원인이다(codex 검증 2026-08-02).
    //
    //  그래서 시간 기반 폴백을 같이 둔다: 시작 후 `kFollowGraceMs` 가 지나면
    //  WORKING 을 못 봤더라도 non-WORKING 상태를 종료로 친다. 유예가 있으므로
    //  "승인 직후 스스로 해제" 회귀(2026-07-28)는 그대로 막힌다.
    QElapsedTimer m_followSince;
    static constexpr qint64 kFollowGraceMs = 5000;
    // 안내 완료 상태가 홈 화면으로 바뀌는 바로 그 입력은 전역 MouseArea에도 남아 있을 수 있다.
    // 그 잔여 탭으로 PATROL -> INTERACTING 을 즉시 되돌리지 않도록 짧게 차단한다.
    QElapsedTimer m_guideEndedAt;
    static constexpr qint64 kGuideEndTouchDebounceMs = 2000;
    //: 등록 직후 앞캠에 머물며 갤러리를 채우는 시간(ms).
    //: AI 쪽 `REGISTRATION_LEARN_SEC`(3.0초)와 **같은 값이어야** 한다 — 짧으면 학습이
    //: 덜 끝난 채 뒷캠으로 넘어가 owner 를 못 잡는다(`confirmGuideRegistration` 주석).
    static constexpr int kGuideLearnMs = 3000;
    //: 이번 안내에서 WORKING 상태를 실제로 본 적이 있나. 안 봤으면 끝을 판정하지
    //: 않는다 — 근거는 `finishGuideIfLeftWorking` 의 ⚠️ 주석(전이 직후 경쟁).
    bool m_guideSawWorking = false;
    //  ⚠️ [2026-08-02] **추종과 같은 폴백을 길잡이에도 둔다.**
    //
    //  `m_guideSawWorking` 하나만 보면, WORKING 메시지를 한 번이라도 놓쳤을 때
    //  (브릿지 재연결·DDS 유실·패널이 늦게 붙음) 이 값이 영영 false 로 남아
    //  `finishGuideIfLeftWorking` 이 **`resetGuidePhase()` 와 `setMode("home")` 을
    //  둘 다 건너뛴다** — 로봇은 안내를 끝냈는데 패널만 안내 화면에 갇힌다.
    //  추종은 `kFollowGraceMs` 로 이미 막고 있었고, 길잡이만 안 막혀 있었다.
    //
    //  유예가 추종(5초)보다 긴 이유: 길잡이는 승인이 **FMS 를 거쳐** 돌아온 뒤
    //  로봇이 WORKING 으로 전이하므로 왕복이 한 단계 더 있다. 짧게 잡으면
    //  "안내가 시작하자마자 화면만 홈으로 튕기는" 2026-08-02 회귀가 되살아난다.
    QElapsedTimer m_guideSince;
    //  상수가 아니라 멤버인 이유: `QElapsedTimer` 는 과거로 되돌릴 수 없어서, 상수로
    //  두면 이 분기를 시험하려면 20초를 **실제로** 기다려야 한다. 그러면 아무도 안
    //  쓰는 시험이 되고, 폴백 없는 분기가 조용히 썩는다(지금 고치는 게 정확히 그
    //  부류다). 시험만 0 으로 줄인다 — 배포 경로에서는 아무도 안 건드린다.
    qint64 m_guideGraceMs = 20000;
    //: 이번 등록의 학습 창에서 주인을 한 번이라도 봤나. 못 봤으면 등록을 되돌린다.
    bool m_regOwnerSeen = false;
    QString m_emotion = QStringLiteral("happy");
    QString m_taskStatus = QStringLiteral("명령 대기");
    QString m_guidePhase = "idle";
    QString m_guideRegPhase = "idle";
    QString m_currentCamera = "none";
    QString m_watchSessionId;      // 우리가 연 감시 세션. stop 은 이 id 로만 낸다
    QTimer m_watchLease;           // 패널이 살아 있음을 알린다(죽으면 세션이 스스로 닫힌다)
    QString m_guideDest;
    double m_distance = 0.0;
    double m_lin = 0.0, m_ang = 0.0;
    double m_joint1 = 0.0, m_joint2 = 0.0, m_gripper = 50.0;
    QStringList m_logs;

    // 로봇 식별 + FMS 접속 (환경변수, 기동 시 1회 — gui.sh 참조)
    QString m_robotId;
    QString m_fmsUrl;
    QString m_abaServiceUrl;        // 도서 검색/추천 — 회원 앱과 같은 ABA Service 백엔드
    bool m_followPending = false;   // 승인 요청 왕복 중 (연타로 중복 요청되는 것 방지)

    QNetworkAccessManager *m_net = nullptr;
    int m_nextRequestId = 1;   // 0 은 QML 쪽 "대기 중인 요청 없음" 센티널

    // 목 시뮬레이션 타이머
    QTimer m_batteryTimer;   // 배터리 서서히 변동
    QTimer m_guideTimer;     // 길잡이 거리 카운트다운
};
