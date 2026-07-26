#include <algorithm>
#include <chrono>
#include <cmath>
#include <map>
#include <memory>
#include <sstream>
#include <set>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>
#include <pluginlib/class_loader.hpp>

#include <libi_fleet_msgs/srv/submit_task.hpp>
#include <libi_fleet_msgs/srv/set_plugins.hpp>
#include <libi_fleet_msgs/srv/set_robot_mode.hpp>
#include <libi_fleet_msgs/srv/set_battery.hpp>
#include <libi_fleet_msgs/msg/task_state.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <std_msgs/msg/string.hpp>
#include <rmf_fleet_msgs/msg/robot_state.hpp>
#include <rmf_fleet_msgs/msg/path_request.hpp>
#include <rmf_fleet_msgs/msg/location.hpp>

#include "libi_fleet/fleet_task.hpp"
#include "libi_fleet/navgraph.hpp"
#include "libi_fleet/patrol_cycle.hpp"
#include "libi_fleet/security_patrol_cycle.hpp"
#include "libi_fleet/fms_types.hpp"
#include "libi_fleet/dispatcher_base.hpp"
#include "libi_fleet/traffic_base.hpp"

using SubmitTask = libi_fleet_msgs::srv::SubmitTask;
using SetPlugins = libi_fleet_msgs::srv::SetPlugins;
using SetRobotMode = libi_fleet_msgs::srv::SetRobotMode;
using SetBattery = libi_fleet_msgs::srv::SetBattery;
using TaskState = libi_fleet_msgs::msg::TaskState;
using RmfRobotState = rmf_fleet_msgs::msg::RobotState;
using PathRequest = rmf_fleet_msgs::msg::PathRequest;
using RmfLocation = rmf_fleet_msgs::msg::Location;

namespace
{
// 로봇 소식이 이 시간 이상 끊기면 stale 로 본다. 어댑터 발행 주기는 2 Hz 이므로
// 10 초면 20 프레임을 놓친 것이라 오탐이 아니다.
// 이 파일에서만 쓴다 — fleet_task.hpp 로 내보내지 않는다.
constexpr double kRobotStaleSec = 10.0;
}  // namespace

