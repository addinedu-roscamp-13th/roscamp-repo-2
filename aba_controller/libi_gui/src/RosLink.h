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
