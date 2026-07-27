#include "RosLink.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/float64.hpp>
#include <cmath>

struct RosLink::Impl {
    std::shared_ptr<rclcpp::Node> node;
    rclcpp::Publisher<std_msgs::msg::Float64>::SharedPtr touchPub;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr cmdPub;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr stateSub;
    rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr poseSub;
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
    // ⚠️ `/amcl_pose` 는 **TRANSIENT_LOCAL** 로 발행된다. 기본 QoS(VOLATILE)로 구독하면
    // QoS 불일치로 **아무것도 안 온다** — 증상이 "지도에 로봇이 영영 안 뜬다" 로 나타나
    // 원인을 찾기 어렵다. libi_modes 의 providers.py 가 같은 이유로 같은 프로파일을 쓴다.
    {
        rclcpp::QoS qos(rclcpp::KeepLast(1));
        qos.transient_local().reliable();
        d_->poseSub = d_->node->create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
            "/amcl_pose", qos,
            [this](geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg) {
                const auto &p = msg->pose.pose.position;
                const auto &q = msg->pose.pose.orientation;
                // 평면 주행이라 yaw 만 있으면 된다 (roll/pitch 는 0).
                const double yaw = std::atan2(2.0 * (q.w * q.z + q.x * q.y),
                                              1.0 - 2.0 * (q.y * q.y + q.z * q.z));
                emit poseReceived(p.x, p.y, yaw);
            });
    }

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
