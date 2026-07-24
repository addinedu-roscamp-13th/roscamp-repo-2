#pragma once
#include <QObject>
#include <QString>
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

public slots:
    void publishTouch();                        // ui_last_touch_at (Float64, 값 무의미 — 수신측이 스탬프)
    void publishFleetCmd(const QString &json);  // /fleet_cmd (String JSON, 예: {"action":"ui_touch"})

signals:
    void fsmStateReceived(QString currentState, double remainingSec,
                          QString errorCode, double batteryPercent, bool docked);

private:
    struct Impl;
    std::unique_ptr<Impl> d_;
    std::thread spin_;
};