namespace libi_fleet
{

// 로봇 상태 어휘는 libi_modes 가 소유한다 (미션 FSM 8종).
// FMS 는 이 값을 관측할 뿐 자기만의 모드 어휘를 따로 두지 않는다 — 두 벌을 두면
// FMS 는 IDLE 이라 믿는데 로봇은 WORKING 인 상황이 조용히 생긴다.
//
// 원본(arte_libi_fleet)은 PATROL|IDLE|STOP|CHARGE 4종이었고, 다음과 같이 대응시켰다:
//   STOP   -> ERROR      스스로 움직이지 않는 상태 (is_immobile)
//   CHARGE -> RETURNING  충전소로 복귀 중 (교통 우선순위 tier 2)
//   PATROL, IDLE 은 이름·뜻이 그대로 일치
// 정의처: aba_controller/libi_modes/.../registry.py BRANCH_ORDER
const std::set<std::string> kLibiModesStates = {
  "ERROR", "RETURNING", "CHARGING", "WORKING",
  "INTERACTING", "SECURITY_PATROL", "PATROL", "IDLE",
};

class FleetNode : public rclcpp::Node
{
public:
  FleetNode()
  : rclcpp::Node("libi_fleet"),
    disp_loader_("libi_fleet", "libi_fleet::DispatcherBase"),
    traf_loader_("libi_fleet", "libi_fleet::TrafficBase")
  {
    navgraph_file_ = declare_parameter<std::string>("navgraph_file", "");
    const std::string disp_name = declare_parameter<std::string>("dispatcher_plugin", "libi_fleet::Auction");
    // 디버깅: 교통관제 기본 OFF(GrantAllTraffic=항상 GRANT). CBS + Space-Time A* 도입 시 교체 예정.
    const std::string traf_name = declare_parameter<std::string>("traffic_plugin", "libi_fleet::GrantAllTraffic");
    const std::string fleet = declare_parameter<std::string>("fleet_name", "libi");
    fleet_name_ = fleet;

    // 배터리 소비 모델(sim 가정값). 완주 가능성 관문: battery% ≥ 소비% + reserve.
    energy_.drain_per_m   = declare_parameter<double>("battery_drain_per_m", 1.0);   // 주행 1m당 %
    energy_.drain_per_act = declare_parameter<double>("battery_drain_per_act", 0.5); // 팔 1동작당 %
    energy_.reserve       = declare_parameter<double>("battery_reserve_pct", 15.0);  // 최소 잔여 %

    // 무진행(stuck) 감지 임계 틱. **0 이면 비활성**(기본).
    //
    // 원래는 상수 kStuckTicks(=100, ≈15s@150ms)로 항상 켜져 있었는데, sim 에서는 nav2 가
    // 첫 경로를 계획하는 동안 로봇이 제자리에 있어 그 시간을 넘겨 **정상 작업이 취소**됐다.
    // (취소 → 재시도 → 서비스 타임아웃 → 주문 FAILED)
    // 예외처리(재계획/에스컬레이션)를 제대로 넣기 전까지는 꺼 둔다. 켜려면 틱 수를 준다:
    //   ros2 run libi_fleet fleet_node --ros-args -p stuck_ticks:=100
    stuck_ticks_ = declare_parameter<int>("stuck_ticks", 0);

    // 도착 판정 반경(m). 맵 축척에 따라 달라진다 — 범위 규칙은 fleet_task.hpp 주석 참고.
    //   실물(수십 m 건물) : 기본 0.35
    //   arte2(1.26×2.16m) : 0.05  (nav2 xy_goal_tolerance 0.05 ≤ r < 최소 레인 0.062)
    //   ros2 run libi_fleet fleet_node --ros-args -p arrive_radius:=0.05
    arrive_radius_ = declare_parameter<double>("arrive_radius", kArriveDefault);

    // 경유 노드를 "지났다"고 볼 반경(m). arrive_radius_ 이하면 꺼진다(시작 로그에 찍는다).
    //
    // ## 왜 필요한가 — 노드마다 서는 문제
    // 로봇은 노드 하나씩 nav2 목표를 받는다(full_path_=false). 그런데 nav2 는 목표에
    // **감속해서 정지**하므로, 다음 노드를 도착 후에 허가하면 정점마다 선다:
    //
    //     이동 → 감속 → 정지 → (틱 150ms + 예약 + 새 목표) → 재출발
    //
    // 그래서 경유 노드는 도착 전 **근처에 오면** 지난 것으로 보고 다음 노드를 미리
    // 예약·발행한다. 로봇은 서기 전에 새 목표를 받아 그대로 이어 달린다.
    //
    // ⚠️ **마지막 노드에는 적용하지 않는다.** 거기서는 실제로 서야 하고(서가 앞 정밀
    //    정지, 작업 완료 판정), 느슨하게 보면 도착하지 않았는데 완료로 보고된다.
    //
    // ⚠️ 반경을 레인 길이의 절반으로 제한한다. 고정값만 쓰면 **레인보다 큰 반경**이
    //    생겨(arte2 최단 레인 0.062m) 출발하자마자 다음 노드를 잡고, 그 노드를 통째로
    //    건너뛰어 코너를 가로지른다. 짧은 레인에서는 자동으로 기존 동작으로 돌아간다.
    prefetch_radius_ = declare_parameter<double>("prefetch_radius", kPrefetchDefault);

    // 남은 정점을 전부 보낼지. **기본 false — 다음 한 노드만 보낸다.**
    //
    // ⚠️ 한 번에 다 보내면 로봇이 예약하지 않은 노드로 들어가 **교통 협상이 무력화된다**
    //    (traffic 플러그인은 다음 한 노드만 예약한다). 로봇이 여러 대인 순간 충돌한다.
    //    그래서 노드 단위가 정본이고, 전체 경로 전송은 단일 로봇 디버깅용으로만 남긴다.
    //    짧은 구간을 계획하지 못하던 문제는 플래너를 Theta* 로 바꿔서 해결했다
    //    (NavFn 의 전위장 고유 실패 모드였다 — send_path 주석 참고).
    full_path_ = declare_parameter<bool>("full_path", false);

    // 이동 중 같은 경로를 몇 틱마다 다시 발행할지(150ms 틱). 0 이면 재발행 안 함.
    //
    // ⚠️ **왜 필요한가**: send_path 는 노드가 바뀔 때 딱 한 번만 발행한다. 로봇 쪽
    // 드라이버가 그 한 번을 놓치면(브릿지 순단·기동 타이밍) 아무도 다시 알려주지 않고,
    // fleet_node 는 t.moving=true 로 도착만 기다려 **주문이 영구 정지**한다.
    // 실측: GRANT 는 나갔는데 드라이버 로그가 0건이었고 로봇은 그 자리에 계속 서 있었다.
    // 드라이버는 같은 목적지면 무시하므로(주행 중 끊김 없음) 재발행은 안전하다.
    resend_ticks_ = declare_parameter<int>("resend_ticks", 7);   // ≈1초

    // ── 시간 계획(CBS) 재계획 정책 ─────────────────────────────────────────
    // 계획 도착 시각을 이만큼 넘기면 그 시간표는 이미 틀렸다고 본다. 장애물 회피·감속으로
    // 몇 초 밀리는 것은 정상이라 0 으로 두면 재계획만 반복한다.
    plan_deadline_slack_ = declare_parameter<double>("plan_deadline_slack", 3.0);
    // 재계획 최소 간격(틱). 매 틱 CBS 를 돌리면 관제가 그것만 하게 된다.
    replan_cooldown_ticks_ = declare_parameter<int>("replan_cooldown_ticks", 20);

    // 순회(patrol) 모드: 켜지면 idle 로봇이 patrol_route(외곽 루프)를 무한 순회.
    patrol_ = declare_parameter<bool>("patrol", true);
    // "auto"(기본) → 우/하 우선 규칙으로 순회 루프 생성(그래프 로드 후). 그 외는 수동 정점 목록.
    const std::string route_s = declare_parameter<std::string>("patrol_route", "auto");
    // 야간 보안순회 루프. "auto" → security_patrol_boundary_cycle(현재 주간과 동일 위임).
    // 그 외는 수동 정점 목록(런치 스크립트가 이름→인덱스 해석해 넣는다).
    const std::string sec_route_s = declare_parameter<std::string>("security_patrol_route", "auto");

    if (!graph_.load(navgraph_file_)) {
      RCLCPP_FATAL(get_logger(), "navgraph 로드 실패: %s", navgraph_file_.c_str());
      throw std::runtime_error("navgraph load failed");
    }
    active_disp_ = disp_name;
    active_traf_ = traf_name;
    dispatcher_ = disp_loader_.createSharedInstance(disp_name);
    traffic_ = traf_loader_.createSharedInstance(traf_name);
    RCLCPP_INFO(get_logger(), "plugins: dispatcher=%s traffic=%s | navgraph=%d verts",
                disp_name.c_str(), traf_name.c_str(), graph_.size());

    // 순회 루프 확정: "auto" 면 우/하 우선 규칙으로 생성, 아니면 수동 정점 목록 파싱.
    if (route_s == "auto") {
      patrol_route_ = right_hand_boundary_cycle(graph_);
      if (patrol_route_.size() < 2) {   // 생성 실패 → 안전 fallback
        RCLCPP_WARN(get_logger(), "patrol_route auto 생성 실패 → 기본 루프 사용");
        patrol_route_ = {0, 1, 2, 3, 7, 6, 5, 4};
      }
    } else {
      std::stringstream ss(route_s); int v; while (ss >> v) { patrol_route_.push_back(v); }
    }
    sanitize_route(patrol_route_, "patrol_route");   // 범위 밖 인덱스 방어(직접 -p 준 경우)
    ensure_ccw(patrol_route_);   // 순회는 항상 반시계(CCW)
    {
      std::string s; for (int v : patrol_route_) { s += std::to_string(v) + " "; }
      RCLCPP_INFO(get_logger(), "순회 루프(CCW): %s", s.c_str());
    }

    // 야간 보안순회 루프 확정.
    if (sec_route_s == "auto") {
      security_patrol_route_ = security_patrol_boundary_cycle(graph_);
      if (security_patrol_route_.size() < 2) {   // 생성 실패 → 주간 루프로 폴백(일단 동일)
        RCLCPP_WARN(get_logger(), "security_patrol_route auto 생성 실패 → 주간 순회 루프 재사용");
        security_patrol_route_ = patrol_route_;
      }
    } else {
      std::stringstream ss2(sec_route_s); int v; while (ss2 >> v) { security_patrol_route_.push_back(v); }
    }
    sanitize_route(security_patrol_route_, "security_patrol_route");   // 범위 밖 인덱스 방어
    ensure_ccw(security_patrol_route_);   // 순회는 항상 반시계(CCW)
    {
      std::string s; for (int v : security_patrol_route_) { s += std::to_string(v) + " "; }
      RCLCPP_INFO(get_logger(), "보안순회 루프(CCW): %s", s.c_str());
    }

    state_sub_ = create_subscription<RmfRobotState>(
      "/robot_state", 10,
      std::bind(&FleetNode::on_robot_state, this, std::placeholders::_1));
    path_pub_ = create_publisher<PathRequest>("/robot_path_requests", rclcpp::QoS(10).reliable());
    task_pub_ = create_publisher<TaskState>("/fms/task_states", 10);
    occ_pub_ = create_publisher<std_msgs::msg::String>("/fms/occupancy", 10);
    // 시간 계획(CBS)의 **시간표**. 재계획할 때마다 한 번 낸다.
    // /fms/routes 는 좌표만 있어 "언제 어디" 를 알 수 없다 — 지연으로 예약 시각이 밀리는
    // 것을 밖에서 보려면 도착틱이 필요하다. 반응형 교통에서는 아무것도 안 나간다.
    plan_pub_ = create_publisher<std_msgs::msg::String>("/fms/plan", rclcpp::QoS(1).transient_local());
    route_pub_ = create_publisher<std_msgs::msg::String>("/fms/routes", 10);
    goal_pub_ = create_publisher<std_msgs::msg::String>("/fms/goals", 10);

    srv_ = create_service<SubmitTask>(
      "/fms/submit_task",
      std::bind(&FleetNode::on_submit, this, std::placeholders::_1, std::placeholders::_2));
    plugins_srv_ = create_service<SetPlugins>(
      "/fms/set_plugins",
      std::bind(&FleetNode::on_set_plugins, this, std::placeholders::_1, std::placeholders::_2));
    reload_srv_ = create_service<std_srvs::srv::Trigger>(
      "/fms/reload_navgraph",
      std::bind(&FleetNode::on_reload, this, std::placeholders::_1, std::placeholders::_2));
    mode_srv_ = create_service<SetRobotMode>(
      "/fms/set_robot_mode",
      std::bind(&FleetNode::on_set_mode, this, std::placeholders::_1, std::placeholders::_2));
    battery_srv_ = create_service<SetBattery>(
      "/fms/set_battery",
      std::bind(&FleetNode::on_set_battery, this, std::placeholders::_1, std::placeholders::_2));
    // libi_modes 상태 자동 구독(#16) — 브릿지가 /libi/fsm_state 로 올린다. set_robot_mode 불필요.
    fsm_sub_ = create_subscription<std_msgs::msg::String>(
      "/libi/fsm_state", 10,
      std::bind(&FleetNode::on_fsm_state, this, std::placeholders::_1));

    timer_ = create_wall_timer(std::chrono::milliseconds(150),
                               std::bind(&FleetNode::on_timer, this));
    // 선행 통과는 arrive_radius 보다 커야 동작한다. 작으면 **조용히 꺼진 것과 같아져**
    // "왜 여전히 노드마다 서지"를 한참 찾게 된다. 그래서 켜짐/꺼짐을 시작할 때 못 박는다.
    if (prefetch_radius_ > arrive_radius_) {
      RCLCPP_INFO(get_logger(), "선행 통과 ON — 경유 노드 %.3fm (레인 절반으로 제한) / 도착 %.3fm",
                  prefetch_radius_, arrive_radius_);
    } else {
      RCLCPP_WARN(get_logger(),
                  "선행 통과 OFF — prefetch_radius(%.3f) ≤ arrive_radius(%.3f). "
                  "노드마다 감속·정지한다. 켜려면 arrive_radius 보다 크게 줄 것.",
                  prefetch_radius_, arrive_radius_);
    }
    RCLCPP_INFO(get_logger(), "libi_fleet FMS up");
  }

private:
  void on_robot_state(const RmfRobotState::SharedPtr msg)
  {
    auto & r = robots_[msg->name];
    r.name = msg->name;
    r.x = msg->location.x;
    r.y = msg->location.y;
    // 배터리는 sim(slotcar)의 battery_percent 를 신뢰하지 않고 내부 상태로 관리(기본 100%).
    // 콘솔 UI(/fms/set_battery)로 각 로봇 배터리를 설정 → 완주 관문·우선순위에 반영.
    if (robot_mode_.find(msg->name) == robot_mode_.end()) {
      robot_mode_[msg->name] = patrol_ ? "PATROL" : "IDLE";   // 최초 관측 시 기본 모드
    }
    last_state_at_[msg->name] = now();
  }

  void publish_task_state(const std::string & id, const std::string & state, const std::string & robot)
  {
    TaskState ts;
    ts.task_id = id;
    ts.state = state;
    ts.robot_id = robot;
    task_pub_->publish(ts);
  }

