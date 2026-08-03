#include "RosLink.h"
#include <QHash>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTimer>
#include <QUuid>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_msgs/msg/float64.hpp>
#include <cmath>

struct RosLink::Impl {
    std::shared_ptr<rclcpp::Node> node;
    rclcpp::Publisher<std_msgs::msg::Float64>::SharedPtr touchPub;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr cmdPub;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr panelReqPub;
    rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr stopPub;
    QTimer *stopTimer = nullptr;      // 비상정지 중 0 을 20Hz 로 반복 발행
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr stateSub;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr panelResSub;
    rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr poseSub;
    rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odomSub;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr replanReasonSub;
    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr shelfDockStatusSub;

    // 상관 상태는 **패널 쪽에** 있다 — FMS 는 받은 id 를 되돌려 주기만 한다.
    // 둘 다 UI 스레드에서만 건드린다(수신은 시그널로 넘어온 뒤에 처리).
    QHash<QString, std::function<void(bool, QJsonObject)>> pending;
    QHash<QString, QTimer *> timers;
};

RosLink::RosLink(QObject *parent) : QObject(parent), d_(new Impl) {
    if (!rclcpp::ok()) rclcpp::init(0, nullptr);
    d_->node = std::make_shared<rclcpp::Node>("libi_gui");
    d_->touchPub = d_->node->create_publisher<std_msgs::msg::Float64>("ui_last_touch_at", 10);
    d_->cmdPub   = d_->node->create_publisher<std_msgs::msg::String>("fleet_cmd", 10);
    // 비상정지 — twist_mux 의 최상위 입력(255). 타입은 Twist 여야 한다
    // (twist_mux.yaml 의 use_stamped:false 와 짝. 다르면 DDS 가 연결을 안 만든다).
    d_->stopPub  = d_->node->create_publisher<geometry_msgs::msg::Twist>("cmd_vel_stop", 10);
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
            emit personBlockedReceived(o.value("person_blocked").toBool(false));
            emit frontPersonSizeReceived(o.value("front_person_size").toDouble(0.0));
            emit personBlockInReceived(o.value("person_block_in").toDouble(-1.0));
            emit personBlockReported(o.value("person_block_seq").toInt(0),
                                     o.value("person_block_node").toInt(-1));
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

    // pinky_bringup(bringup.py) 이 엔코더로 계산해 30Hz 로 쏘는 실측 바퀴 속도.
    // AI 서버 cmd_vel 미리보기(POSE.linearX/angularZ)와 달리 실제로 바퀴가 도는 값이다.
    d_->odomSub = d_->node->create_subscription<nav_msgs::msg::Odometry>(
        "odom", 10,
        [this](nav_msgs::msg::Odometry::SharedPtr msg) {
            emit odomReceived(msg->twist.twist.linear.x, msg->twist.twist.angular.z);
        });

    // ── 재계획 사유 (FMS → 로봇, 2026-08-03) ─────────────────────────────────
    // ⚠️ **TRANSIENT_LOCAL 로 구독한다.** 발행 쪽(fleet_link `_replan_reason_pubs`)이
    //    래치라, 기본 QoS(VOLATILE)로 받으면 **QoS 불일치로 아무것도 안 온다** —
    //    `/amcl_pose` 에서 이미 한 번 겪은 함정이다(위 주석).
    {
      rclcpp::QoS qos(rclcpp::KeepLast(1));
      qos.transient_local().reliable();
      d_->replanReasonSub = d_->node->create_subscription<std_msgs::msg::String>(
        "replan_reason", qos,
        [this](std_msgs::msg::String::SharedPtr msg) {
          const auto o = QJsonDocument::fromJson(
            QByteArray::fromStdString(msg->data)).object();
          // ⚠️ 깨진 JSON 을 `{seq:0, reason:""}` 으로 조용히 받아들이면 **기준 seq 가
          //    0 으로 리셋된다** — 그 뒤 진짜 사건이 "이미 본 것" 으로 무시된다.
          //    두 필드가 다 있어야 사건으로 친다(codex 검토 P2).
          if (!o.contains("seq") || !o.contains("reason")) { return; }
          emit replanReasonReceived(o.value("seq").toInt(0),
                                    o.value("reason").toString());
        });
    }

    // 정밀 도킹 상태 — robot_agent가 제어 주기와 분리해 0.5초마다 요약을 낸다.
    d_->shelfDockStatusSub = d_->node->create_subscription<std_msgs::msg::String>(
        "shelf_dock_status", 20,
        [this](std_msgs::msg::String::SharedPtr msg) {
            emit shelfDockStatusReceived(QString::fromStdString(msg->data));
        });

    // ── 패널 요청 통로 (FMS app/panel_bridge.py) ──────────────────────────────
    // 방향이 fleet_cmd 와 반대다: 여기서는 **로봇이 요청하고 서버가 승인**한다.
    // 브릿지가 88↔86 을 중계하므로 로봇 쪽 토픽 이름에는 접두사가 없다.
    d_->panelReqPub = d_->node->create_publisher<std_msgs::msg::String>("panel_request", 10);
    d_->panelResSub = d_->node->create_subscription<std_msgs::msg::String>(
        "panel_result", 10,
        [this](std_msgs::msg::String::SharedPtr msg) {
            const auto o = QJsonDocument::fromJson(QByteArray::fromStdString(msg->data)).object();
            // 여긴 ROS spin 스레드다. 콜백을 여기서 부르면 UI 를 다른 스레드에서 만지게
            // 되므로 시그널로 넘긴다(수신자가 UI 스레드라 자동으로 큐잉 연결).
            emit panelResultArrived(o.value(QStringLiteral("id")).toString(), o);
        });
    connect(this, &RosLink::panelResultArrived, this, &RosLink::onPanelResult);

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

void RosLink::setEmergencyStop(bool on) {
    if (!on) {
        if (d_->stopTimer) { d_->stopTimer->stop(); }
        return;
    }
    if (!d_->stopTimer) {
        d_->stopTimer = new QTimer(this);
        d_->stopTimer->setInterval(50);          // 20Hz — twist_mux timeout(0.5s)의 10배 여유
        connect(d_->stopTimer, &QTimer::timeout, this, [this] {
            geometry_msgs::msg::Twist zero;      // 전 축 0 (기본 생성자가 이미 0)
            d_->stopPub->publish(zero);
        });
    }
    // 누른 즉시 한 발 — 첫 타이머 tick(50ms)을 기다리지 않는다.
    geometry_msgs::msg::Twist zero;
    d_->stopPub->publish(zero);
    d_->stopTimer->start();
}

void RosLink::publishFleetCmd(const QString &json) {
    std_msgs::msg::String m; m.data = json.toStdString();
    d_->cmdPub->publish(m);
}

bool RosLink::panelLinkUp() const {
    return d_->panelReqPub && d_->panelReqPub->get_subscription_count() > 0;
}

void RosLink::panelRequest(const QString &op, QJsonObject args,
                           std::function<void(bool, QJsonObject)> cb, int timeoutMs) {
    const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    args.insert(QStringLiteral("id"), id);
    args.insert(QStringLiteral("op"), op);

    if (cb) {
        d_->pending.insert(id, cb);
        // 타임아웃은 선택이 아니다 — 토픽은 구독자가 없어도 발행이 성공하므로, 이게 없으면
        // 응답 없는 요청이 콜백을 영영 붙잡고 화면이 "요청 중" 에서 안 빠져나온다.
        auto *t = new QTimer(this);
        t->setSingleShot(true);
        t->setInterval(timeoutMs);
        connect(t, &QTimer::timeout, this, [this, id]() { onPanelResult(id, QJsonObject()); });
        d_->timers.insert(id, t);
        t->start();
    }

    std_msgs::msg::String m;
    m.data = QJsonDocument(args).toJson(QJsonDocument::Compact).toStdString();
    d_->panelReqPub->publish(m);
}

void RosLink::onPanelResult(QString id, QJsonObject body) {
    if (QTimer *t = d_->timers.take(id)) { t->stop(); t->deleteLater(); }
    const auto cb = d_->pending.take(id);
    if (!cb) return;   // 타임아웃 뒤 늦게 도착한 응답 — 이미 실패로 처리했다
    cb(body.value(QStringLiteral("ok")).toBool(false), body);
}
