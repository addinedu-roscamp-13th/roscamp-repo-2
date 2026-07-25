#include "RosLink.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/float64.hpp>

struct RosLink::Impl {
    std::shared_ptr<rclcpp::Node> node;
    rclcpp::Publisher<std_msgs::msg::Float64>::SharedPtr touchPub;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr cmdPub;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr stateSub;
};

RosLink::RosLink(QObject *parent) : QObject(parent), d_(new Impl) {
    if (!rclcpp::ok()) rclcpp::init(0, nullptr);
    d_->node = std::make_shared<rclcpp::Node>("libi_gui");
    d_->touchPub = d_->node->create_publisher<std_msgs::msg::Float64>("ui_last_touch_at", 10);
    d_->cmdPub   = d_->node->create_publisher<std_msgs::msg::String>("fleet_cmd", 10);
    d_->stateSub = d_->node->create_subscription<std_msgs::msg::String>(
        "/libi/fsm_state", 10,
        [this](std_msgs::msg::String::SharedPtr msg) {
            const auto doc = QJsonDocument::fromJson(QByteArray::fromStdString(msg->data));
            const auto o = doc.object();
            emit fsmStateReceived(
                o.value("current_state").toString(),
                o.value("remaining_sec").toDouble(0.0),
                o.value("error_code").toString(),
                o.value("battery_percent").toDouble(-1.0),
                o.value("is_docked").toBool(false));
        });
    spin_ = std::thread([this]() { rclcpp::spin(d_->node); });
}

RosLink::~RosLink() {
    rclcpp::shutdown();
    if (spin_.joinable()) spin_.join();
}

void RosLink::publishTouch() {
    std_msgs::msg::Float64 m; m.data = 0.0;   // 값 무의미 — 로봇이 수신 시점 monotonic 으로 스탬프
    d_->touchPub->publish(m);
}

void RosLink::publishFleetCmd(const QString &json) {
    std_msgs::msg::String m; m.data = json.toStdString();
    d_->cmdPub->publish(m);
}