  // 로봇에게 줄 경로를 만든다. `verts` 는 **navgraph 정점 인덱스 열**(간선을 따라간다).
  //
  // ## 왜 정점 열을 그대로 주나
  // 전역 플래너에게 매번 "다음 정점 하나"만 맡기면, 이 축소맵(통로 폭 0.20m)에서는
  // 6~20cm 짜리 계획을 수십 번 시키는 꼴이 된다. 실제로 NavFn 이 그걸 못 만들고
  //   "Failed to create a plan from potential when a legal potential was found"
  // 로 실패했고, BT 가 복구동작(spin/backup)을 돌려 로봇이 엉뚱한 곳을 배회했다.
  //
  // navgraph 의 정점·간선은 **사람이 통로 중심에 맞춰 직접 찍은 것**이라, 그 열을
  // 그대로 경유점으로 주면 벽에서 떨어진 안전한 경로가 된다. 로봇 쪽 드라이버가
  // 이걸 NavigateThroughPoses 로 넘겨 멈춤 없이 통과한다.
  //
  // ## 자세(yaw)
  // 각 경유점의 yaw 는 **그 다음 점으로 향하는 방향**이다. 예전엔 yaw 를 안 채워서
  // 0(맵 +x)으로 나갔고, yaw_goal_tolerance 0.15rad 탓에 정점마다 "이동 → 다시 yaw 0
  // 으로 제자리 회전"을 반복했다(노드당 10초, 화면상 엉뚱한 곳을 쳐다봄).
  // ⚠️ 마지막 정점의 자세는 waypoint.yaml 에 정의된 값(예: 서가를 바라보는 방향)을
  //    써야 맞다 — 지금 navgraph 생성기가 그 yaw 를 버리고 있어 후속 과제로 남긴다.
  void send_path(const std::string & robot, double x0, double y0,
                 const std::vector<int> & verts)
  {
    if (verts.empty()) { return; }
    PathRequest req;
    req.fleet_name = fleet_name_;
    req.robot_name = robot;
    req.task_id = robot + "-" + std::to_string(++path_seq_);   // 고유 task_id (slotcar dedup 회피)

    std::vector<RmfLocation> pts;
    pts.reserve(verts.size() + 1);
    RmfLocation p0; p0.x = x0; p0.y = y0; p0.level_name = "L1";
    pts.push_back(p0);                       // [0] 은 출발점(로봇 현재 위치)이지 목적지가 아니다
    for (int v : verts) {
      const Vertex & vx = graph_.vertex(v);
      RmfLocation p; p.x = vx.x; p.y = vx.y; p.level_name = "L1";
      pts.push_back(p);
    }
    // 각 점의 yaw = 다음 점으로 향하는 방향. 마지막 점은 직전 구간 방향을 유지한다.
    for (size_t i = 0; i + 1 < pts.size(); ++i) {
      const double dx = pts[i + 1].x - pts[i].x, dy = pts[i + 1].y - pts[i].y;
      pts[i].yaw = (std::hypot(dx, dy) > 1e-6) ? std::atan2(dy, dx) : 0.0;
    }
    if (pts.size() >= 2) { pts.back().yaw = pts[pts.size() - 2].yaw; }
    // 마지막 점이 **정점 자신의 자세**를 가지고 있으면 그걸 쓴다 — 서가를 정면으로
    // 보고 서야 팔이 책을 집을 수 있고, 주차장은 도킹 방향으로 서야 한다.
    // 경유지에는 적용하지 않는다(노드마다 제자리 회전을 하게 된다).
    if (!verts.empty()) {
      const Vertex & last = graph_.vertex(verts.back());
      if (last.has_yaw) { pts.back().yaw = last.yaw; }
    }

    req.path = pts;
    path_pub_->publish(req);
  }

  void on_submit(const std::shared_ptr<SubmitTask::Request> req,
                 std::shared_ptr<SubmitTask::Response> res)
  {
    int goal = -1;
    try { goal = std::stoi(req->dropoff); } catch (...) { goal = -1; }
    if (goal < 0 || goal >= graph_.size()) {
      res->accepted = false; res->reason = "bad_goal_vertex"; return;
    }
    const int arm_actions = req->arm_actions > 0 ? req->arm_actions : 0;   // 팔 동작 횟수
    std::string robot;
    if (!req->robot.empty()) {              // 특정 로봇 강제 배정
      auto it = robots_.find(req->robot);
      if (it == robots_.end()) { res->accepted = false; res->reason = "unknown_robot"; return; }
      // 강제 배정이라도 스스로 못 움직이는 로봇에는 줄 수 없다.
      if (is_immobile(mode_of(req->robot))) { res->accepted = false; res->reason = "robot_stopped"; return; }
      // 순회/기존 task 중이어도 받는다 — 아래 path/battery 통과 후 기존 task 취소하고 강제 배정.
      robot = req->robot;
    } else {                                // dispatcher 가 선택 (IDLE·PATROL 로봇만 후보)
      std::vector<RobotInfo> snapshot;
      for (const auto & kv : robots_) {
        const std::string m = mode_of(kv.first);
        if (!is_dispatchable(m)) { continue; }
        RobotInfo ri = kv.second;
        if (is_on_patrol(kv.first)) { ri.busy = false; }   // 순회는 중단 가능 → 경매 후보에 포함
        snapshot.push_back(ri);
      }
      robot = dispatcher_->assign(goal, arm_actions, snapshot, graph_, energy_);
    }
    if (robot.empty()) {
      res->accepted = false; res->reason = "no_robot_available"; return;
    }
    auto & r = robots_[robot];
    int start = graph_.nearest(r.x, r.y);
    auto path = graph_.dijkstra(start, goal);
    if (start == goal) { path = {goal, goal}; }   // 최근접 정점이 곧 목표 → 그 노드로 이동 후 완료(auction.cpp 와 일치, no_path 오거절 방지)
    if (path.size() < 2) {
      res->accepted = false; res->reason = "no_path"; return;   // 진짜 도달 불가만 거절
    }
    // 완주 가능성 관문(강제 배정도 포함 — 방전 좌초 방지). 자동배차는 dispatcher 가 이미 필터.
    double need = graph_.path_cost(path) * energy_.drain_per_m
                + arm_actions * energy_.drain_per_act + energy_.reserve;
    if (r.battery < need) {
      res->accepted = false; res->reason = "insufficient_battery"; return;
    }
    if (r.busy) { cancel_task(robot); }   // 순회/기존 task 취소하고 이 배차로 대체 (특정 배차·경매 낙찰 공통)
    r.busy = true;
    // 콘솔이 지정한 커스텀 작업 이름(requester 필드에 실려옴). 비우면 자동 T-N.
    std::string tid = req->requester.empty()
                        ? ("T-" + std::to_string(++task_counter_))
                        : req->requester;
    r.task_id = tid;
    ActiveTask t; t.id = tid; t.robot = robot; t.path = path; t.idx = 1; t.moving = false;
    t.priority = req->priority;
    t.start_seq = ++task_seq_;
    t.arm_actions = arm_actions;
    traffic_->request_move(robot, path[0], path[0], compute_priority(robot, t));   // 시작 노드 점유
    tasks_.push_back(t);
    replan_all_routes();   // 배치 계획형 교통(CBS)이면 전 로봇 시간표를 다시 짠다
    res->accepted = true; res->task_id = tid; res->reason = "";
    publish_task_state(tid, "ASSIGNED", robot);
    RCLCPP_INFO(get_logger(), "[%s] %s 배차 → goal v%d, path %zu nodes",
                tid.c_str(), robot.c_str(), goal, path.size());
  }

