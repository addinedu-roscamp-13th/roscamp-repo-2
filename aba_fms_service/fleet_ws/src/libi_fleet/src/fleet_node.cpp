#include <condition_variable>
#include <mutex>
#include <thread>
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
#include "libi_fleet_msgs/msg/fleet_goals.hpp"
#include "libi_fleet_msgs/msg/fleet_occupancy.hpp"
#include "libi_fleet_msgs/msg/fleet_plan.hpp"
#include "libi_fleet_msgs/msg/fleet_routes.hpp"
#include "libi_fleet_msgs/msg/robot_hold.hpp"
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
    // 기본 교통관제 = CBS + 가중 Space-Time A*. 계획이 밀리면 CbsTraffic 이 스스로
    // ReservationDeadlock 반응형으로 강등하므로 시간표가 깨져도 멈추지 않는다.
    // 진단용으로 끄려면 -p traffic_plugin:=libi_fleet::GrantAllTraffic.
    const std::string traf_name = declare_parameter<std::string>("traffic_plugin", "libi_fleet::CbsTraffic");
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
    // [2026-08-01] 한 번에 한 대만 들어갈 수 있는 구역(정점 인덱스, 공백 구분).
    //
    // arte2 의 충전소가 **대피선 없는 막다른 사슬**이다:
    //     (본관) ── 충전소입구(차수3) ── 충전소통로(차수2) ── 충전소(차수1)
    // 두 대가 들어가면 서로 못 지나간다 — 정면교차 전수검사 462쌍 중 실패 6건이
    // 전부 이 셋 사이였고 그 실패는 정답이다(계획으로 풀 수 없다).
    //
    // ⚠️ 여기 **번호를 박지 않는다.** 이름→인덱스 해석은 런처가 한다(patrol_route 와
    //    같은 관례). 오늘 `주차장` 정점이 사라졌는데 코드가 번호를 들고 있어 init-pose 가
    //    죽은 전례가 있다. 비워 두면 규칙이 안 걸릴 뿐 오작동은 없다.
    const std::string excl_s = declare_parameter<std::string>("exclusive_region", "");
    // [2026-08-01] 두 로봇이 물리적으로 겹치지 않으려면 필요한 최소 중심간 거리(m).
    //
    // nav2 의 inscribed 반경이 0.088 이므로 두 대는 0.176 이상 떨어져야 한다. arte2 는
    // 순회 통로와 서비스 지점이 0.151 간격이라 **서로 다른 정점을 잡아도 겹친다** —
    // 노드 예약의 전제가 지도에서 깨져 있다. 그래서 그만큼 가까운 정점끼리 동시 점유를 막는다.
    // 0 이하면 규칙을 끈다(예전 동작). 지도를 고치면 이 값이 자연히 안 걸리게 된다.
    const double min_sep = declare_parameter<double>("min_separation_m", 0.176);

    if (!graph_.load(navgraph_file_)) {
      RCLCPP_FATAL(get_logger(), "navgraph 로드 실패: %s", navgraph_file_.c_str());
      throw std::runtime_error("navgraph load failed");
    }
    {   // "7 19 20 21" 같은 공백 구분 인덱스열 → 집합
      std::istringstream is(excl_s);
      int v;
      while (is >> v) { if (v >= 0) { exclusive_region_.insert(v); } }
      if (!exclusive_region_.empty()) {
        std::string js;
        for (int i : exclusive_region_) { js += " v" + std::to_string(i); }
        RCLCPP_INFO(get_logger(), "[dispatch] 배타 구역(동시 1대):%s", js.c_str());
      }
    }
    active_disp_ = disp_name;
    active_traf_ = traf_name;
    dispatcher_ = disp_loader_.createSharedInstance(disp_name);
    traffic_ = traf_loader_.createSharedInstance(traf_name);
    // 근접 정점 상호배제를 플러그인에 알려 준다(위 min_separation_m 주석 참고).
    min_separation_m_ = min_sep;
    traffic_->set_min_separation(graph_, min_separation_m_);
    {
      int pairs = 0;
      for (int i = 0; i < graph_.size(); ++i) { pairs += static_cast<int>(traffic_->too_close_to(i).size()); }
      if (pairs > 0) {
        RCLCPP_WARN(get_logger(),
                    "[traffic] 정점 %d쌍이 %.3fm 보다 가깝습니다 — 동시 점유를 막습니다. "
                    "지도를 고치는 것이 근본 해결입니다.", pairs / 2, min_separation_m_);
      }
    }
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
    // ── QoS 계약 ────────────────────────────────────────────────────────────
    //
    // 기본값(RELIABLE·depth 10)을 그대로 쓰면 두 가지가 섞인다: **놓치면 안 되는 사건**과
    // **최신값만 의미 있는 상태 스트림**. 후자를 depth 10 으로 두면 구독자가 잠깐 느릴 때
    // 큐에 옛 값이 쌓였다가 몰려 나온다 — 화면이 과거를 재생한다.
    //
    // ⚠️ **RELIABLE 은 유지한다.** BEST_EFFORT 로 내리면 RELIABLE 로 구독하는 쪽과
    //    QoS 불일치가 되어 **조용히 한 건도 안 온다.** 여기 소비자(백엔드)는 기본값으로
    //    구독하므로, 바꾸려면 양쪽을 같이 고쳐야 한다. 지금 얻을 것은 깊이뿐이다.
    //
    //   사건(놓치면 안 됨)     : task_states, robot_hold, robot_path_requests → depth 10
    //   상태 스트림(최신만)    : occupancy, routes, goals                     → depth 1
    //   래치(늦게 붙어도 필요) : plan                                          → transient_local
    task_pub_ = create_publisher<TaskState>("/fms/task_states", rclcpp::QoS(10).reliable());
    occ_pub_ = create_publisher<libi_fleet_msgs::msg::FleetOccupancy>(
      "/fms/occupancy", rclcpp::QoS(1).reliable());
    // 시간 계획(CBS)의 **시간표**. 재계획할 때마다 한 번 낸다.
    // /fms/routes 는 좌표만 있어 "언제 어디" 를 알 수 없다 — 지연으로 예약 시각이 밀리는
    // 것을 밖에서 보려면 도착틱이 필요하다. 반응형 교통에서는 아무것도 안 나간다.
    plan_pub_ = create_publisher<libi_fleet_msgs::msg::FleetPlan>("/fms/plan", rclcpp::QoS(1).transient_local());
    route_pub_ = create_publisher<libi_fleet_msgs::msg::FleetRoutes>(
      "/fms/routes", rclcpp::QoS(1).reliable());
    // ── [2026-08-01] 주문 도중 순회로 떠나지 않게 붙잡는다 ──────────────────
    //
    // 주문 하나는 여러 다리(주행 → 팔 → 주행 → 팔)로 되어 있는데, fleet_node 는
    // **주행 다리만** 안다. 팔 다리는 상위 orchestrator 가 로봇에 직접 명령을 쏘고
    // 결과를 기다리므로 그 사이 fleet_node 는 로봇이 할 일이 없다고 본다.
    //
    // 실측 사고: 주행 다리를 끝내고 150 ms 뒤 그 로봇이 **순회를 시작해 서가를 떠났다.**
    // 그 순간 팔에게 그 서가에서 집으라는 명령이 나가는 중이었다.
    //   [orchestrator:t1] pinky-1 도착 v4 / 작업 완료
    //   [P-pinky-1]       pinky-1 순회 시작 (시작 v4) → v5 (GRANT)
    //
    // 그동안 이걸 막아 준 것은 로봇 상태기계가 WORKING 이라 순회 조건(mode==PATROL)이
    // 안 맞았던 것뿐이다 — **보호가 링크 하나에 얹혀 있었다.** 그 링크가 끊기면 로봇이
    // 주문 도중에 떠난다. 그래서 붙잡기를 주문 계약으로 끌어올린다.
    //
    // ⚠️ TTL 은 필수다. 푸는 쪽이 죽으면 로봇이 **영영 순회를 못 하게** 되기 때문이다.
    hold_sub_ = create_subscription<libi_fleet_msgs::msg::RobotHold>(
      "/fms/robot_hold", rclcpp::QoS(10).reliable(),
      [this](const libi_fleet_msgs::msg::RobotHold::SharedPtr m) { on_robot_hold(m); });
    goal_pub_ = create_publisher<libi_fleet_msgs::msg::FleetGoals>(
      "/fms/goals", rclcpp::QoS(1).reliable());

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

    planner_thread_ = std::thread(&FleetNode::planner_loop, this);
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

  ~FleetNode() override
  {
    {
      std::lock_guard<std::mutex> lk(planner_mu_);
      planner_stop_ = true;
    }
    planner_cv_.notify_all();
    if (planner_thread_.joinable()) { planner_thread_.join(); }
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

    // ── [2026-08-01] 목표 중복을 **여기서 막는다** ──────────────────────────
    //
    // 계획(cbs_planner)은 목표 도착 후 그 정점에 계속 앉아 있다고 본다(kNeverEnds).
    // 그래서 두 로봇의 목표가 같으면 **해가 아예 없다** — 계획이 통째로 실패하고
    // 시스템은 조용히 반응형으로 내려간다.
    //
    // 예전에는 그 사실을 계획이 실패한 **뒤에야** 로그로 알렸다(아래 replan 경고).
    // 알리는 것과 막는 것은 다르다. 배차가 안 겹치게 보장한다는 전제 위에 계획이
    // 서 있으므로, 그 보장을 실제로 여기서 세운다.
    //
    // ⚠️ 자동배차와 **강제배정 둘 다** 지나는 자리다. 강제배정만 열어 두면
    //    관제 화면에서 같은 정점을 두 번 지정하는 것으로 그대로 재현된다.
    //
    // 순회 목표는 세지 않는다 — 순회는 중단 가능해서 경매 후보로 들어가고(아래
    // is_on_patrol 참고), 배달이 잡으면 그 순회는 취소된다.
    for (const auto & t : tasks_) {
      if (t.patrol || t.path.empty()) { continue; }
      if (t.path.back() != goal) { continue; }
      if (!req->robot.empty() && t.robot == req->robot) { continue; }   // 같은 로봇 재지정은 허용
      res->accepted = false;
      res->reason = "goal_taken";
      RCLCPP_WARN(get_logger(),
                  "[dispatch] v%d 는 이미 %s 의 목표입니다 — 거절(goal_taken). "
                  "같은 정점을 두 로봇의 목표로 두면 시간표가 아예 안 선다.",
                  goal, t.robot.c_str());
      return;
    }

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
    // ── [2026-08-01] 선점당하는 로봇의 **커밋 구간을 존중한다** ──────────────
    //
    // 예전에는 무조건 `nearest(x,y)` 에서 새 경로를 짰다. 그런데 선점 대상이 이미
    // 레인 중간을 달리고 있으면 그건 **뒤돌아 가라**는 뜻이고, 그 사이에 cancel_task 가
    // 예약을 다 놓아 버린다 — 로봇은 아직 옛 nav2 목표로 달리는데 그 노드가 비어
    // 보여서, 다른 로봇이 그 자리를 잡을 수 있다. 실제로 이 창이 열려 있었다.
    //
    // 그래서 움직이는 중이면 **가고 있던 노드**에서 새 경로를 시작한다. replan_all_routes
    // 가 쓰는 것과 같은 규칙이다(`pr.start = t.moving ? path[idx] : path[idx-1]`).
    // 로봇은 하던 대로 그 노드까지 가고, 새 경로는 거기서부터 이어진다.
    int start = graph_.nearest(r.x, r.y);
    for (const auto & t : tasks_) {
      if (t.robot == robot && t.moving && t.idx < t.path.size()) { start = t.path[t.idx]; break; }
    }
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
    // ── [2026-08-01] 충전소 통로는 한 번에 한 대만 ────────────────────────
    //
    // arte2 의 충전소는 **대피선 없는 막다른 사슬**이다:
    //     (본관) ── 충전소입구(차수3) ── 충전소통로(차수2) ── 충전소(차수1)
    // 두 로봇이 이 안에서 서로 지나갈 방법이 없다. 실측으로 확인했다 — 정면교차
    // 전수검사 462쌍 중 실패 6건이 **전부 이 셋 사이**였고, 그 실패는 정답이다.
    //
    // 그러니 계획에 맡기지 말고 **배차에서 직렬화**한다. 안전한 쪽(뒤따라 들어가기)까지
    // 막지만, 한 대 폭 통로에서는 보수적인 편이 맞다.
    //
    // ⚠️ 정점 **번호가 아니라 이름**으로 잡는다. 오늘 `주차장` 정점이 사라졌는데
    //    코드가 번호를 들고 있어 init-pose 가 죽은 전례가 있다(pi.sh 의 충전소 수정).
    //    이름은 지도가 바뀌어도 따라온다. 못 찾으면 규칙이 그냥 안 걸릴 뿐 오작동은 없다.
    {
      const std::set<int> & spur = exclusive_region_;
      auto touches = [&](const std::vector<int> & p, size_t from) {
        for (size_t k = from; k < p.size(); ++k) { if (spur.count(p[k])) { return true; } }
        return false;
      };
      if (!spur.empty() && touches(path, 0)) {
        for (const auto & t : tasks_) {
          if (t.robot == robot || t.path.empty()) { continue; }   // 자기 것은 곧 대체된다
          const size_t from = t.idx > 0 ? t.idx - 1 : 0;          // 남은 경로만 본다
          if (!touches(t.path, from)) { continue; }
          res->accepted = false;
          res->reason = "exclusive_region_busy";
          RCLCPP_WARN(get_logger(),
                      "[dispatch] 충전소 통로에 %s 가 이미 들어가 있습니다 — 거절. "
                      "대피선이 없어 두 대가 서로 못 지나간다.", t.robot.c_str());
          return;
        }
      }
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
    std::set<int> taken_goals;   // 이번 스냅샷에서 이미 누가 목표로 잡은 정점
    for (auto & t : tasks_) {
      if (t.path.size() < 2 || t.idx < 1 || t.idx >= t.path.size()) { continue; }
      PlanRequest pr;
      pr.robot = t.robot;
      pr.start = t.moving ? t.path[t.idx] : t.path[t.idx - 1];   // 커밋 구간 존중
      // ── [2026-08-01] 순회도 계획에 넣는다 ────────────────────────────────
      //
      // 예전에는 `if (t.patrol) continue;` 로 통째로 뺐다. 그러면 CBS 는 **순회 로봇이
      // 없는 셈 치고** 시간표를 짜고, 실행에서 반응형 예약이 막아 대기 → 지연 → 강등 →
      // 재계획이 반복된다. 3대 중 한둘이 늘 순회 중이니 시간표가 계속 흔들렸다.
      //
      // 그렇다고 목표를 **랩 끝**으로 두면 안 된다. 계획은 목표 도착 후 그 정점에 영원히
      // 앉아 있다고 보므로(kNeverEnds), 주 통로 위의 랩 끝 정점이 통째로 잠긴다.
      // 그래서 **다음 한 정점만** 목표로 준다. 영구 점유가 한 칸 앞에만 생기고 다음
      // 재계획에서 갱신된다. (codex 와 A/B/C 를 견줘 이 방식을 골랐다.)
      pr.goal = t.patrol ? t.path[std::min(t.idx + 1, t.path.size() - 1)] : t.path.back();
      pr.priority = compute_priority(t.robot, t);
      if (pr.start == pr.goal) { continue; }   // 이미 목표 — 계획할 것이 없다

      // ⚠️ **목표가 겹치면 계획이 통째로 실패한다.** 계획은 목표 도착 후 그 정점에 영원히
      //    앉아 있다고 보므로(kNeverEnds) 두 로봇의 목표가 같으면 해가 아예 없다.
      //
      //    순회는 목표를 "다음 정점 하나" 로 두는데, **두 대가 한 칸 간격으로 같은 루프를
      //    돌면 그 다음 정점이 같아진다.** 실측: 3대 순회 시작 직후 v4 가 겹쳐
      //    `시간표를 세우지 못했습니다(0/3)` 가 뜨고 그 뒤로 재계획이 0회였다 —
      //    이름만 CBS 인 상태로 되돌아간다.
      //
      //    순회는 랩을 따라가면 되므로 **한 칸 더 밀어** 피한다. 그래도 겹치면 이번
      //    라운드에서 뺀다(그 로봇은 반응형 예약이 지켜 준다). 배달은 밀 수 없다 —
      //    목적지가 정해져 있으므로 거절 사유를 남기는 쪽이 맞다(on_submit 의 goal_taken).
      if (taken_goals.count(pr.goal)) {
        if (!t.patrol) { continue; }
        bool moved = false;
        for (size_t k = t.idx + 2; k < t.path.size(); ++k) {
          if (!taken_goals.count(t.path[k]) && t.path[k] != pr.start) {
            pr.goal = t.path[k];
            moved = true;
            break;
          }
        }
        if (!moved) { continue; }
      }
      taken_goals.insert(pr.goal);
      snap.robots.push_back(pr);
      planned.push_back(&t);
    }
    if (snap.robots.empty()) { return; }

    // ── [2026-08-01] 탐색은 **콜백 밖에서** 한다 ─────────────────────────────
    //
    // 이 노드는 `rclcpp::spin()` 단일 스레드다. 150 ms 타이머(도착 판정·통행 허가·
    // 목표 발행)와 서비스 4개, 구독 6개가 그 한 스레드를 공유한다. 여기서 CBS 를 직접
    // 돌리면 **탐색이 도는 동안 관제가 통째로 멈춘다** — 도착도 못 보고 통행 허가도
    // 못 내주고 배차 요청에도 응답을 못 한다.
    //
    // 지금 지도(22정점)에서는 1 ms 라 안 드러나지만, 틱을 잘게 쪼갠 설정에서 8.9 초까지
    // 측정된 적이 있다. 지도가 커지면 그날 바로 터진다.
    //
    // 그래서 스냅샷 조립(싼 일)만 여기서 하고 탐색은 워커에 넘긴다. 결과는 타이머가
    // 집어 간다. CbsTraffic 은 원래 탐색을 자기 잠금 밖에서 하도록 설계돼 있어
    // (GateStaysResponsiveDuringReplan 이 그걸 붙들고 있다) 다른 스레드에서 불러도 된다.
    if (!hand_to_planner(std::move(snap))) { return; }
  }

  // 워커가 계산해 둔 시간표가 있으면 적용한다. 타이머가 매 틱 부른다.
  //
  // ⚠️ **결과를 ActiveTask 포인터로 들고 오지 않는다.** 탐색이 도는 동안 task 가
  //    끝나거나 취소되면 그 포인터가 대롱거린다. 로봇 **이름으로 다시 찾는다** —
  //    사라졌으면 그 로봇 몫만 조용히 버린다.
  void apply_planner_result()
  {
    std::vector<PlannedRoute> routes;
    PlanSnapshot snap;
    {
      std::lock_guard<std::mutex> lk(planner_mu_);
      if (!result_ready_) { return; }
      routes = std::move(result_routes_);
      snap = std::move(result_snap_);
      result_ready_ = false;
    }
    apply_routes(routes, snap);
  }

  void apply_routes(const std::vector<PlannedRoute> & routes, const PlanSnapshot & snap)
  {
    if (routes.size() != snap.robots.size()) {
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
                  routes.size(), snap.robots.size(),
                  dup.empty() ? "" : (" 목표 중복:" + dup + " (같은 정점을 목표로 둘 수 없습니다)").c_str());
      // ⚠️ **빈 시간표를 반드시 낸다.** `/fms/plan` 은 transient_local 이라 마지막 값이
      //    계속 살아 있다 — 여기서 아무것도 안 내면 관제 화면은 **이미 버린 예약을**
      //    그대로 띄운 채로 남는다. 화면이 "예약 v7 에 12:03:41" 이라고 말하는데
      //    실제로는 반응형으로 돌고 있는, 코드는 멀쩡한데 화면만 거짓인 상태가 된다.
      publish_plan({}, now_sec(), traffic_->tick_seconds(), "시간표를 세우지 못했습니다");
      return;   // 기존 경로 그대로. 플러그인은 이미 반응형으로 내려가 있다.
    }

    const double epoch = now_sec();
    const double tick_sec = traffic_->tick_seconds();
    for (size_t i = 0; i < routes.size(); ++i) {
      if (routes[i].path.size() < 2) { continue; }
      // 이름으로 다시 찾는다 — 탐색 중에 사라진 task 는 그냥 건너뛴다.
      ActiveTask * tp = nullptr;
      for (auto & cand : tasks_) { if (cand.robot == routes[i].robot) { tp = &cand; break; } }
      if (tp == nullptr) { continue; }
      ActiveTask & t = *tp;
      if (t.path.size() < 2 || t.idx < 1 || t.idx >= t.path.size()) { continue; }
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
      // ⚠️ 순회는 **계획 구간 뒤에 canonical 랩을 도로 이어 붙인다.**
      //    CBS 목표를 다음 한 정점으로 줄였으므로, 그것만 남기면 랩이 잘려 나가고
      //    다음 틱에 "1바퀴 완주" 로 오인돼 랩이 계속 재생성된다.
      if (t.patrol) {
        t.plan_end_idx = static_cast<int>(np.size()) - 1;
        for (size_t k = t.idx + 2; k < t.path.size(); ++k) {
          np.push_back(t.path[k]);
          na.push_back(-1);            // 이어 붙인 꼬리에는 마감이 없다
        }
      } else {
        t.plan_end_idx = -1;
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
  void publish_plan(const std::vector<PlannedRoute> & routes, double epoch, double tick_sec,
                    const std::string & reason = "")
  {
    libi_fleet_msgs::msg::FleetPlan m;
    m.seq = ++plan_seq_;
    // ⚠️ `epoch` 는 **steady_clock** 이다 — 내부 마감 계산에는 그게 맞지만(NTP 로 튀지
    //    않는다) 부팅 후 경과초라 바깥에서는 아무 의미가 없다. 관제 화면이 "몇 시에
    //    예약"을 그리려면 벽시계가 따로 필요하다. 같은 순간의 system_clock 을 함께 싣는다.
    //    화면은 `epoch_wall + arrive*tick_sec` 로 예약 시각을 얻는다.
    //    (둘을 합치지 않는 이유: steady 를 벽시계로 바꾸면 NTP 보정 때 마감이 통째로 밀려
    //     멀쩡한 계획이 "지연" 으로 판정된다.)
    m.epoch = epoch;
    m.epoch_wall = std::chrono::duration<double>(
      std::chrono::system_clock::now().time_since_epoch()).count();
    m.tick_sec = tick_sec;
    m.drift_limit = traffic_ ? traffic_->drift_limit() : 0;
    m.reason = reason;
    for (const auto & r : routes) {
      libi_fleet_msgs::msg::RobotPlan rp;
      rp.robot = r.robot;
      rp.path = r.path;
      rp.arrive_tick = r.arrive_tick;
      // ⚠️ **좌표를 같이 싣는다.** 정점 인덱스만 주면 받는 쪽이 인덱스→좌표 표를 따로
      //    들고 있어야 하는데, 화면이 쓰는 waypoint.yaml 과 여기 navgraph 의 정점 순서가
      //    같다는 보장이 없다. 한 칸만 어긋나도 화면은 **조용히 엉뚱한 정점**에 예약
      //    시각을 붙인다(실측: 라벨이 아예 안 떴다). 좌표로 주면 그 가정 자체가 사라진다.
      for (int v : r.path) {
        const Vertex & vx = graph_.vertex(v);
        rp.xs.push_back(vx.x);
        rp.ys.push_back(vx.y);
      }
      m.robots.push_back(std::move(rp));
    }
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
    // 워커가 계산해 둔 시간표를 먼저 집어 간다 — 이번 틱의 이동 판단이 새 계획을 쓰도록.
    apply_planner_result();
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
      if (is_held(r.name)) { continue; }   // 주문 다리 사이 — 순회로 떠나면 안 된다
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
        // ⚠️ **순회가 계획 구간 끝에 닿으면 그 자리에서 재계획을 건다.**
        //    순회는 다음 한 정점까지만 계획된다. 그 칸을 넘어가 버린 뒤에 움직이면
        //    실행 게이트가 "계획에 없는 칸" 으로 보고 강등한다 — 순회를 계획에 넣어
        //    없애려던 churn 이 그대로 돌아온다. 넘기 **전에** 새 구간을 받아 둔다.
        if (t.patrol && t.plan_end_idx >= 0 && static_cast<int>(t.idx) >= t.plan_end_idx) {
          replan_requested_ = true;
        }
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
  // 각 로봇의 남은 경로(현재 노드→목표)를 발행(시각화용).
  //
  // ⚠️ 시간표(FleetPlan)와 달리 **매 틱 나간다.** 계획이 없어도(반응형 교통) 경로는 있고,
  //    화면은 그때도 경로를 보여야 한다.
  void publish_routes()
  {
    libi_fleet_msgs::msg::FleetRoutes m;
    for (const auto & t : tasks_) {
      libi_fleet_msgs::msg::RobotRoute r;
      r.robot = t.robot;
      const size_t start = t.idx > 0 ? t.idx - 1 : 0;   // 현재 향해 출발한 노드부터
      for (size_t i = start; i < t.path.size(); ++i) {
        const Vertex & v = graph_.vertex(t.path[i]);
        r.xs.push_back(v.x);
        r.ys.push_back(v.y);
      }
      m.routes.push_back(std::move(r));
    }
    route_pub_->publish(m);
  }

  // 각 로봇의 최종 목적지 발행(배차 task 만; 순회는 목적지가 없어 빠진다).
  void publish_goals()
  {
    libi_fleet_msgs::msg::FleetGoals m;
    for (const auto & t : tasks_) {
      if (t.patrol) { continue; }              // 순회는 최종 목적지 없음
      if (t.path.empty()) { continue; }
      m.robots.push_back(t.robot);
      m.goals.push_back(t.path.back());
    }
    goal_pub_->publish(m);
  }

  // 노드 점유 현황 발행(시각화용).
  void publish_occupancy()
  {
    libi_fleet_msgs::msg::FleetOccupancy m;
    for (const auto & [node, robot] : traffic_->occupancy()) {
      m.nodes.push_back(node);
      m.robots.push_back(robot);
    }
    occ_pub_->publish(m);
  }

  // ⚠️ 예전에는 std_msgs/String 에 JSON 을 실어 `find("\"hold\":true")` 로 파싱했다.
  //    스키마 검증이 없어 오타 하나가 런타임에야 드러났고, 실제로 발행 쪽 NameError 가
  //    "dispatch 실패"로 둔갑해 주문을 통째로 죽인 적이 있다. 타입 메시지로 바꿨다.
  void on_robot_hold(const libi_fleet_msgs::msg::RobotHold::SharedPtr msg)
  {
    if (msg->robot.empty()) { return; }
    if (!msg->hold) {
      if (hold_until_.erase(msg->robot)) {
        RCLCPP_INFO(get_logger(), "[hold] %s 해제", msg->robot.c_str());
      }
      return;
    }
    double ttl = static_cast<double>(msg->ttl_sec);
    if (ttl <= 0.0 || ttl > 600.0) { ttl = 120.0; }   // 터무니없는 값은 기본으로
    hold_until_[msg->robot] = now_sec() + ttl;
    RCLCPP_INFO(get_logger(), "[hold] %s 붙잡음 (%.0fs) — 주문 다리 사이",
                msg->robot.c_str(), ttl);
  }

  // 지금 붙잡혀 있나. 만료된 것은 여기서 치운다 — 푸는 쪽이 죽어도 스스로 풀린다.
  bool is_held(const std::string & robot)
  {
    auto it = hold_until_.find(robot);
    if (it == hold_until_.end()) { return false; }
    if (now_sec() >= it->second) {
      RCLCPP_WARN(get_logger(),
                  "[hold] %s 붙잡기 만료 — 푸는 쪽이 안 왔다(주문이 끊겼거나 팔이 늦다). "
                  "순회를 재개한다.", robot.c_str());
      hold_until_.erase(it);
      return false;
    }
    return true;
  }

  // ── 플래너 워커 ────────────────────────────────────────────────────────
  //
  // 스냅샷 하나만 들고 있는다(큐가 아니다). 새 요청이 오면 **덮어쓴다** — 낡은 스냅샷으로
  // 계산해 봐야 그 결과는 어차피 버려진다. 계획은 "지금 상태"에 대한 답이어야 한다.
  bool hand_to_planner(PlanSnapshot && snap)
  {
    {
      std::lock_guard<std::mutex> lk(planner_mu_);
      // graph_ 포인터는 노드 수명 내내 유효하다(멤버). 스냅샷은 값 복사라 안전하다.
      pending_snap_ = std::move(snap);
      pending_ready_ = true;
    }
    planner_cv_.notify_one();
    return true;
  }

  void planner_loop()
  {
    for (;;) {
      PlanSnapshot snap;
      {
        std::unique_lock<std::mutex> lk(planner_mu_);
        planner_cv_.wait(lk, [this] { return pending_ready_ || planner_stop_; });
        if (planner_stop_) { return; }
        snap = std::move(pending_snap_);
        pending_ready_ = false;
      }
      // ⚠️ 여기가 **긴 구간**이다. 어떤 잠금도 쥐지 않는다.
      std::vector<PlannedRoute> routes = traffic_->replan(snap);
      {
        std::lock_guard<std::mutex> lk(planner_mu_);
        // 계산하는 사이 새 요청이 왔으면 이 결과는 이미 낡았다 — 버리고 다시 돈다.
        if (pending_ready_) { continue; }
        result_routes_ = std::move(routes);
        result_snap_ = std::move(snap);
        result_ready_ = true;
      }
    }
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
    // ⚠️ **순회도 시간표를 받아야 한다.** 예전에는 여기서 replan 을 안 불러, 편대가
    //    전부 순회 중이면 CBS 가 한 번도 안 돌았다(실측: 3대 순회 60초에 재계획 0회).
    //    그 상태는 이름만 CBS 고 실제로는 반응형이다.
    replan_all_routes();
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
    replan_all_routes();   // 주간 순회와 같은 이유 — start_patrol 주석 참고
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
        traffic_->set_min_separation(graph_, min_separation_m_);      // 교체해도 규칙이 사라지면 안 된다
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
  double min_separation_m_{0.0};     // 두 로봇이 겹치지 않을 최소 중심간 거리(m)
  std::set<int> exclusive_region_;   // 동시 1대만 허용(충전소 사슬 등). 런처가 인덱스로 준다.
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
  rclcpp::Publisher<libi_fleet_msgs::msg::FleetOccupancy>::SharedPtr occ_pub_;
  rclcpp::Publisher<libi_fleet_msgs::msg::FleetPlan>::SharedPtr plan_pub_;
  int plan_seq_{0};
  rclcpp::Publisher<libi_fleet_msgs::msg::FleetRoutes>::SharedPtr route_pub_;
  rclcpp::Subscription<libi_fleet_msgs::msg::RobotHold>::SharedPtr hold_sub_;

  // 플래너 워커 — 탐색을 executor 스레드 밖으로 뺀다.
  std::thread planner_thread_;
  std::mutex planner_mu_;
  std::condition_variable planner_cv_;
  PlanSnapshot pending_snap_;
  PlanSnapshot result_snap_;
  std::vector<PlannedRoute> result_routes_;
  bool pending_ready_{false};
  bool result_ready_{false};
  bool planner_stop_{false};
  std::map<std::string, double> hold_until_;   // 로봇 → 붙잡기 만료 시각(steady 초)
  rclcpp::Publisher<libi_fleet_msgs::msg::FleetGoals>::SharedPtr goal_pub_;
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
