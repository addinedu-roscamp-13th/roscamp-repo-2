#pragma once
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <functional>
#include <memory>
#include <thread>

namespace rclcpp { class Node; }

// ROS2-SEAM: libi_gui 와 로봇 FSM 을 잇는 rclcpp 노드. Qt 이벤트루프와 별도 스레드에서
// spin 하고, 수신은 시그널로(큐잉되어 UI 스레드에서 처리) 넘긴다.
class RosLink : public QObject {
    Q_OBJECT
public:
    explicit RosLink(QObject *parent = nullptr);
    ~RosLink() override;

    /** 패널 요청 통로가 살아 있는지 — 브릿지(서버)가 구독 중이면 참.
     *  거짓이면 호출측이 HTTP 로 폴백한다. */
    bool panelLinkUp() const;

    /** FMS 에 요청을 보내고 답을 기다린다 — `/panel_request` → `/panel_result`.
     *
     *  HTTP 와 달리 **토픽은 아무도 안 듣고 있어도 조용히 성공한다.** 그래서 타임아웃이
     *  없으면 fail-closed 호출부(길잡이·추종 승인)가 "요청 중" 화면에 영영 갇힌다.
     *  응답이 없으면 `cb(false, {})` 로 부른다 — 기존 통신오류 처리와 같은 모양이다.
     *
     *  cb 는 UI 스레드에서 불린다. */
    void panelRequest(const QString &op, QJsonObject args,
                      std::function<void(bool, QJsonObject)> cb, int timeoutMs = 5000);

public slots:
    void publishTouch();                        // ui_last_touch_at (Float64, 값 무의미 — 수신측이 스탬프)
    void publishFleetCmd(const QString &json);  // /fleet_cmd (String JSON, 예: {"action":"ui_touch"})

    /** 비상정지 — 켜면 `/cmd_vel_stop` 으로 **0 을 20Hz 로 계속** 낸다(twist_mux priority 255).
     *
     *  왜 계속 내나: twist_mux 는 timeout(0.5s) 안에 안 오는 입력을 없는 것으로 친다.
     *  한 번만 쏘면 0.5초 뒤 추종·nav2 가 다시 이긴다. 반대로 끄면 그냥 발행을 멈추면 된다.
     *  ⚠️ 그래서 이건 **소프트웨어 정지**다 — 패널이 죽으면 정지도 풀린다.
     *     하드웨어 차단은 별개로 있어야 한다. */
    void setEmergencyStop(bool on);

signals:
    void fsmStateReceived(QString currentState, double remainingSec,
                          QString errorCode, double batteryPercent, bool docked);
    /** /libi/fsm_state 의 `person_blocked` — 주행 중 앞을 막는 사람 때문에 서 있는가.
     *  `fsmStateReceived` 에 인자를 더하지 않고 따로 낸다 — 그 시그니처를 건드리면
     *  기존 연결·시험이 전부 같이 바뀐다. */
    void personBlockedReceived(bool blocked);
    /** /libi/fsm_state 의 `front_person_size` — 앞캠에 보이는 가장 큰 사람의
     *  sqrt(area) px(320 기준). 0 = 안 보임. 임계값을 실기에서 맞추려고 화면에 띄운다. */
    void frontPersonSizeReceived(double px);
    /** /libi/fsm_state 의 `person_block_in` — 사람 때문에 정점 차단을 알리기까지 남은 초.
     *  음수면 세고 있지 않다. 관제에서 재계획이 사람 때문인지 지연 때문인지 가르는 값. */
    void personBlockInReceived(double sec);
    /** /libi/fsm_state 의 `person_block_seq`/`person_block_node` — 사람 차단을 **알린**
     *  누적 횟수와 그 정점. 늘어난 순간이 곧 "사람 때문에 재탐색을 요청했다" 다.
     *  레벨(`person_blocked`)이 아니라 횟수인 이유는 임계 근처 깜빡임 때문이다. */
    void personBlockReported(int seq, int node);
    /** /libi/fsm_state 의 `chase_state` — 야간순찰 IntruderChase 리프의 내부 상태
     *  ("idle"/"chasing"/"release"/"backoff"). `current_state`("보안순찰")와 별개로,
     *  SECURITY_PATROL 안에서 도는 하위 상태다. 패널이 "추종중"/"유실" 을 그린다. */
    void chaseStateReceived(QString state);
    /** /libi/replan_reason — FMS 가 내려보내는 **재계획 사유**(String JSON `{seq, reason}`).
     *  사람 사유는 로봇이 스스로 알지만(`person_block_seq`), 지연·차단해제·다른 로봇
     *  때문에 난 재계획은 fleet_node 만 안다. 그걸 패널 기록에 남기려고 받는다. */
    void replanReasonReceived(int seq, QString reason);
    /** `shelf_dock_status` — 정밀 도킹/역순 복귀의 단계·거리 JSON. */
    void shelfDockStatusReceived(QString json);
    /** /amcl_pose — map 프레임 좌표[m]와 yaw[rad]. 그림 좌표 변환은 RobotController 가 한다. */
    void poseReceived(double x, double y, double yawRad);
    /** odom — pinky_bringup 이 엔코더로 계산해 30Hz 로 쏘는 실측 바퀴 속도[m/s, rad/s].
     *  AI 서버가 보내는 cmd_vel 미리보기(POSE.linearX/angularZ)와 달리 이게 실제 바퀴다. */
    void odomReceived(double linearX, double angularZ);
    /** 내부용 — ROS spin 스레드에서 UI 스레드로 넘기는 다리(큐잉 연결). 직접 쓰지 않는다. */
    void panelResultArrived(QString id, QJsonObject body);

private slots:
    void onPanelResult(QString id, QJsonObject body);

private:
    struct Impl;
    std::unique_ptr<Impl> d_;
    std::thread spin_;
};