  // ── 배치 계획형 교통(CBS) 재계획 ─────────────────────────────────────────
  //
  // 반응형 플러그인(ReservationDeadlock/GrantAll)은 plans_routes()==false 라 여기서 바로 나간다.
  // 그래서 기본 동작은 예전과 **한 틱도 달라지지 않는다**.
  //
  // 계획형이면 활성 task 전체를 한 번에 다시 푼다. 로봇 3~5대·정점 41개 규모에서는
  // 신규 로봇만 끼워 넣는 것(prioritized)보다 전체 재계획이 단순하고, 끼워넣기 특유의
  // 굶주림·불필요한 우회가 없다.
  //
  // ⚠️ 출발점은 **관측상 확정된 정점**이다. 이동 중인 로봇은 이미 GRANT 받아 그 노드로
  //    가고 있으므로(커밋 구간) 그 노드를 출발점으로 잡고, 진행 중인 한 칸은 새 경로 앞에
  //    그대로 붙인다. 그러지 않으면 로봇이 가는 도중에 경로가 갈아치워져 왔던 길을 되돌아간다.
  //
  // ponytail: 순회(patrol) task 는 계획에서 뺀다. 순회 경로는 canonical 랩이라 CBS 가 준
  //   최단 경로로 갈아치우면 순회 의미가 깨진다(patrol_goal/route_for 가 그 순서를 전제).
  //   순회 로봇은 CbsTraffic 의 물리 점유 안전망이 막아 준다 — 안전하지만 계획은 그만큼
  //   보수적이다. 유한 horizon(다음 한 바퀴)만 계획에 넣는 방식으로 승급할 수 있다.
  void replan_all_routes()
  {
    if (!traffic_ || !traffic_->plans_routes() || tasks_.empty()) { return; }

    PlanSnapshot snap;
    snap.graph = &graph_;
    // 스스로 못 움직이는 로봇은 영구 장애물로 넣는다 — 계획이 피해 가야 한다.
    for (const auto & kv : robots_) {
      if (is_immobile(mode_of(kv.first))) {
        snap.blocked.push_back(graph_.nearest(kv.second.x, kv.second.y));
      }
    }

    std::vector<ActiveTask *> planned;
    for (auto & t : tasks_) {
      if (t.patrol) { continue; }
      if (t.path.size() < 2 || t.idx < 1 || t.idx >= t.path.size()) { continue; }
      PlanRequest pr;
      pr.robot = t.robot;
      pr.start = t.moving ? t.path[t.idx] : t.path[t.idx - 1];   // 커밋 구간 존중
      pr.goal = t.path.back();
      pr.priority = compute_priority(t.robot, t);
      if (pr.start == pr.goal) { continue; }   // 이미 목표 — 계획할 것이 없다
      snap.robots.push_back(pr);
      planned.push_back(&t);
    }
    if (snap.robots.empty()) { return; }

    const std::vector<PlannedRoute> routes = traffic_->replan(snap);
    if (routes.size() != planned.size()) {
      // ⚠️ 조용히 넘어가면 안 된다. 계획이 안 서면 시스템은 반응형으로 도는데, 관제 화면에는
      //    아무 표시가 없어 "CBS 를 켰는데 왜 그대로지" 를 나중에 디버깅하게 된다.
      //
      //    가장 흔한 원인은 **목표가 겹치는 것**이다. 계획은 목표 도착 후 그 정점에 계속
      //    앉아 있다고 보므로(kNeverEnds), 두 로봇의 목표가 같으면 해가 아예 없다.
      //    실행기는 도착 즉시 노드를 놓지만, 로봇이 실제로 비켜 주는 건 아니라서
      //    계획을 느슨하게 푸는 대신 **왜 못 세웠는지 알리는 쪽**을 택한다.
      std::set<int> goals;
      std::string dup;
      for (const auto & r : snap.robots) {
        if (!goals.insert(r.goal).second) { dup += " v" + std::to_string(r.goal); }
      }
      RCLCPP_WARN(get_logger(),
                  "[traffic] 시간표를 세우지 못했습니다(%zu/%zu) — 반응형으로 운행합니다.%s",
                  routes.size(), planned.size(),
                  dup.empty() ? "" : (" 목표 중복:" + dup + " (같은 정점을 목표로 둘 수 없습니다)").c_str());
      return;   // 기존 경로 그대로. 플러그인은 이미 반응형으로 내려가 있다.
    }

    const double epoch = now_sec();
    const double tick_sec = traffic_->tick_seconds();
    for (size_t i = 0; i < routes.size(); ++i) {
      ActiveTask & t = *planned[i];
      if (routes[i].robot != t.robot || routes[i].path.size() < 2) { continue; }
      std::vector<int> np, na;
      np.insert(np.end(), routes[i].path.begin(), routes[i].path.end());
      na.insert(na.end(), routes[i].arrive_tick.begin(), routes[i].arrive_tick.end());
      if (t.moving) {
        np.insert(np.begin(), t.path[t.idx - 1]);   // 진행 중인 한 칸을 살린다
        na.insert(na.begin(), -1);                  // 이미 떠난 정점 — 마감 없음
        // ⚠️ **지금 향해 가는 정점(커밋 노드)에도 마감을 걸지 않는다.**
        //    새 계획은 그 정점을 t=0 으로 잡지만, 로봇은 아직 **가는 중**이라 남은 주행시간이
        //    있다. 0 으로 두면 그 남은 시간이 그대로 "지연" 으로 잡혀, 정상 계획이 세워지자마자
        //    무효화되고 재계획이 계속 돈다.
        //    (실측: 시나리오 첫 실행에서 `계획 도착(0틱) 초과 3.1s` 가 3초마다 반복됐다.
        //     센티널을 이전 정점에만 넣고 커밋 노드에는 안 넣어서 생긴 일이다.)
        //    커밋 구간의 지연은 stuck 감지(no_move)와 drift_limit 이 이미 담당한다.
        if (na.size() > 1) { na[1] = -1; }
      }
      t.path = np;
      t.plan_arrive = na;
      t.plan_epoch = epoch;
      t.plan_tick_sec = tick_sec;
      t.idx = 1;
      t.reroutes = 0;
      RCLCPP_INFO(get_logger(), "[%s] %s 시간표 재계획 → %zu nodes (도착 %d틱)",
                  t.id.c_str(), t.robot.c_str(), t.path.size(),
                  routes[i].arrive_tick.empty() ? -1 : routes[i].arrive_tick.back());
    }
    publish_plan(routes, epoch, tick_sec);
  }

  // 시간표를 밖으로 낸다(시각화·기록용). 재계획할 때마다 한 번.
  // seq 가 늘어나는 것이 "다시 짰다" 는 신호고, 같은 경로라도 arrive 가 달라지면
  // **예약 시각이 밀린 것**이다 — 지연이 관제에 반영됐다는 증거다.
  void publish_plan(const std::vector<PlannedRoute> & routes, double epoch, double tick_sec)
  {
    std::string j = "{\"seq\":" + std::to_string(++plan_seq_) +
                    ",\"epoch\":" + std::to_string(epoch) +
                    ",\"tick_sec\":" + std::to_string(tick_sec) + ",\"robots\":{";
    bool first = true;
    for (const auto & r : routes) {
      if (!first) { j += ","; }
      j += "\"" + r.robot + "\":{\"path\":[";
      for (size_t i = 0; i < r.path.size(); ++i) {
        j += (i ? "," : "") + std::to_string(r.path[i]);
      }
      j += "],\"arrive\":[";
      for (size_t i = 0; i < r.arrive_tick.size(); ++i) {
        j += (i ? "," : "") + std::to_string(r.arrive_tick[i]);
      }
      j += "]}";
      first = false;
    }
    j += "}}";
    std_msgs::msg::String m; m.data = j;
    plan_pub_->publish(m);
  }

  // 계획이 밀려서 반응형으로 강등된 뒤, 다시 계획으로 되돌아온다.
  //
  // 장애물·지체로 늦는 것은 정상 운영에서 늘 일어난다. 강등만 있고 복귀가 없으면 CBS 는
  // 첫 지연 한 번으로 영영 꺼진 채 남는다 — 그러면 붙인 의미가 없다.
  //
  // ⚠️ 매 틱(150ms) 재계획하면 CBS 가 관제를 먹는다. 최소 간격을 둔다. 지연은 몇 초 단위로
  //    풀리는 일이라 이 정도 지연 반응이면 충분하다.
  void service_replan_requests()
  {
    if (!traffic_ || !traffic_->plans_routes()) { return; }
    if (replan_cooldown_ > 0) { replan_cooldown_--; return; }
    if (!traffic_->needs_replan() && !replan_requested_) { return; }

    // 진전(노드 도달) 없이 재계획이 반복되면 간격을 배로 늘린다.
    //
    // 지연 원인이 계획으로 안 풀리는 것일 때(복도가 막힘, 로봇이 아주 느림) 고정 간격이면
    // CBS 가 3초마다 영원히 돈다 — 관제가 그것만 하게 된다. 진전이 생기면(도달 시) 0 으로
    // 되돌리므로, 정상 운영에서는 항상 최단 간격이다.
    const int shift = std::min(replan_streak_, 4);          // 최대 16배
    replan_cooldown_ = replan_cooldown_ticks_ * (1 << shift);
    replan_streak_++;

    RCLCPP_INFO(get_logger(), "[traffic] 시간표 무효 → 재계획 (사유: %s, 다음 간격 %d틱)",
                traffic_->last_demote_reason().empty() ? "도착 마감 초과" : traffic_->last_demote_reason().c_str(),
                replan_cooldown_);
    replan_requested_ = false;
    replan_all_routes();
  }

  static double now_sec()
  {
    return std::chrono::duration<double>(
             std::chrono::steady_clock::now().time_since_epoch()).count();
  }

  // 계획 도착 시각을 넘겼나. 넘겼으면 그 시간표는 이미 남의 통과를 잘못 열어 주고 있다.
  //
  // ⚠️ 로봇이 늦는 것 자체는 막을 수 없다(장애물 회피·감속·리로컬라이제이션). 막을 수 있는 건
  //    **늦은 걸 모르는 것**이다. 계획은 "이 정점을 이 시각에 비운다"를 전제로 남에게 통과를
  //    열어 줬으므로, 그 전제가 깨진 순간 다시 짜야 한다.
  void check_plan_deadline(const ActiveTask & t)
  {
    if (t.plan_arrive.empty() || t.idx >= t.plan_arrive.size()) { return; }
    if (t.plan_arrive[t.idx] < 0) { return; }   // 마감 없음(재계획 시점에 이미 이동 중이던 칸)
    const double due = t.plan_epoch + t.plan_arrive[t.idx] * t.plan_tick_sec + plan_deadline_slack_;
    if (now_sec() <= due) { return; }
    if (!replan_requested_) {
      std::string dbg;
      for (size_t i = 0; i < t.plan_arrive.size(); ++i) {
        dbg += (i ? "," : "") + std::string(i == t.idx ? "*" : "") +
               std::to_string(t.path[i]) + "@" + std::to_string(t.plan_arrive[i]);
      }
      RCLCPP_WARN(get_logger(),
                  "[%s] %s ⏱ v%d 계획 도착(%d틱) 초과 %.1fs → 재계획 요청 [idx=%zu %s]",
                  t.id.c_str(), t.robot.c_str(), t.path[t.idx], t.plan_arrive[t.idx],
                  now_sec() - due + plan_deadline_slack_, t.idx, dbg.c_str());
    }
    replan_requested_ = true;
  }

  void on_timer()
  {
    service_replan_requests();

    // ── 로봇 인식 상태 경고 ────────────────────────────────────────────────
    // robots_ 는 /robot_state 로만 채워지고(on_robot_state) **어디서도 제거되지 않는다.**
    // 그래서 두 가지 고장이 각각 다른 모습으로 나타나고, 둘 다 예전엔 무증상이었다:
    //
    //   ① 로봇을 한 번도 못 봄  → 순회 루프가 돌 대상이 없어 배차·순찰이 시작조차 안 된다
    //   ② 보다가 소식이 끊김    → 옛날 좌표를 현재 위치로 믿고 도착 판정·GRANT 를 계속 낸다
    //
    // 그동안 관제 패널에는 로봇이 정상으로 보인다(패널은 amcl_pose 를 직접 읽는다).
    // 2026-07-26 순찰 정지가 ①이었고, 침묵 때문에 진단이 몇 시간 걸렸다.
    if (robots_.empty()) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 15000,
        "로봇 0대 — /robot_state 를 아무도 발행하지 않고 있습니다. 배차·순회가 시작되지 않습니다. "
        "확인: pgrep -af robot_state_adapter  /  "
        "어댑터 로그에 'amcl_pose 대기' 가 있으면 브릿지·AMCL 문제입니다  /  "
        "기동: ./scripts/laptop/robot-link.sh --all");
    } else {
      // 두 경우를 **구별해서** 모은다 — 원인도 조치도 다르다.
      //   never : /robot_state 를 한 번도 못 받았다. 위치가 (0,0) 인 유령이다.
      //           robots_ 에는 on_set_battery(:880) 가 미관측 로봇도 만들어 넣는다.
      //   stale : 받다가 끊겼다. 어댑터가 죽었거나 브릿지가 끊긴 것이다.
      const auto t_now = now();
      std::string never_seen, stale;
      for (const auto & kv : robots_) {
        auto it = last_state_at_.find(kv.first);
        if (it == last_state_at_.end()) {
          if (!never_seen.empty()) { never_seen += ", "; }
          never_seen += kv.first;
        } else if ((t_now - it->second).seconds() > kRobotStaleSec) {
          if (!stale.empty()) { stale += ", "; }
          stale += kv.first;
        }
      }
      if (!never_seen.empty()) {
        RCLCPP_WARN_THROTTLE(
          get_logger(), *get_clock(), 15000,
          "위치 미관측 로봇: %s — /robot_state 를 한 번도 못 받았습니다. "
          "위치를 (0,0) 으로 두고 배차 판단이 돌아갑니다. "
          "확인: pgrep -af robot_state_adapter  /  어댑터 로그의 'amcl_pose 대기'",
          never_seen.c_str());
      }
      if (!stale.empty()) {
        RCLCPP_WARN_THROTTLE(
          get_logger(), *get_clock(), 15000,
          "로봇 상태 끊김(%.0fs 이상): %s — 이 로봇들의 위치는 옛날 값입니다. "
          "도착 판정과 통행 허가가 실제 위치와 어긋날 수 있습니다. "
          "확인: pgrep -af robot_state_adapter",
          kRobotStaleSec, stale.c_str());
      }
    }

    // 순회 모드(per-robot): task 없는 PATROL 로봇은 주간 순회, SECURITY_PATROL 로봇은
    // 보안 순회 루프를 부여한다. 둘 다 문자열 정확일치 — SECURITY_PATROL 이 빠져 있어서
    // 야간 로봇이 웨이포인트 허가를 못 받고 제자리에 멈추던 구멍을 여기서 메운다.
    for (auto & kv : robots_) {
      RobotInfo & r = kv.second;
      if (r.busy) { continue; }
      bool has = false;
      for (const auto & t : tasks_) { if (t.robot == r.name) { has = true; break; } }
      if (has) { continue; }
      const std::string m = mode_of(r.name);
      if (m == "PATROL" && patrol_route_.size() >= 2) { start_patrol(r); }
      else if (m == "SECURITY_PATROL" && security_patrol_route_.size() >= 2) { start_security_patrol(r); }
    }

    for (auto it = tasks_.begin(); it != tasks_.end();) {
      ActiveTask & t = *it;
      RobotInfo & r = robots_[t.robot];

      const Vertex & tv = graph_.vertex(t.path[t.idx]);
      double d = std::hypot(r.x - tv.x, r.y - tv.y);

      // ── stuck 감지: 이동 지시(t.moving)됐는데 위치가 안 변하면(슬롯카 벽 끼임 등) →
      //    예약 노드 해제하고 task 취소. wedged 로봇이 노드를 붙잡아 다른 로봇을 막는 걸 방지. ──
      if (t.moving && std::hypot(r.x - t.last_x, r.y - t.last_y) < 0.02) { t.no_move++; }
      else { t.no_move = 0; }
      t.last_x = r.x; t.last_y = r.y;
      check_plan_deadline(t);   // 계획 도착 시각 초과 → 재계획 요청(시간 계획일 때만)
      if (stuck_ticks_ > 0 && t.no_move > stuck_ticks_) {
        RCLCPP_ERROR(get_logger(), "[%s] %s ⚠ 무진행(슬롯카 stuck 추정) → 예약 해제·task 취소",
                     t.id.c_str(), t.robot.c_str());
        if (t.idx < t.path.size()) { traffic_->release_node(t.robot, t.path[t.idx]); }
        if (t.idx >= 1) { traffic_->release_node(t.robot, t.path[t.idx - 1]); }
        r.busy = false; r.task_id.clear();
        publish_task_state(t.id, "FAILED", t.robot);
        it = tasks_.erase(it); continue;
      }

      // 판정 반경: 마지막 노드는 정확히(arrive_radius_), 경유 노드는 미리(prefetch_radius_).
      // 경유 노드를 일찍 지난 것으로 보면 다음 노드 예약·발행이 앞당겨져, 로봇이 감속해
      // 서기 전에 새 목표를 받는다. 자세한 배경은 위 prefetch_radius 파라미터 주석 참고.
      const bool final_node = (t.idx + 1 >= t.path.size());
      double reach = arrive_radius_;
      if (!final_node && prefetch_radius_ > arrive_radius_ && t.idx >= 1) {
        // 레인 길이의 절반을 넘지 않게 깎는다 — 안 그러면 짧은 레인에서 노드를 건너뛴다.
        const Vertex & pv = graph_.vertex(t.path[t.idx - 1]);
        const double lane = std::hypot(pv.x - tv.x, pv.y - tv.y);
        reach = std::max(arrive_radius_, std::min(prefetch_radius_, 0.5 * lane));
      }

      if (t.moving && d < reach) {
        // 도착: 예약한 목표 노드는 그대로 소유(다음 출발 때 release). 엣지 예약은 없음.
        RCLCPP_INFO(get_logger(), "[%s] %s %s v%d", t.id.c_str(), t.robot.c_str(),
                    final_node ? "도착" : (reach > arrive_radius_ ? "선행통과" : "통과"),
                    t.path[t.idx]);
        t.idx++;
        t.moving = false;
        t.reroutes = 0;   // 노드 도달 = 진전 → 우회 카운터 리셋
        replan_streak_ = 0;   // 진전 → 재계획 backoff 도 원복
        t.wait_ticks = 0;   // 노드 도달 = 진전 → 타임드 우회 카운터 리셋
        if (t.idx >= t.path.size()) {
          if (t.patrol) {
            t.path = make_patrol_path(r, -1, route_for(t));   // 현재 위치서 canonical 랩 재생성(방향 유지)
            t.idx = 1; t.moving = false;
            RCLCPP_INFO(get_logger(), "[%s] %s 순회 1바퀴 → 계속", t.id.c_str(), t.robot.c_str());
          } else {
            traffic_->release_node(t.robot, t.path.back());     // 최종 노드 해제
            r.busy = false; r.task_id.clear();
            publish_task_state(t.id, "COMPLETED", t.robot);
            RCLCPP_INFO(get_logger(), "[%s] %s 작업 완료", t.id.c_str(), t.robot.c_str());
            it = tasks_.erase(it);
            continue;
          }
        }
      }

      // 이동 중이면 같은 경로를 주기적으로 다시 알려준다(놓친 명령 자가 복구).
      if (t.moving && resend_ticks_ > 0 && ++t.resend_tick >= resend_ticks_) {
        t.resend_tick = 0;
        std::vector<int> again;
        if (full_path_) {
          for (size_t k = t.idx; k < t.path.size(); ++k) { again.push_back(t.path[k]); }
        } else {
          again.push_back(t.path[t.idx]);
        }
        send_path(t.robot, r.x, r.y, again);
      }

      if (!t.moving) {
        int cur = t.path[t.idx - 1];
        int next = t.path[t.idx];
        MoveDecision dec = traffic_->request_move(t.robot, cur, next, compute_priority(t.robot, t));
        if (dec == MoveDecision::GRANT) {
          if (cur != next) { traffic_->release_node(t.robot, cur); }   // 출발 순간 이전 노드 해제 (cur==next=start==goal 케이스는 목표 유지)
          // full_path 면 남은 정점을 전부 실어 보낸다 — 로봇이 간선을 따라 멈춤 없이 간다.
          // ⚠️ 예약(traffic)은 여전히 **다음 한 노드**만 잡는다. 로봇이 여러 대면 예약하지
          //    않은 노드로 들어갈 수 있으므로, 다중 로봇 운영 전에 예약도 구간 단위로
          //    확장해야 한다. 그때까지는 `-p full_path:=false` 로 한 노드씩 되돌릴 수 있다.
          std::vector<int> route;
          if (full_path_) {
            for (size_t k = t.idx; k < t.path.size(); ++k) { route.push_back(t.path[k]); }
          } else {
            route.push_back(next);
          }
          send_path(t.robot, r.x, r.y, route);
          t.moving = true; t.wait_logged = false; t.stuck = false; t.wait_ticks = 0;   // 풀림 → escalation 해제
          RCLCPP_INFO(get_logger(), "[%s] %s → v%d (GRANT)", t.id.c_str(), t.robot.c_str(), next);
        } else if (dec == MoveDecision::DEADLOCK) {
          // 우회는 kMaxReroutes 번까지만(livelock 방지). 초과하면 우회 포기 → escalate + 대기.
          int goal_node = patrol_goal(t, next);   // 순회는 막힌 노드 다음 canonical(비-canonical 이면 최종목적지 폴백)
          auto reroute = (t.reroutes < kMaxReroutes && goal_node >= 0)
                       ? graph_.dijkstra(cur, goal_node, next) : std::vector<int>{};   // next 를 피해 우회
          if (reroute.size() >= 2) {
            t.reroutes++;
            RCLCPP_WARN(get_logger(), "[%s] %s ⚠ 교착 감지(v%d) → 우회 %zu nodes (재시도 %d/%d)",
                        t.id.c_str(), t.robot.c_str(), next, reroute.size(), t.reroutes, kMaxReroutes);
            t.path = reroute; t.idx = 1; t.moving = false; t.wait_logged = false; t.stuck = false;
          } else {
            if (!t.stuck) {   // 우회 불가 or 우회 반복초과(livelock) → 우선순위 최상위 escalate(주변이 비켜줌) 후 대기
              t.stuck = true;
              RCLCPP_WARN(get_logger(), "[%s] %s ⛔ 완전막힘/우회반복(v%d) → 우선순위 최상위 상향, 대기",
                          t.id.c_str(), t.robot.c_str(), next);
            }
            if (!t.wait_logged) { publish_task_state(t.id, "EXECUTING", t.robot); t.wait_logged = true; }
          }
        } else {   // WAIT
          if (blocked_by_stopped(next)) {   // 정지 로봇(영구 장애물)이 막음 → 우회
            auto reroute = graph_.dijkstra(cur, patrol_goal(t, next), next);   // 순회는 방향 유지
            if (reroute.size() >= 2) {
              RCLCPP_WARN(get_logger(), "[%s] %s ⤴ 정지 로봇(v%d) 우회 → %zu nodes",
                          t.id.c_str(), t.robot.c_str(), next, reroute.size());
              t.path = reroute; t.idx = 1; t.moving = false; t.wait_logged = false;
            } else if (!t.wait_logged) {
              RCLCPP_ERROR(get_logger(), "[%s] %s 정지 로봇(v%d) 막힘, 우회 불가",
                           t.id.c_str(), t.robot.c_str(), next);
              t.wait_logged = true;
            }
          } else {
            // 일반 WAIT(움직이는 로봇이 점유). 오래 안 풀리면 타임드 우회(작업·순회 모두).
            t.wait_ticks++;
            if (!t.wait_logged) {
              publish_task_state(t.id, "EXECUTING", t.robot);
              RCLCPP_WARN(get_logger(), "[%s] %s ⏸ v%d 점유중 → 양보 대기", t.id.c_str(), t.robot.c_str(), next);
              t.wait_logged = true;
            }
            if (t.wait_ticks >= kRerouteWaitTicks) {
              int goal_node = patrol_goal(t, next);   // 순회는 방향 유지(비-canonical 이면 최종목적지 폴백)
              auto reroute = (goal_node >= 0) ? graph_.dijkstra(cur, goal_node, next)
                                              : std::vector<int>{};
              if (reroute.size() >= 2) {
                RCLCPP_WARN(get_logger(), "[%s] %s ⤴ %ds 대기 → 우회 %zu nodes (v%d 회피)",
                            t.id.c_str(), t.robot.c_str(), (kRerouteWaitTicks * 150 + 500) / 1000,
                            reroute.size(), next);
                t.path = reroute; t.idx = 1; t.moving = false; t.wait_logged = false;
                t.wait_ticks = 0;
              } else {
                t.wait_ticks = 0;   // 우회 불가 → 카운터만 리셋, 계속 대기(다음 주기 재시도)
              }
            }
          }
        }
      }
      ++it;
    }
    publish_occupancy();
    publish_routes();
    publish_goals();
  }

  // 각 로봇의 남은 경로(현재 노드→목표)를 JSON 으로 발행(시각화용): {"robot":[[x,y],...]}.
  void publish_routes()
  {
    std::string j = "{";
    bool first = true;
    for (const auto & t : tasks_) {
      if (!first) { j += ","; }
      j += "\"" + t.robot + "\":[";
      size_t start = t.idx > 0 ? t.idx - 1 : 0;   // 현재 향해 출발한 노드부터
      for (size_t i = start; i < t.path.size(); ++i) {
        const Vertex & v = graph_.vertex(t.path[i]);
        if (i > start) { j += ","; }
        j += "[" + std::to_string(v.x) + "," + std::to_string(v.y) + "]";
      }
      j += "]";
      first = false;
    }
    j += "}";
    std_msgs::msg::String m; m.data = j;
    route_pub_->publish(m);
  }

  // 각 로봇의 최종 목적지 발행(배차 task만; 순회는 제외 → 콘솔에서 "—"). {"robot": goalVertex}.
  void publish_goals()
  {
    std::string j = "{";
    bool first = true;
    for (const auto & t : tasks_) {
      if (t.patrol) { continue; }              // 순회는 최종 목적지 없음
      if (!first) { j += ","; }
      j += "\"" + t.robot + "\":" + std::to_string(t.path.back());
      first = false;
    }
    j += "}";
    std_msgs::msg::String m; m.data = j;
    goal_pub_->publish(m);
  }

  // 교통 플러그인의 실제 예약(노드→로봇)을 JSON 으로 발행(시각화용).
  void publish_occupancy()
  {
    std::string j = "{";
    bool first = true;
    for (const auto & no : traffic_->occupancy()) {
      if (!first) { j += ","; }
      j += "\"" + std::to_string(no.first) + "\":\"" + no.second + "\"";
      first = false;
    }
    j += "}";
    std_msgs::msg::String m; m.data = j;
    occ_pub_->publish(m);
  }

  std::string mode_of(const std::string & robot) const
  {
    auto it = robot_mode_.find(robot);
    return it == robot_mode_.end() ? "IDLE" : it->second;
  }

  // ── libi_modes 8종 상태에 대한 질의 ────────────────────────────────────────
  // 상태 자체는 libi_modes 가 소유한다. 여기서는 배차·교통 판단에 필요한 것만 파생한다.

  // 새 task 를 받을 수 있는 상태인가.
  //   IDLE   : 대기 중 — 받을 수 있다
  //   PATROL : 순회 중 — 중단하고 받을 수 있다 (배차 1순위)
  // 나머지는 전부 제외한다:
  //   WORKING         이미 작업 중 (강제 배정 경로에서만 기존 task 를 취소하고 덮어쓴다)
  //   INTERACTING     이용자 응대 중 — 두고 가면 안 된다
  //   SECURITY_PATROL 야간 순찰 중 — 주간 태스크를 받지 않는다
  //   RETURNING       배터리 부족으로 복귀 중
  //   CHARGING        충전 중 (BATTERY_READY 넘어 IDLE 이 되면 그때 후보)
  //   ERROR           고장 — 스스로 움직이지 않는다
  static bool is_dispatchable(const std::string & state)
  {
    return state == "IDLE" || state == "PATROL";
  }

  // 스스로 비켜줄 수 없는 상태인가 → 그 노드는 영구 장애물로 보고 다른 로봇이 우회한다.
  // libi_modes 의 ERROR 는 "원인을 모르니 자율 주행을 재개하지 않는다"이므로 정확히 이 경우다.
  static bool is_immobile(const std::string & state)
  {
    return state == "ERROR";
  }

  // node 가 스스로 못 움직이는 로봇에 점유돼 있으면 true → 영구 장애물이므로 우회 대상.
  bool blocked_by_stopped(int node) const
  {
    for (const auto & no : traffic_->occupancy()) {
      if (no.first == node && is_immobile(mode_of(no.second))) { return true; }
    }
    return false;
  }

  double battery_of(const std::string & robot) const
  {
    auto it = robots_.find(robot);
    return it == robots_.end() ? 100.0 : it->second.battery;
  }

  // 교통 우선순위(단일 int): tier(가장 큼) > task 나이(오래된=큼) > 배터리(낮은=큼).
  //   tier: 순회=0 · 작업=1 · 충전복귀(CHARGE)=2 · 완전막힘(STUCK,동적)=3
  int compute_priority(const std::string & robot, const ActiveTask & t) const
  {
    int tier;
    if (t.stuck) { tier = 3; }                              // 완전 막힘(escalation)
    else if (mode_of(robot) == "RETURNING") { tier = 2; }   // 충전 복귀 (libi_modes RETURNING)
    else if (t.patrol) { tier = 0; }                        // 순회
    else { tier = 1; }                                      // 작업
    int seq = t.start_seq < kSeqMax ? t.start_seq : kSeqMax;
    int age = kSeqMax - seq;                                // 오래된(작은 seq)일수록 큼
    int bi = static_cast<int>(std::lround(battery_of(robot)));
    if (bi < 0) { bi = 0; } else if (bi > 100) { bi = 100; }
    int batt = 100 - bi;                                    // 낮은 배터리일수록 큼
    return tier * kTierStep + age * kAgeStep + batt;
  }

  // 이 로봇의 활성 task 가 순회(patrol)인가 — 순회는 배차로 중단 가능.
  bool is_on_patrol(const std::string & robot) const
  {
    for (const auto & t : tasks_) { if (t.robot == robot) { return t.patrol; } }
    return false;
  }

  // 로봇의 활성 task 취소: 점유(현재+예약 노드) 해제 후 task 제거, busy 해제.
  void cancel_task(const std::string & robot)
  {
    for (auto it = tasks_.begin(); it != tasks_.end();) {
      if (it->robot == robot) {
        if (it->idx >= 1 && it->idx - 1 < it->path.size()) {
          traffic_->release(robot, it->path[it->idx - 1]);
        }
        if (it->idx < it->path.size()) {
          traffic_->release(robot, it->path[it->idx]);
        }
        it = tasks_.erase(it);
      } else {
        ++it;
      }
    }
    auto r = robots_.find(robot);
    if (r != robots_.end()) { r->second.busy = false; r->second.task_id.clear(); }
  }

  // 순회 task 의 루트(주간=patrol_route_, 야간=security_patrol_route_).
  const std::vector<int> & route_for(const ActiveTask & t) const
  {
    return t.security ? security_patrol_route_ : patrol_route_;
  }

  // 주어진 순회 루프에서 node 의 다음 노드(방향 유지). node 가 루프에 없으면 -1.
  static int route_succ(const std::vector<int> & route, int node)
  {
    const int n = static_cast<int>(route.size());
    for (int i = 0; i < n; ++i) {
      if (route[i] == node) { return route[(i + 1) % n]; }
    }
    return -1;
  }

  // 우회 목적지 산출. 순회 task 는 **막힌 노드의 다음 canonical 노드**(방향 유지)를 목표로
  // 우회한다. 단 이미 우회 중이라 next 가 canonical 루프에 없으면(route_succ==-1) 현재 경로의
  // 최종 목적지로 폴백한다 — 그 값은 우회를 만들 때 route_succ 로 잡은 canonical 노드다.
  // (일반 작업 task 는 언제나 최종 목적지 t.path.back().)
  int patrol_goal(const ActiveTask & t, int next) const
  {
    if (!t.patrol) { return t.path.back(); }
    const int g = route_succ(route_for(t), next);
    return g >= 0 ? g : t.path.back();
  }

  // 순회 방향을 항상 **반시계(CCW)** 로 고정한다. 월드 좌표(위에서 본 평면, y 위쪽)에서
  // shoelace signed area > 0 이면 CCW. CW 로 만들어졌으면(예: auto 경계순회는 우수법이라
  // CW 일 수 있다) 루트를 뒤집어 CCW 로 맞춘다. 정점 3개 미만이면 방향 개념이 없어 그대로 둔다.
  void ensure_ccw(std::vector<int> & route) const
  {
    if (signed_area_2x(graph_, route) < 0.0) {   // CW → 뒤집어 CCW
      std::reverse(route.begin(), route.end());
    }
  }

  // 범위 밖 정점 인덱스를 버린다(경고). fms_service.sh 는 이름해석으로 이미 안전하지만,
  // `-p patrol_route:=`/`-p security_patrol_route:=` 로 직접 준 값은 검증되지 않았다 —
  // 그대로 두면 signed_area_2x/make_patrol_path 의 graph_.vertex(.at) 에서 기동 중 throw.
  void sanitize_route(std::vector<int> & route, const char * label) const
  {
    const int n = graph_.size();
    const size_t before = route.size();
    route.erase(std::remove_if(route.begin(), route.end(),
                [n](int v) { return v < 0 || v >= n; }), route.end());
    if (route.size() != before) {
      RCLCPP_WARN(get_logger(), "%s: 범위 밖 정점 인덱스 %zu개 무시(그래프 정점 %d개)",
                  label, before - route.size(), n);
    }
  }

  // 현재 위치에서 CCW 방향으로 한 바퀴 랩 경로 생성.
  // 진입점 = 로봇에서 **그래프거리(벽 고려, Dijkstra)** 최근접 순회 정점(직선거리 아님).
  // avoid_first>=0 이면 진입점의 다음 홉이 그 노드일 때 한 칸 앞에서 시작(방향은 유지).
  std::vector<int> make_patrol_path(const RobotInfo & r, int avoid_first,
                                    const std::vector<int> & route) const
  {
    const size_t n = route.size();
    if (n == 0) { return {}; }
    // 진입점: 로봇을 최근접 그래프 노드로 스냅 → 각 순회 정점까지 Dijkstra 경로비용 최소.
    // 도달불가 후보(빈 경로)는 건너뛴다(path_cost({})==0 을 최소로 오인 방지).
    const int snap = graph_.nearest(r.x, r.y);
    size_t k = 0; double bd = 1e18;
    for (size_t i = 0; i < n; ++i) {
      double dd;
      if (route[i] == snap) {
        dd = 0.0;                                    // 진입점이 곧 최근접 노드
      } else {
        const auto p = graph_.dijkstra(snap, route[i]);
        if (p.empty()) { continue; }                 // 도달불가 → skip
        dd = graph_.path_cost(p);
      }
      if (dd < bd) { bd = dd; k = i; }
    }
    if (bd > 1e17) {                                 // 전부 도달불가(비정상 그래프) → 직선거리 폴백
      for (size_t i = 0; i < n; ++i) {
        const Vertex & v = graph_.vertex(route[i]);
        const double dd = std::hypot(r.x - v.x, r.y - v.y);
        if (dd < bd) { bd = dd; k = i; }
      }
    }
    if (avoid_first >= 0 && route[(k + 1) % n] == avoid_first) { k = (k + 1) % n; }
    std::vector<int> path;
    // 로봇이 진입점(route[k]) 위에 있지 않으면 현재 노드를 맨 앞에 둔다 — 그래야 진입점이
    // **첫 목표**가 된다. 안 그러면 t.idx=1(start_patrol)이 path[0]=진입점을 건너뛰고
    // path[1]로 보내 진입점이 한 칸 밀린다. 일반 task 는 path[0]=로봇 시작노드(L338)라
    // 이 문제가 없다 — 여기서도 같은 규칙으로 맞춘다. 이미 진입점 위면(snap==route[k])
    // 붙이지 않아 즉시 다음 노드로 진행(랩 재생성 케이스가 그렇다).
    if (snap != route[k]) { path.push_back(snap); }
    for (size_t i = 0; i < n; ++i) { path.push_back(route[(k + i) % n]); }
    path.push_back(route[k]);   // 루프 닫기(마지막==처음)
    return path;
  }

  // 로봇을 주간 순회 루프에 태워 무한 순회 시작.
  void start_patrol(RobotInfo & r)
  {
    std::vector<int> path = make_patrol_path(r, -1, patrol_route_);
    if (path.size() < 2) { return; }
    r.busy = true;
    std::string tid = "P-" + r.name;
    r.task_id = tid;
    ActiveTask t; t.id = tid; t.robot = r.name; t.path = path;
    t.idx = 1; t.moving = false; t.patrol = true;   // 순회는 최저 tier
    t.start_seq = ++task_seq_;
    traffic_->request_move(r.name, path[0], path[0], compute_priority(r.name, t));   // 진입점 점유
    tasks_.push_back(t);
    publish_task_state(tid, "PATROL", r.name);
    RCLCPP_INFO(get_logger(), "[%s] %s 순회 시작 (시작 v%d, %zu nodes)",
                tid.c_str(), r.name.c_str(), path[0], path.size());
  }

  // 로봇을 야간 보안순회 루프에 태워 무한 순회 시작.
  void start_security_patrol(RobotInfo & r)
  {
    std::vector<int> path = make_patrol_path(r, -1, security_patrol_route_);
    if (path.size() < 2) { return; }
    r.busy = true;
    std::string tid = "SP-" + r.name;
    r.task_id = tid;
    ActiveTask t; t.id = tid; t.robot = r.name; t.path = path;
    t.idx = 1; t.moving = false; t.patrol = true; t.security = true;   // 순회 tier + 야간 루트
    t.start_seq = ++task_seq_;
    traffic_->request_move(r.name, path[0], path[0], compute_priority(r.name, t));   // 진입점 점유
    tasks_.push_back(t);
    publish_task_state(tid, "SECURITY_PATROL", r.name);
    RCLCPP_INFO(get_logger(), "[%s] %s 보안순회 시작 (시작 v%d, %zu nodes)",
                tid.c_str(), r.name.c_str(), path[0], path.size());
  }

  void on_set_plugins(const std::shared_ptr<SetPlugins::Request> req,
                      std::shared_ptr<SetPlugins::Response> res)
  {
    try {
      if (!req->dispatcher.empty()) {
        dispatcher_ = disp_loader_.createSharedInstance(req->dispatcher);
        active_disp_ = req->dispatcher;
      }
      if (!req->traffic.empty()) {
        traffic_ = traf_loader_.createSharedInstance(req->traffic);  // 잠금상태 초기화(테스트는 idle 시 스왑)
        active_traf_ = req->traffic;
      }
      res->ok = true;
    } catch (const std::exception & e) {
      res->ok = false; res->reason = e.what();
    }
    res->active_dispatcher = active_disp_;
    res->active_traffic = active_traf_;
    RCLCPP_INFO(get_logger(), "set_plugins → dispatcher=%s traffic=%s (ok=%d)",
                active_disp_.c_str(), active_traf_.c_str(), res->ok ? 1 : 0);
  }

  void on_reload(const std::shared_ptr<std_srvs::srv::Trigger::Request>,
                 std::shared_ptr<std_srvs::srv::Trigger::Response> res)
  {
    Navgraph g;
    if (g.load(navgraph_file_)) {
      graph_ = g;
      res->success = true;
      res->message = "reloaded " + std::to_string(graph_.size()) + " vertices";
      RCLCPP_INFO(get_logger(), "navgraph 리로드: %d 정점", graph_.size());
    } else {
      res->success = false;
      res->message = "load failed";
    }
  }

  void on_set_mode(const std::shared_ptr<SetRobotMode::Request> req,
                   std::shared_ptr<SetRobotMode::Response> res)
  {
    const std::string & m = req->mode;
    if (!kLibiModesStates.count(m)) {
      res->ok = false; res->reason = "bad_mode"; return;
    }
    // RETURNING 은 진행 중 task 를 유지한 채 우선순위만 올린다(복귀는 양보받아야 한다).
    // 그 외 상태 전환은 현재 task 취소(점유 해제).
    if (m != "RETURNING") {
      cancel_task(req->robot);
      for (const auto & no : traffic_->occupancy()) {   // 남은 점유(이전 정지 claim 등) 해제
        if (no.second == req->robot) { traffic_->release(req->robot, no.first); }
      }
    }
    robot_mode_[req->robot] = m;      // 미관측 로봇도 저장(관측되면 적용)
    if (is_immobile(m)) {             // 못 움직이는 로봇은 현재 노드를 장애물로 점유(다른 로봇이 우회)
      auto it = robots_.find(req->robot);
      if (it != robots_.end()) {
        int node = graph_.nearest(it->second.x, it->second.y);
        traffic_->request_move(req->robot, node, node, kStopPrio);
      }
    }
    res->ok = true; res->reason = "";
    RCLCPP_INFO(get_logger(), "로봇 모드: %s → %s", req->robot.c_str(), m.c_str());
  }

  // sim 테스트용 배터리 설정(각 로봇). 완주 관문·우선순위에 즉시 반영.
  void on_set_battery(const std::shared_ptr<SetBattery::Request> req,
                      std::shared_ptr<SetBattery::Response> res)
  {
    double v = req->value;
    if (v < 0.0) { v = 0.0; } else if (v > 100.0) { v = 100.0; }
    auto & r = robots_[req->robot];   // 미관측 로봇도 생성(관측되면 위치 갱신)
    r.name = req->robot;
    r.battery = v;
    res->ok = true; res->reason = "";
    RCLCPP_INFO(get_logger(), "배터리 설정: %s → %.0f%%", req->robot.c_str(), v);
  }

  // libi_modes 의 FsmState(JSON) 를 구독해 robot_mode_ 를 자동 갱신(#16). set_robot_mode(수동/sim)
  // 없이 실제 로봇 상태와 동기화된다. **관측만** 한다 — cancel_task 는 하지 않는다: 로봇이
  // fleet 가 준 일을 하느라 WORKING 이 된 걸 취소하면 fleet 자기 task 를 죽인다. ERROR 만
  // 장애물로 점유해 다른 로봇이 우회하게 한다(on_set_mode 의 immobile 처리와 동일).
  // ⚠️ robot_id(FsmState) 와 robot name(RmfRobotState) 이 같은 키여야 매칭(런타임 확인 필요).
  void on_fsm_state(const std_msgs::msg::String::SharedPtr msg)
  {
    const std::string robot = json_str_field(msg->data, "robot_id");
    const std::string state = json_str_field(msg->data, "current_state");
    if (robot.empty() || state.empty() || !kLibiModesStates.count(state)) { return; }
    if (mode_of(robot) == state) { return; }   // 변화 없으면 무시(매 발행마다 처리 방지)
    robot_mode_[robot] = state;
    if (is_immobile(state)) {
      auto it = robots_.find(robot);
      if (it != robots_.end()) {
        int node = graph_.nearest(it->second.x, it->second.y);
        traffic_->request_move(robot, node, node, kStopPrio);
      }
    }
    RCLCPP_INFO(get_logger(), "FsmState 동기화: %s → %s", robot.c_str(), state.c_str());
  }

  // 플랫 JSON 에서 "key":"value" 의 value 추출 (fleet_node 에 JSON 라이브러리 없음).
  static std::string json_str_field(const std::string & json, const std::string & key)
  {
    const std::string pat = "\"" + key + "\"";
    auto k = json.find(pat);
    if (k == std::string::npos) { return ""; }
    auto colon = json.find(':', k + pat.size());
    if (colon == std::string::npos) { return ""; }
    auto q1 = json.find('"', colon + 1);
    if (q1 == std::string::npos) { return ""; }
    auto q2 = json.find('"', q1 + 1);
    if (q2 == std::string::npos) { return ""; }
    return json.substr(q1 + 1, q2 - q1 - 1);
  }

  // plugins
  pluginlib::ClassLoader<DispatcherBase> disp_loader_;
  pluginlib::ClassLoader<TrafficBase> traf_loader_;
  std::shared_ptr<DispatcherBase> dispatcher_;
  std::shared_ptr<TrafficBase> traffic_;
  std::string active_disp_;
  std::string active_traf_;

  Navgraph graph_;
  std::string navgraph_file_;
  std::string fleet_name_;
  int stuck_ticks_{0};   // 0 = 무진행 감지 비활성
  double plan_deadline_slack_{3.0};   // 계획 도착 시각을 이만큼 넘기면 재계획(초)
  int replan_cooldown_ticks_{20};     // 재계획 최소 간격(틱)
  int replan_cooldown_{0};
  bool replan_requested_{false};      // 도착 마감 초과로 fleet_node 가 스스로 요청
  int replan_streak_{0};              // 진전 없이 이어진 재계획 횟수(backoff 지수)
  double arrive_radius_{kArriveDefault};   // 도착 판정 반경(m) — 맵 축척마다 다름
  double prefetch_radius_{kPrefetchDefault};  // 경유 노드 선행 통과 반경(m). 0 이면 꺼짐
  bool full_path_{false};                  // true 면 남은 정점 전부 전송(단일 로봇 디버깅용)
  int resend_ticks_{7};                    // 이동 중 경로 재발행 주기(틱). 0=끔
  bool patrol_{false};
  std::vector<int> patrol_route_;
  std::vector<int> security_patrol_route_;   // 야간 보안순회 루프(CCW 정규화)
  std::map<std::string, std::string> robot_mode_;   // 로봇 → PATROL|IDLE|STOP|CHARGE
  std::map<std::string, RobotInfo> robots_;
  //: 로봇별 마지막 /robot_state 수신 시각. **robots_ 는 만료되지 않으므로** 이것이
  //  "이 로봇 소식이 끊겼다"를 알 수 있는 유일한 근거다.
  std::map<std::string, rclcpp::Time> last_state_at_;
  std::vector<ActiveTask> tasks_;
  EnergyParams energy_;             // 배터리 소비 모델(완주 가능성 관문)
  int task_counter_{0};
  int path_seq_{0};
  int task_seq_{0};                 // 전체 task 생성 순서(우선순위 나이 tiebreak)

  rclcpp::Subscription<RmfRobotState>::SharedPtr state_sub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr fsm_sub_;   // libi_modes FsmState 자동구독(#16)
  rclcpp::Publisher<PathRequest>::SharedPtr path_pub_;
  rclcpp::Publisher<TaskState>::SharedPtr task_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr occ_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr plan_pub_;
  int plan_seq_{0};
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr route_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr goal_pub_;
  rclcpp::Service<SubmitTask>::SharedPtr srv_;
  rclcpp::Service<SetPlugins>::SharedPtr plugins_srv_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr reload_srv_;
  rclcpp::Service<SetRobotMode>::SharedPtr mode_srv_;
  rclcpp::Service<SetBattery>::SharedPtr battery_srv_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace libi_fleet

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<libi_fleet::FleetNode>());
  rclcpp::shutdown();
  return 0;
}
