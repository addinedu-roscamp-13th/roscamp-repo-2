#include <condition_variable>
#include <mutex>
#include <thread>
#include <algorithm>
#include <cctype>
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
#include "libi_fleet_msgs/msg/node_block.hpp"
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

// 로봇 소식이 이만큼 끊기면 **그 로봇의 task 를 거두고 예약도 푼다.**
//
// ⚠️ `kRobotStaleSec`(10초)보다 훨씬 길게 잡는다. 10초는 "새 일감을 주지 않는다" 는
//    판단에 쓰는 값이라 짧아도 되지만, 예약 해제는 **되돌릴 수 없는 결정**이다.
//    브릿지가 잠깐 끊겼다 붙는 정도로 예약을 풀면, 그 노드에 실제로 서 있는 로봇을
//    향해 다른 로봇을 보내게 된다.
//
// ⚠️ 그렇다고 영원히 안 풀 수도 없다. 실측 2026-08-02: sim 로봇을 내렸는데
//    `[P-pinky-2] 시간표 재계획` 이 5초마다 계속 돌고 그 유령이 노드를 잡고 있었다 —
//    살아 있는 로봇의 경로가 그만큼 좁아진다. 아무도 안 푸는 예약은 결국 교통을 막는다.
//
//    그래서 **충분히 오래** 기다린 뒤 자동으로 거둔다. 로봇이 돌아오면 `/robot_state`
//    가 다시 오고 순회 루프가 새 task 를 준다 — 복구도 자동이다.
constexpr double kGhostTaskSec = 60.0;

// ── 반복 지연 간선에 붙이는 벌점 ────────────────────────────────────────────
//
// 재계획은 원래 **같은 길을 다시 낸다** — 회피 대상에 들어가는 건 ERROR 로봇뿐이라
// "저 길이 실제로 느리다"는 관측이 모델에 되먹여지지 않았다. 그래서 지연 → 재계획 →
// 같은 경로 → 또 지연이 돈다. 그 고리를 끊는다.
//
// 3회: 1~2회는 사람이 잠깐 지나갔거나 리로컬라이제이션이 튄 것일 수 있다. 3회 연속
//      같은 간선에서 마감을 놓치면 우연이 아니라 그 길의 성질이다.
// 120초: 마지막 실패 뒤 이만큼 조용하면 잊는다. 치운 장애물 때문에 영원히 돌아가는
//        일이 없게 한다. 순회 한 바퀴(≈10정점 × 몇 초)보다 넉넉하다.
// 10틱: 벌점은 **대안 경로를 이길 만큼** 커야 한다. 이 지도의 간선 하나가 대체로
//       몇 틱이라, 한 자릿수로는 재계획이 같은 길을 그대로 다시 고른다.
constexpr int kSlowEdgeMisses = 3;
constexpr double kSlowEdgeTtlSec = 120.0;
constexpr int kSlowEdgeExtraTicks = 10;
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

    // RETURNING 은 로봇 FSM 이 충전소 입구까지 직접 주행한다. FMS가 명령을
    // 중복 발행하면 두 실행기가 같은 Nav2 goal 을 덮으므로, 여기서는 UI 표시용
    // waypoint만 만든다. arte2 navgraph의 충전소통로는 v17이다.
    return_goal_node_ = declare_parameter<int>("return_goal_node", 17);

    // ── 시간 계획(CBS) 재계획 정책 ─────────────────────────────────────────
    // 재계획 최소 간격(틱). **0 = 지체를 안 순간 그 틱에 바로 다시 짠다.**
    //
    // 예약 시각을 넘긴 시간표는 그 순간부터 남에게 **틀린 통과 허가**를 내주고 있다.
    // 간격을 두는 것은 그 틀린 허가를 그만큼 더 유지하겠다는 뜻이라 0 으로 둔다.
    //
    // ponytail: 0 이면 backoff(:911) 도 같이 죽는다(0 × 2^n = 0). 복도가 막혀 계획으로
    //   안 풀리는 지연이면 CBS 가 150ms 마다 무한히 돈다 — 탐색은 잠금 밖이라 로봇이
    //   서지는 않지만 타이머 콜백을 먹는다(cbg_timer_ 는 MutuallyExclusive). CPU 가
    //   실제로 문제가 되면 `replan_cooldown_ticks:=1` 로 바닥만 깔면 되고, 그러면
    //   backoff 도 같이 살아난다(최대 16틱 ≈ 2.4초). 빌드 불필요.
    replan_cooldown_ticks_ = declare_parameter<int>("replan_cooldown_ticks", 0);

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

    cbg_timer_ = create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);
    cbg_srv_   = create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);
    cbg_sub_   = create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);

    rclcpp::SubscriptionOptions sub_opt;
    sub_opt.callback_group = cbg_sub_;
    state_sub_ = create_subscription<RmfRobotState>(
      "/robot_state", 10,
      std::bind(&FleetNode::on_robot_state, this, std::placeholders::_1), sub_opt);
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
      [this](const libi_fleet_msgs::msg::RobotHold::SharedPtr m) { on_robot_hold(m); }, sub_opt);

    // ── 정점 차단 (2026-08-03) ────────────────────────────────────────────
    // 사람이 막았거나 도킹이 잡아 둔 정점. 유효시간이 지나면 스스로 푼다
    // (RobotHold 와 같은 이유 — 푸는 쪽이 죽어도 길이 영영 막히면 안 된다).
    // (node, owner) 로 나눠 잡는다 — 사람 차단과 서가 잠금이 같은 정점을 같이
    // 잡을 수 있고, 한쪽의 해제가 남의 차단까지 지우면 안 된다.
    node_block_sub_ = create_subscription<libi_fleet_msgs::msg::NodeBlock>(
      "/fms/node_block", rclcpp::QoS(10).reliable(),
      [this](const libi_fleet_msgs::msg::NodeBlock::SharedPtr m) {
        std::lock_guard<std::recursive_mutex> lk(state_mu_);
        const auto key = std::make_pair(m->node, m->owner);
        double ttl = static_cast<double>(m->ttl_sec);
        if (ttl > 600.0) { ttl = 600.0; }
        if (ttl <= 0.0) {
          blocked_until_.erase(key);
          blocked_reason_.erase(key);
          RCLCPP_INFO(get_logger(), "[block] 정점 %d 해제 owner=%s (%s)",
                      m->node, m->owner.c_str(), m->robot.c_str());
        } else {
          blocked_until_[key] = now_sec() + ttl;
          blocked_reason_[key] = m->reason;
          RCLCPP_WARN(get_logger(), "[block] 정점 %d 차단 %.0fs 사유=%s owner=%s 로봇=%s",
                      m->node, ttl, m->reason.c_str(), m->owner.c_str(), m->robot.c_str());
          // ── 커밋 노드가 막혔다 → **떠나온 정점으로 되돌린다** (2026-08-03) ──
          //
          //   보고한 로봇은 막힌 정점 B 에 **못 닿은 채** A–B 레인에 서 있다. 예전에는
          //   여기서 FMS 가 로봇에게 `backup` 을 직접 쏴서 A 로 물렸는데, 그 이동은
          //   **예약 체계 밖**이었다 — 예약을 확인하지도 잡지도 않고 움직이니 A 에
          //   다른 로봇이 들어와 정면으로 만날 수 있었다(codex 3차 P0).
          //
          //   대신 여기서 **예약을 원자적으로 갈아탄다**:
          //     ① `request_move(A, A)` 로 A 를 점유 claim 한다(`from==to` 는 claim,
          //        `cbs_traffic.hpp:290`). 남이 쥐고 있으면 GRANT 가 안 나온다.
          //     ② 성공했을 때만 B 를 놓고 `moving=false` 로 내린다.
          //   실패하면 **아무것도 안 바꾼다** — B 를 쥔 채 기다린다. 그 편이
          //   "아무 노드도 안 쥔 채 레인에 서 있는" 상태보다 안전하다.
          //
          //   `moving=false` 가 되면 그 뒤는 전부 기존 기계장치가 맡는다:
          //     · `plan_start_for` 가 A 를 출발점으로 준다(계획·예약·실제가 일치)
          //     · `!t.moving` 분기가 A 에서 다음 홉을 정상 `request_move` 로 요청한다
          //     · B 는 `is_node_blocked` 게이트에 걸려 WAIT → 타임드 우회
          //     · `send_path` 가 **로봇의 실제 좌표**에서 경로를 내므로 nav2 가
          //       지금 자리에서 알아서 몬다 — 따로 후진 명령을 쏠 이유가 없다
          // ⚠️ 이름은 **정규화해서** 비교한다 — 근거는 `norm_robot` 머리말(실기 P0).
          const std::string want = norm_robot_name(m->robot);
          bool known = false;
          for (const auto & t : tasks_) {
            if (norm_robot_name(t.robot) == want) { known = true; break; }
          }
          if (!known && !tasks_.empty()) {
            std::string names;
            for (const auto & t : tasks_) { names += " " + t.robot; }
            RCLCPP_WARN(get_logger(),
                        "[block] 차단 보고 로봇 '%s' 와 이름이 맞는 작업이 없다 "
                        "(작업 중:%s) — 후퇴·재전송 중단이 안 걸립니다",
                        m->robot.c_str(), names.c_str());
          }
          for (auto & t : tasks_) {
            if (norm_robot_name(t.robot) != want || !t.moving) { continue; }
            if (t.idx < 1 || t.idx >= t.path.size()) { continue; }
            if (t.path[t.idx] != m->node) { continue; }
            const int back_to = t.path[t.idx - 1];

            // ⚠️ [2026-08-03] **`moving` 은 claim 성공 여부와 무관하게 내린다.**
            //
            //   이게 실제 레버다. `moving` 이 true 인 동안 타이머가 **1초마다 B행 목표를
            //   다시 쏜다**(`resend_ticks_`, :1534). 로봇이 사람을 보고 스스로 멈춰도
            //   그 재전송이 계속 밀어붙인다 — 사람 쪽으로.
            //
            //   codex 4차 P0: "예약만 A로 바뀌고 실제 로봇은 A–B 레인/기존 B행 명령에
            //   남는다". 처음엔 claim 성공일 때만 내렸는데, **실패 경로에서 바로 그
            //   재전송이 살아 있었다.** 내리면 `!t.moving` 분기로 가고, 거기서 B 는
            //   `is_node_blocked` 게이트에 걸려 WAIT 이므로 **B 로는 아무것도 안 나간다.**
            //   대신 `wait_ticks` 가 쌓여 타임드 우회가 돌고, 우회가 잡히면 그때
            //   `send_path` 가 **로봇의 실제 좌표에서** 새 경로를 낸다.
            t.moving = false;
            t.wait_logged = false;
            t.wait_ticks = 0;

            // 예약 교체는 claim 이 성공할 때만. 실패하면 B 를 쥔 채 남는다 —
            // 그 점유가 이 레인의 정면 진입을 막는 유일한 장치다(codex 3차 "P0 방어 성공").
            if (traffic_->request_move(t.robot, back_to, back_to,
                                       compute_priority(t.robot, t)) != MoveDecision::GRANT) {
              RCLCPP_WARN(get_logger(),
                          "[%s] %s v%d 차단 — 되돌아갈 v%d 를 남이 쥐고 있다. "
                          "B 예약 유지·목표 재전송 중단하고 대기",
                          t.id.c_str(), t.robot.c_str(), m->node, back_to);
              continue;
            }
            traffic_->release_node(t.robot, m->node);
            RCLCPP_INFO(get_logger(), "[%s] %s v%d 차단 → v%d 로 되돌림(예약 교체 완료)",
                        t.id.c_str(), t.robot.c_str(), m->node, back_to);
          }
        }
        // ⚠️ **세대를 올린다.** 안 올리면 이 차단을 **모른 채 이미 계산이 끝난** 옛
        //    결과가 같은 세대로 통과해 그대로 적용된다 — 차단 정점을 지나는 경로가
        //    잠깐 발행될 수 있다(codex 검토 P1). `apply_planner_result` 의 세대 검사가
        //    그걸 걸러 주는 장치인데, 세대를 안 올리면 그 장치가 안 돈다.
        ++state_gen_;
        // 사유를 계획에 실어 보낸다 — 화면이 "사람 때문" 을 바로 읽는다.
        replan_all_routes(ttl <= 0.0
                            ? std::string("정점 차단 해제")
                            : std::string("정점 차단(") + m->reason + ")");
      }, sub_opt);
    goal_pub_ = create_publisher<libi_fleet_msgs::msg::FleetGoals>(
      "/fms/goals", rclcpp::QoS(1).reliable());

    srv_ = create_service<SubmitTask>(
      "/fms/submit_task",
      std::bind(&FleetNode::on_submit, this, std::placeholders::_1, std::placeholders::_2),
      rclcpp::ServicesQoS(), cbg_srv_);
    plugins_srv_ = create_service<SetPlugins>(
      "/fms/set_plugins",
      std::bind(&FleetNode::on_set_plugins, this, std::placeholders::_1, std::placeholders::_2),
      rclcpp::ServicesQoS(), cbg_srv_);
    reload_srv_ = create_service<std_srvs::srv::Trigger>(
      "/fms/reload_navgraph",
      std::bind(&FleetNode::on_reload, this, std::placeholders::_1, std::placeholders::_2),
      rclcpp::ServicesQoS(), cbg_srv_);
    mode_srv_ = create_service<SetRobotMode>(
      "/fms/set_robot_mode",
      std::bind(&FleetNode::on_set_mode, this, std::placeholders::_1, std::placeholders::_2),
      rclcpp::ServicesQoS(), cbg_srv_);
    battery_srv_ = create_service<SetBattery>(
      "/fms/set_battery",
      std::bind(&FleetNode::on_set_battery, this, std::placeholders::_1, std::placeholders::_2),
      rclcpp::ServicesQoS(), cbg_srv_);
    // libi_modes 상태 자동 구독(#16) — 브릿지가 /libi/fsm_state 로 올린다. set_robot_mode 불필요.
    fsm_sub_ = create_subscription<std_msgs::msg::String>(
      "/libi/fsm_state", 10,
      std::bind(&FleetNode::on_fsm_state, this, std::placeholders::_1), sub_opt);

    planner_thread_ = std::thread(&FleetNode::planner_loop, this);
    timer_ = create_wall_timer(std::chrono::milliseconds(150),
                               std::bind(&FleetNode::on_timer, this), cbg_timer_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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
        // 소식이 끊긴 로봇은 경매 후보에서 뺀다. 옛 좌표로 낙찰되면 주문이
        // 유령에게 가고, 그 주문은 아무도 수행하지 않는다(`state_stale` 머리말).
        if (state_stale(kv.first)) { continue; }
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
    ++state_gen_;   // 상태가 바뀌었다 — 계산 중인 시간표는 낡았다
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
  // `why` — 이번 재계획을 **왜** 하는가. `/fms/plan.reason` 으로 그대로 나가고,
  // FMS 백엔드가 그걸 로봇·패널로 중계한다(관제에서 사람/지연을 가리기 위해서다,
  // 2026-08-03 사용자 요구). 비워 두면 예전과 같이 사유 없는 계획이다.
  void replan_all_routes(const std::string & why = "")
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
    // 사람·도킹이 막아 둔 정점도 같이 피한다. 기존 "못 움직이는 로봇" 과 같은 취급이다.
    for (int n : active_blocks()) {
      // CBS의 blocked 목록은 로봇별 예외를 표현하지 못한다. 서가 도킹을
      // 배정받은 로봇 자신의 최종 정점은 목록에서 빼고, 실제 진입은 아래의
      // robot-aware 게이트가 다른 로봇을 계속 막는다.
      if (!is_owned_shelf_dock_goal(n)) { snap.blocked.push_back(n); }
    }
    snap.slow_edges = slow_edges_now();

    std::vector<ActiveTask *> planned;
    std::set<int> taken_goals;   // 이번 스냅샷에서 이미 누가 목표로 잡은 정점
    for (auto & t : tasks_) {
      if (t.path.size() < 2 || t.idx < 1 || t.idx >= t.path.size()) { continue; }
      PlanRequest pr;
      pr.robot = t.robot;
      // ⚠️ [2026-08-03] **차단된 커밋 노드에서 계획을 시작하지 않는다.**
      //
      //   사람이 막은 정점은 곧 로봇이 향하던 `t.path[t.idx]` 다. 그런데 `t.moving` 은
      //   차단 때 아무도 안 내리므로, 예전에는 그 노드를 `pr.start` 로 줬다 — CBS 가
      //   **"로봇이 이미 거기 도착했다"** 고 가정하고 그 너머에서 새 경로를 짠 것이다.
      //   실제로는 사람 앞에서 못 닿고 서 있어서, 그 계획의 첫 홉이 **사람 너머**에서
      //   시작한다. 로봇에 내려보내면 nav2 가 사람을 뚫으려다 실패한다
      //   (실기 2026-08-03: `Failed to create plan with tolerance of: 0.100000`).
      //
      //   실제로 로봇이 있는 곳 — 떠나온 직전 정점 — 에서 짠다. 사람 차단 뒤 FMS 가
      //   보내는 `backup` 도 정확히 그 정점으로 물린다(`_send_backup_from_block`),
      //   그래서 계획과 실제 위치가 같은 곳을 가리킨다.
      //
      //   판정은 순수 함수로 뺐다(`fleet_task.hpp`) — 여기서는 잡을 수 없어 시험이 없던
      //   자리다. `test_plan_start.cpp` 가 되돌림까지 붙들고 있다.
      //   `committed_from` 은 "지금 타고 있는 간선" 이라, 커밋 노드가 막혔으면 그 간선을
      //   끝내지 못하므로 예산도 주지 않는다(-1).
      if (!plan_start_for(t.path, t.idx, t.moving, snap.blocked,
                          pr.start, pr.committed_from)) { continue; }
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
      // ⚠️ 목표는 **계획 출발점 바로 다음 한 정점**이다 — `t.idx + 1` 로 박으면 서 있을
      //    때 구간이 두 홉이 되어 CBS 가 랩 정점을 건너뛴다(`patrol_goal_index` 머리말).
      const std::size_t goal_i = patrol_goal_index(t.idx, t.moving, t.path.size());
      pr.goal = t.patrol ? t.path[goal_i] : t.path.back();
      pr.priority = compute_priority(t.robot, t);
      if (pr.start == pr.goal) {
        // ⚠️ [2026-08-03] **조용히 넘기지 않는다.** 이 로봇은 이번 라운드 계획에서
        //    빠지는데, 다른 로봇이 하나라도 있으면 "재계획 성공" 로그가 그대로 찍혀
        //    이 로봇의 시간표도 새로 짜인 것처럼 보인다. 실제로는 옛 `plan_epoch`·
        //    `plan_arrive` 가 그대로 남아, 화면 카운트다운이 계속 흘러가고
        //    `check_plan_deadline` 은 갱신되지 않은 마감을 본다(codex 지적 P1).
        //
        //    ⚠️ [2026-08-03 수정] 처음엔 여기서 `plan_arrive` 를 **비웠다.** 그러면
        //       `check_plan_deadline` 이 첫 줄에서 나가 **마감 감시가 통째로 꺼진다** —
        //       "0이 되면 무조건 재계획" 이라는 요구를 정면으로 깬다(codex 검토 P1).
        //       아직 이동 중인 커밋 간선의 마감까지 같이 사라졌다.
        //       그래서 **버리지 않고 남긴다.** 낡았지만 그 마감이 지나면 재계획이
        //       걸리고, 그게 바로 우리가 원하는 동작이다. 대신 조용히 넘기지 않게
        //       **로그는 남긴다** — 이 로봇만 이번 라운드에서 빠졌다는 사실이 보여야 한다.
        t.plan_excluded = true;
        if (!t.plan_arrive.empty() && !t.excluded_logged) {
          t.excluded_logged = true;
          RCLCPP_INFO(get_logger(),
                      "[%s] %s 계획에서 제외(출발==목표 v%d) — 시간표는 옛것 그대로다",
                      t.id.c_str(), t.robot.c_str(), pr.goal);
        }
        continue;
      }

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
        // 목표 **다음** 칸부터 민다 — 기준은 `goal_i` 다(`t.idx + 2` 로 박으면 서 있을 때
        // 목표가 `t.idx` 라 그 바로 다음인 `t.idx + 1` 을 건너뛴다).
        for (size_t k = goal_i + 1; k < t.path.size(); ++k) {
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
    if (snap.robots.empty()) {
      // 계획할 로봇이 하나도 없다(전부 목표에 도착했거나 목표가 겹쳐 밀려났다).
      // 조용히 나가면 래치된 옛 시간표가 화면에 그대로 남는다 — `clear_plan_once` 머리말.
      clear_plan_once("계획할 로봇 없음");
      return;
    }

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
    if (!hand_to_planner(std::move(snap), why)) { return; }
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
    std::string why;
    {
      std::lock_guard<std::mutex> lk(planner_mu_);
      if (!result_ready_) { return; }
      const uint64_t gen = result_gen_;
      routes = std::move(result_routes_);
      snap = std::move(result_snap_);
      why = result_reason_;
      result_ready_ = false;
      if (gen != state_gen_) {
        RCLCPP_DEBUG(get_logger(),
                     "[traffic] 낡은 시간표 폐기(세대 %lu≠%lu) — 계산 중에 상태가 바뀌었다",
                     gen, state_gen_);
        // ⚠️ [2026-08-03] **여기서 다시 요청을 건다.** 예전 주석은 "다음 요청이 곧 온다"
        //    고 했지만 보장이 없다 — `replan_requested_` 는 이 계산을 띄울 때 이미
        //    소비됐고(`service_replan_requests`), 살아 있는 task 가 **또** 마감을
        //    넘겨야만 다시 선다. 그 사이 시간표는 옛 값 그대로다(codex 지적 P1).
        request_replan("계획 재계산(세대 불일치)");
        return;   // 계산 중에 상태가 바뀌었다 — 위에서 다시 요청해 뒀다.
      }
    }
    apply_routes(routes, snap, why);
  }

  void apply_routes(const std::vector<PlannedRoute> & routes, const PlanSnapshot & snap,
                    const std::string & why = "")
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

      // 워커는 콜백 밖에서 도므로, 결과가 돌아오는 짧은 사이 로봇이 커밋 정점에
      // 이미 닿을 수 있다. 아직 `on_timer`가 도착을 처리하기 전이라 세대는 같지만,
      // 이 결과를 적용하면 `t.idx = 1`로 되감겨 방금 지난 정점을 다시 목표로 낸다.
      //
      // 실기에서는 `v14 통과 → v10 GRANT → (옛 계획 적용) v14 통과`가 150ms마다
      // 반복되어, 지도에는 로봇이 노드 위인데 지연만 커지고 실제 명령은 앞뒤로
      // 흔들렸다. 현재 AMCL이 해당 계획의 앵커 반경 안이면 결과를 버린다. 이번 틱의
      // 정상 도착 처리가 state_gen을 올리고, 그 뒤의 새 스냅샷이 다음 정점부터 짠다.
      const std::size_t anchor_i = planner_apply_anchor_index(
        t.moving, routes[i].path.size());
      auto rit = robots_.find(t.robot);
      if (anchor_i < routes[i].path.size() && rit != robots_.end()) {
        const Vertex & anchor = graph_.vertex(routes[i].path[anchor_i]);
        const double anchor_d = std::hypot(rit->second.x - anchor.x, rit->second.y - anchor.y);
        if (anchor_d < arrive_radius_) {
          RCLCPP_DEBUG(get_logger(),
                       "[%s] %s 도착 직전 낡은 시간표 폐기(v%d, %.3fm)",
                       t.id.c_str(), t.robot.c_str(), routes[i].path[anchor_i], anchor_d);
          request_replan("도착 직전 시간표 재계산");
          continue;
        }
      }
      std::vector<int> np, na;
      np.insert(np.end(), routes[i].path.begin(), routes[i].path.end());
      na.insert(na.end(), routes[i].arrive_tick.begin(), routes[i].arrive_tick.end());
      // ⚠️ [2026-08-07] **계획이 이미 그 정점에서 시작하면 또 붙이지 않는다.**
      //
      //   `t.moving` 은 스냅샷을 뜰 때와 결과를 적용할 때가 **다른 값일 수 있다.**
      //   통과 직후에는 `moving=false` 라(1599행) `plan_start_for` 의 !ride 갈래가
      //   **서 있는 정점**(`path[idx-1]`)을 출발점으로 준다. 그런데 워커가 도는 사이
      //   GRANT 가 오면 `moving=true` 로 뒤집히고, 여기서 그 정점을 한 번 더 앞에
      //   붙여 `np = [v8, v8, v7 …]` 이 된다. `t.idx = 1` 이므로 **방금 지난 v8 이
      //   다시 목표**가 된다.
      //
      //   실기 2026-08-07(pinky-3 순회): 모든 정점이 `선행통과 v8` → 1.5s 뒤
      //   `통과 v8` 로 **두 번** 찍혔다. 관제 화면에서는 하이라이트가 다음 노드로
      //   갔다가 자기 노드로 되돌아온 뒤 다시 다음으로 갔고, 정점마다 1.5~2.5초를
      //   되감기로 흘렸다. 로봇 쪽에서는 `주행 watchdog 재시도 1/3: motion progress
      //   watchdog expired` 로 드러난다.
      //
      //   ⚠️ 두 번째 통과가 `선행통과` 가 아니라 **`통과`** 로 찍히는 것이 지문이다 —
      //      라벨이 `통과` 이려면 `reach == arrive_radius_` 여야 하고, 그건
      //      `0.5 × lane <= arrive_radius_`, 즉 **앞뒤 정점이 같을 때**뿐이다(1567행).
      //
      //   `ride` 갈래(스냅샷도 이동 중)에서는 계획이 커밋 정점에서 시작하므로 여기
      //   조건이 안 걸리고 예전과 똑같이 한 칸을 살린다.
      //   판정은 순수 함수로 뺐다(`fleet_task.hpp`) — `test_plan_commit_head.cpp` 가
      //   되돌림까지 붙들고 있다.
      if (should_prepend_commit_head(np, t.moving, t.path[t.idx - 1])) {
        np.insert(np.begin(), t.path[t.idx - 1]);   // 진행 중인 한 칸을 살린다
        na.insert(na.begin(), -1);                  // 이미 떠난 정점 — 마감 없음
        // ── 커밋 노드(지금 향해 가는 정점)의 마감 ──────────────────────────
        //
        // 새 계획은 이 정점을 t=0 으로 잡지만 로봇은 아직 **가는 중**이라 남은 주행시간이
        // 있다. 0 을 그대로 마감으로 쓰면 계획이 세워지자마자 초과가 되어 재계획이 계속
        // 돈다(실측: `계획 도착(0틱) 초과 3.1s` 가 3초마다 반복).
        //
        // ⚠️ [2026-08-02] **예전엔 그래서 마감을 통째로 버렸다(`na[1] = -1`).**
        //    그러면 `check_plan_deadline` 이 첫 줄에서 빠져나가 **그 구간이 무감지**가
        //    된다. 주석은 "stuck 감지(no_move)와 drift_limit 이 담당한다" 고 적었는데
        //    둘 다 거짓이었다:
        //      · `stuck_ticks` 기본값이 0 이라 no_move 검사 자체가 꺼져 있다
        //      · `CbsTraffic::request_move` 의 지연 판정은 **다음 칸을 물어볼 때만** 돈다
        //    sim 실측(2026-08-02, `scripts/cbs_sim`): 25초 지연을 주입한 로봇의 마감 초과
        //    경고가 **0건**. 화면은 `지연 +14.9s` 라고 말하는데 아무도 재계획을 안 걸었다.
        //
        // 그래서 0 도 -1 도 아닌 **그 간선의 예산**을 쓴다. 값은 교통 플러그인이
        // 자기 시간 모델(회전·노드 정지·slow-edge 포함)로 계산해 준 것이다 —
        // 여기서 거리·속도로 다시 계산하지 않는 이유는 `plan_deadline_slack` 주석과 같다.
        // 플러그인이 안 채워 주면(-1) 예전대로 마감 없이 둔다.
        if (na.size() > 1) { na[1] = routes[i].commit_deadline_tick; }
      }
      // ⚠️ 순회는 **계획 구간 뒤에 canonical 랩을 도로 이어 붙인다.**
      //    CBS 목표를 다음 한 정점으로 줄였으므로, 그것만 남기면 랩이 잘려 나가고
      //    다음 틱에 "1바퀴 완주" 로 오인돼 랩이 계속 재생성된다.
      if (t.patrol) {
        t.plan_end_idx = static_cast<int>(np.size()) - 1;
        // ⚠️ **꼬리는 "계획이 실제로 도달한 정점" 다음부터** 잇는다. `t.idx + 2` 로 고정하면
        //    안 된다 — 순회 목표는 보통 `idx+1` 이지만 다른 순회 로봇과 겹치면 랩을 따라
        //    **더 뒤로 밀린다**(위 taken_goals 분기). 그때 그 사이 정점들이 계획 구간과
        //    꼬리에 **두 번** 들어가, 랩이 `… 12 14 10 | 14 10 9 …` 처럼 되고 다음 재계획이
        //    그 위에 또 쌓인다. 화면에서는 "순회 경로가 지 멋대로 바뀐다" 로 보인다.
        //    (순회 로봇이 둘 이상일 때만 터져 1대 시험에서는 안 드러났다. 유령 task 도
        //     같은 조건을 만든다 — 꺼진 로봇이 순회 task 를 들고 있으면 목표가 겹친다.)
        //
        //    인덱스를 기억해 두지 않고 **경로에서 되찾는다**. 탐색은 콜백 밖에서 도는데
        //    그동안 `t.idx` 가 전진할 수 있어, 요청 시점에 계산한 인덱스는 결과가 올 때
        //    이미 낡아 있을 수 있다. `np.back()`(=계획 목표)을 현재 idx 부터 찾으면
        //    그 어긋남에 영향받지 않는다. (계산은 patrol_tail_index — 시험이 붙어 있다)
        const size_t tail = patrol_tail_index(t.path, t.idx, np.back());
        // CBS 가 지름길로 canonical 정점을 건너뛰었으면 그 정점은 이번 랩에서 순회되지
        // 않는다. 조용히 넘기지 않고 로그로 남긴다 — arte2 지도에는 v13-v10 현(弦)이 실제로
        // 있어(랩은 13→12→14→10) 목표가 멀리 밀리면 12·14 가 통째로 빠질 수 있다.
        for (size_t k = t.idx; k + 1 < tail && k < t.path.size(); ++k) {
          if (std::find(np.begin(), np.end(), t.path[k]) == np.end()) {
            RCLCPP_WARN(get_logger(), "[%s] %s ⚠ 계획이 순회 정점 v%d 를 건너뛰었습니다(지름길)",
                        t.id.c_str(), t.robot.c_str(), t.path[k]);
          }
        }
        for (size_t k = tail; k < t.path.size(); ++k) {
          np.push_back(t.path[k]);
          na.push_back(-1);            // 이어 붙인 꼬리에는 마감이 없다
        }
      } else {
        t.plan_end_idx = -1;
      }
      t.path = np;
      t.plan_arrive = na;
      t.plan_epoch = epoch;
      t.excluded_logged = false;   // 계획이 실제로 갱신됐다 — 제외 로그를 다시 낼 수 있다
      t.plan_excluded = false;     // 이번엔 계획에 들어왔다 — 마감 감시를 되살린다
      t.plan_tick_sec = tick_sec;
      t.idx = 1;
      t.reroutes = 0;
      RCLCPP_INFO(get_logger(), "[%s] %s 시간표 재계획 → %zu nodes (도착 %d틱)",
                  t.id.c_str(), t.robot.c_str(), t.path.size(),
                  routes[i].arrive_tick.empty() ? -1 : routes[i].arrive_tick.back());
    }
    // 사유를 같이 낸다 — 관제가 '이 재계획이 왜 났나' 를 화면에서 가른다.
    publish_plan(routes, epoch, tick_sec, why);
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
    last_plan_empty_ = routes.empty();
    plan_pub_->publish(m);
  }

  // ── 계획이 없어졌다는 것도 **알려야 한다** ──────────────────────────────────
  //
  // `/fms/plan` 은 transient_local(래치)이라 마지막 값이 영원히 살아 있다. 그래서
  // 계획할 것이 사라졌을 때 아무것도 안 내면, 화면은 **이미 지난 시간표를 현재처럼**
  // 들고 카운트다운한다. 실측 2026-08-02: 로봇을 전부 내린 뒤 04:11 에 짠 계획을
  // 09:15 에 보고 "지연 +18198.6s" 를 찍었다. 숫자가 틀린 게 아니라 재료가 5시간
  // 묵은 것인데, 화면에는 그 말이 없어 "예약이 깨졌나" 로 읽힌다.
  //
  // 실패 경로(`apply_routes` 의 "시간표를 세우지 못했습니다")는 이미 빈 계획을 낸다.
  // 여기서 메우는 것은 **조용히 빠져나가던 두 자리**다 — 활성 task 가 없을 때와
  // 계획 대상 로봇이 하나도 없을 때.
  //
  // ⚠️ 이미 비운 뒤에는 다시 내지 않는다. 이 함수는 150 ms 타이머에서도 불린다.
  void clear_plan_once(const char * why)
  {
    if (last_plan_empty_) { return; }
    if (!traffic_ || !traffic_->plans_routes()) { return; }   // 반응형 교통은 계획 자체가 없다
    RCLCPP_INFO(get_logger(), "[traffic] 시간표 비움 — %s", why);
    publish_plan({}, now_sec(), traffic_->tick_seconds(), why);
  }

  // 계획이 밀려서 반응형으로 강등된 뒤, 다시 계획으로 되돌아온다.
  //
  // 장애물·지체로 늦는 것은 정상 운영에서 늘 일어난다. 강등만 있고 복귀가 없으면 CBS 는
  // 첫 지연 한 번으로 영영 꺼진 채 남는다 — 그러면 붙인 의미가 없다.
  //
  // ⚠️ **기본 간격은 0 이다 — 지체를 안 그 틱에 바로 짠다**(:179). 아래 cooldown/backoff 는
  //    그래서 기본 설정에서는 전부 통과한다. `replan_cooldown_ticks` 를 1 이상으로 주면
  //    다시 살아난다 — CBS 가 타이머 콜백을 먹을 때 쓰는 손잡이다.
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

    // 누가 걸었나에 따라 사유가 다르다. 플러그인이 강등한 것(`needs_replan`)이면 그
    // 사유를, 우리가 건 것이면 `request_replan` 이 남긴 사유를 쓴다. 둘을 섞으면
    // 끈적한 옛 강등 사유가 우리 요청에 붙는다(codex 3차 P1).
    const bool by_plugin = traffic_->needs_replan();
    std::string why = by_plugin ? traffic_->last_demote_reason() : requested_why_;
    if (why.empty()) { why = by_plugin ? "계획 강등" : "재계획 요청"; }
    requested_why_.clear();
    RCLCPP_INFO(get_logger(), "[traffic] 시간표 무효 → 재계획 (사유: %s, 다음 간격 %d틱)",
                why.c_str(), replan_cooldown_);
    replan_requested_ = false;
    // 로그에만 남기던 사유를 계획에도 싣는다 — 관제가 그걸로 사람/지연을 가린다.
    replan_all_routes(why);
  }

  static double now_sec()
  {
    return std::chrono::duration<double>(
             std::chrono::steady_clock::now().time_since_epoch()).count();
  }

  // ── 반복해서 마감을 못 지킨 간선을 기억한다 ───────────────────────────────
  //
  // 재계획은 **원래 같은 길을 다시 낸다.** 회피 대상(`blocked`)에 들어가는 건
  // `is_immobile`(ERROR) 로봇뿐이라, "저 길이 실제로 느리다"는 관측이 모델에 전혀
  // 되먹여지지 않았다. 그래서 지연 → 재계획 → **같은 경로** → 또 지연이 돌았다.
  //
  // ⚠️ **막지 않고 비싸게 만든다**(traffic_base.hpp `SlowEdge` 머리말). 순회 루프는
  //    고리 하나라 간선 한 줄만 빼도 반대편 길이 없어져 시간표가 통째로 실패한다.
  //    비용만 올리면 대안이 있을 때만 갈아타고, 없으면 그 길로 계속 간다.
  //
  // ⚠️ 벌점은 **잊힌다.** 사람이 잠깐 서 있었던 복도가 영원히 비싸지면, 치우고 난
  //    뒤에도 로봇이 계속 돌아간다. 마지막 실패 뒤 TTL 이 지나면 항목을 버린다.
  void note_deadline_miss(int from, int to)
  {
    if (from < 0 || to < 0 || from == to) { return; }
    auto & e = edge_miss_[{from, to}];
    e.first++;              // 연속 실패 횟수
    e.second = now_sec();   // 마지막 실패 시각
    if (e.first == kSlowEdgeMisses) {
      RCLCPP_WARN(get_logger(),
                  "[traffic] v%d→v%d 가 %d회 연속 마감을 못 지켰다 → 재계획에서 +%d틱 벌점(막지 않음)",
                  from, to, kSlowEdgeMisses, drift_penalty_ticks());
    }
  }

  // 제때 도착했으면 그 간선의 기록을 지운다. 한 번의 사고로 영구히 미움받지 않게.
  void note_deadline_kept(int from, int to) { edge_miss_.erase({from, to}); }

  // ⚠️ **drift_limit 에 매달지 않는다.** 예전엔 `max(1, drift_limit())` 이었는데,
  //    지연 관용을 0 으로 내리자 벌점이 1틱으로 같이 무너졌다 — 하필 벌점이 가장 필요한
  //    설정에서다. 관용이 0 이면 재계획이 잦아지고, 재계획은 **원래 같은 길을 다시 낸다.**
  //    그 되먹임을 끊는 것이 이 벌점인데 1틱은 대안 경로를 이기지 못한다.
  //    두 값은 뜻이 다르다: 관용은 "언제 틀렸다고 볼 것인가", 벌점은 "얼마나 돌아갈 값어치가
  //    있나". 같은 숫자를 쓸 이유가 없다.
  int drift_penalty_ticks() const { return kSlowEdgeExtraTicks; }

  std::vector<SlowEdge> slow_edges_now()
  {
    std::vector<SlowEdge> out;
    const double now = now_sec();
    for (auto it = edge_miss_.begin(); it != edge_miss_.end(); ) {
      if (now - it->second.second > kSlowEdgeTtlSec) { it = edge_miss_.erase(it); continue; }
      if (it->second.first >= kSlowEdgeMisses) {
        out.push_back({it->first.first, it->first.second, drift_penalty_ticks()});
      }
      ++it;
    }
    return out;
  }

  // 마감을 이만큼 넘겨야 "그 시간표는 틀렸다" 로 본다(초). 장애물 회피·감속으로 몇 초
  // 밀리는 것은 정상이라, 0 이면 재계획만 반복한다.
  //
  // ⚠️ **주인은 교통 플러그인이다 — 여기서 따로 파라미터를 두지 않는다.**
  //    예전에는 `plan_deadline_slack` ROS 파라미터로 같은 값을 한 벌 더 들고 있었고,
  //    주석이 "CbsTraffic 의 drift_limit 과 같은 값으로 두라" 고 사람에게 부탁했다.
  //    손으로 맞춰야 하는 값은 언젠가 어긋난다 — 어긋나면 교통 계층은 "계획대로 가는 중"
  //    으로 보고 통과를 열어 주는 구간에서 fleet_node 만 혼자 마감 초과로 재계획을 걸어,
  //    순회 경로가 5~10초마다 새로 짜인다(실측 2026-08-02). 유도하면 그 자리가 사라진다.
  //    ⚠️ 화면도 같은 값을 쓴다 — `/fms/plan` 의 `drift_limit` 이 이 값의 출처다.
  double plan_deadline_slack() const
  {
    return traffic_ ? traffic_->drift_limit() * traffic_->tick_seconds() : 0.0;
  }

  // 재계획을 요청한다. **사유를 같이 남긴다.**
  //
  // ⚠️ [2026-08-03] 예전에는 `replan_requested_ = true` 만 세우고, 사유는
  //    `traffic_->last_demote_reason()` 에서 꺼내 썼다. 그 값은 **끈적하다** —
  //    마지막 강등 사유가 계속 남는다. 우리(fleet_node)가 건 재계획에 **직전 강등의
  //    사유가 그대로 붙어** 화면에 거짓 원인이 뜬다(codex 3차 P1). 관제에서 사람/지연을
  //    가리려고 사유를 내보내는 것이니, 그게 틀리면 통로 자체가 무의미하다.
  //
  //    먼저 온 요청의 사유를 유지한다 — 한 틱에 여럿이 걸리면 처음 것이 원인에 가깝다.
  void request_replan(const char * why)
  {
    if (!replan_requested_) { requested_why_ = why ? why : ""; }
    replan_requested_ = true;
  }

  // 계획 도착 시각을 넘겼나. 넘겼으면 그 시간표는 이미 남의 통과를 잘못 열어 주고 있다.
  //
  // ⚠️ 로봇이 늦는 것 자체는 막을 수 없다(장애물 회피·감속·리로컬라이제이션). 막을 수 있는 건
  //    **늦은 걸 모르는 것**이다. 계획은 "이 정점을 이 시각에 비운다"를 전제로 남에게 통과를
  //    열어 줬으므로, 그 전제가 깨진 순간 다시 짜야 한다.
  // ⚠️ [2026-08-03] **커밋 칸 하나가 아니라 `t.idx` 이후 전 칸을 본다.**
  //
  //   예전에는 `t.idx` 한 칸만 검사했다. 그런데 관제의 **예약 표**는 `FleetPlan` 의
  //   `arrive_tick` 이 `>= 0` 인 **모든 칸**에 카운트다운을 찍는다
  //   (`WaypointEditor.tsx:908-921`). 그래서 로봇이 아직 안 닿은 **먼 칸**이 0을 지나
  //   `지연 +16.3s` 로 빨갛게 떠 있는데 아무도 재계획을 안 거는 상태가 생겼다
  //   (실기 2026-08-03: `순회경로-4 +6.3s` 와 `순회경로-5 +16.3s` 가 동시에 초과).
  //
  //   계획의 약속("이 정점을 이 시각에 비운다")은 **모든 칸에** 걸려 있다. 먼 칸이
  //   밀렸다는 것은 그 시간표가 이미 남에게 틀린 통과 허가를 주고 있다는 뜻이라,
  //   그 칸도 재계획 사유다. 화면이 세는 것과 실제 감시 대상을 같게 만든다 —
  //   **프론트에서 계산을 새로 만들지 않고** FMS 가 낸 마감만 쓴다.
  //
  //   ⚠️ 그래도 **벌점은 커밋 간선에만** 준다(아래). 미래 칸이 트리거였다고 아직
  //      지나지도 않은 간선을 "느린 길"로 학습시키면 다음 CBS 경로가 왜곡된다
  //      (codex 지적, `slow_edges_now()` 가 그 표를 읽는다).
  //   ⚠️ 지도 캔버스 라벨은 **다음 홉 하나**뿐이라(`WaypointEditor.tsx:714`) 예전에도
  //      맞았다. 어긋난 것은 예약 표 쪽이다.
  void check_plan_deadline(ActiveTask & t)
  {
    if (t.plan_arrive.empty()) { return; }
    // ⚠️ 이번 계획에서 **제외된** task 는 마감으로 재계획을 요청하지 않는다.
    //    `start == goal` 이라 짤 것이 없는데 요청하면 → 또 제외 → 또 요청으로
    //    150ms 루프가 된다(codex 3차 P1, `ActiveTask::plan_excluded` 머리말).
    //    다른 로봇의 마감은 그대로 본다 — "0이면 재계획" 보장은 유지된다.
    if (t.plan_excluded) { return; }
    const double slack = plan_deadline_slack();
    const double now = now_sec();

    // 판정은 순수 함수로 뺐다(`fleet_task.hpp`) — 여기서는 잡을 수 없어 시험이 없던
    // 자리다. `test_plan_deadline_scan.cpp` 가 되돌림까지 붙들고 있다.
    const size_t last = std::min(t.plan_arrive.size(), t.path.size());
    const size_t hit = first_overdue_cell(t.path, t.plan_arrive, t.idx,
                                          t.plan_epoch, t.plan_tick_sec, slack, now);
    if (hit >= last) { return; }
    const double due_hit = t.plan_epoch + t.plan_arrive[hit] * t.plan_tick_sec + slack;

    if (!replan_requested_) {
      std::string dbg;
      for (size_t i = 0; i < t.plan_arrive.size(); ++i) {
        dbg += (i ? "," : "") + std::string(i == t.idx ? "*" : (i == hit ? "!" : "")) +
               std::to_string(t.path[i]) + "@" + std::to_string(t.plan_arrive[i]);
      }
      RCLCPP_WARN(get_logger(),
                  "[%s] %s ⏱ v%d 계획 도착(%d틱) 초과 %.1fs → 재계획 요청 [idx=%zu hit=%zu %s]",
                  t.id.c_str(), t.robot.c_str(), t.path[hit], t.plan_arrive[hit],
                  now - due_hit + slack, t.idx, hit, dbg.c_str());
    }
    // 어느 **간선**이 마감을 못 지켰나 — 다음 재계획이 그 길을 비싸게 보게 한다.
    // 매 틱 부르므로 **한 번 지나는 동안 한 번만** 센다. 중복 방지를 인덱스가 아니라
    // 간선으로 하는 이유는 `ActiveTask::missed_from` 주석 참고(재계획이 인덱스를 되감는다).
    //
    // ⚠️ **`hit == t.idx` 일 때만** 센다 — 지금 실제로 타고 있는 간선이 늦은 경우다.
    //    미래 칸이 트리거였는데 그 칸의 직전 간선에 벌점을 주면, 로봇이 지나지도 않은
    //    길을 "느리다"고 학습해 다음 경로가 엉뚱하게 돌아간다(codex 지적 P1).
    if (hit == t.idx && t.idx >= 1) {
      const int mf = t.path[t.idx - 1];
      const int mt = t.path[t.idx];
      if (mf != t.missed_from || mt != t.missed_to) {
        t.missed_from = mf;
        t.missed_to = mt;
        note_deadline_miss(mf, mt);
      }
    }
    request_replan("도착 마감 초과");
  }

  // 이 로봇의 위치 정보가 낡았나. `/robot_state` 를 마지막으로 받은 지
  // `kRobotStaleSec` 이 지났거나, 한 번도 못 받았으면 참이다.
  //
  // ## ⚠️ [2026-08-02] 왜 이걸 **새 일감 배정**에서 봐야 하나
  //
  // `robots_` 는 `/robot_state` 로만 채워지고 **어디서도 제거되지 않는다**(on_timer 주석).
  // 그래서 로봇을 꺼도 목록에 남고, 아래 순회 루프가 `mode == "PATROL"` 만 보고
  // **꺼진 로봇에게 순회를 다시 배정**했다. 실측 2026-08-02: pinky-3 의 Pi 를 완전히
  // 내린 뒤에도 "[P-pinky-3] 순회 시작" 이 찍혔다. 그 유령이 노드를 예약하면
  // **살아 있는 로봇의 길을 막는다** — 경고만 찍고 넘어갈 문제가 아니다.
  //
  // ⚠️ **기존 예약을 여기서 풀지는 않는다.** 통신이 끊긴 것과 로봇이 그 자리에서
  //    사라진 것은 다르다. 마지막 예약 노드에 실제로 서 있을 수 있고, 그걸 말없이
  //    풀면 다른 로봇을 그 자리로 보내게 된다. 막는 것은 **새로 주는 일**뿐이다.
  //    (해제는 사람이 확인한 뒤 명시적으로 해야 한다 — 후속 과제)
  // 소식이 끊긴 지 `sec` 을 넘었나. `state_stale` 은 `kRobotStaleSec` 고정이고
  // 이건 호출부가 기준을 정한다 — 새 배정(10초)과 예약 해제(60초)는 기준이 달라야 한다.
  bool state_stale_for(const std::string & name, double sec) const
  {
    auto it = last_state_at_.find(name);
    if (it == last_state_at_.end()) { return true; }
    return (now() - it->second).seconds() > sec;
  }

  bool state_stale(const std::string & name) const
  {
    auto it = last_state_at_.find(name);
    if (it == last_state_at_.end()) { return true; }   // 한 번도 못 받았다
    return (now() - it->second).seconds() > kRobotStaleSec;
  }

  void on_timer()
  {
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
    // 워커가 계산해 둔 시간표를 먼저 집어 간다 — 이번 틱의 이동 판단이 새 계획을 쓰도록.
    apply_planner_result();
    service_replan_requests();

    // ── 정점 차단 TTL 만료 sweep (2026-08-03) ────────────────────────────
    // active_blocks() 는 replan_all_routes() 안에서만 불렸다 — 그래서 차단이 유효시간으로
    // 저절로 풀려도 **아무도 재계획을 요청하지 않아** 다른 사건(새 차단 보고 등)이 재계획을
    // 돌릴 때까지 그 길은 계속 막힌 것으로 취급됐다. 이 타이머(150ms, 이미 돌고 있다)에서
    // 만료를 걷어내고, **실제로 뭔가 풀렸을 때만** check_plan_deadline 과 같은 방식으로
    // replan_requested_ 를 세운다 — service_replan_requests() 가 다음 틱에 쿨다운을 지켜
    // 가며 대신 재계획한다(매 틱 전체 재계획을 도는 낭비를 피한다). 만료 로그는
    // active_blocks() 안에 이미 있으므로 여기서 또 찍지 않는다.
    if (!blocked_until_.empty()) {
      const size_t before = blocked_until_.size();
      active_blocks();
      if (blocked_until_.size() != before) {
      request_replan("정점 차단 해제");
      // ── 차단이 풀렸다 → **되돌리기를 못 했던 로봇을 다시 시도한다** ────────
      //
      //   차단 순간 `request_move(A, A)` claim 이 실패하면(A 를 남이 쥐고 있었다)
      //   그 로봇은 막힌 B 를 쥔 채 남는다. 그건 의도된 안전 상태지만, 차단이 풀린
      //   뒤에도 그대로면 **못 가는 자리를 계속 쥔 채 아무도 안 풀어 준다** —
      //   도착·순회중단·stuck·ghost 정리는 전부 **새 path 기준**이라 옛 B 를 못 본다
      //   (codex 3차 P1: "진행 불능 가능").
      //
      //   여기서는 예약을 풀지 않는다 — 그건 다시 "아무것도 안 쥔 채 레인에" 를
      //   만든다. 대신 `moving` 을 그대로 두고 재계획만 걸어, B 가 다시 갈 수 있는
      //   정점이 됐으니 평소 경로로 진행하게 한다. 실제로 B 에 도착하면 그때
      //   기존 도착 처리가 예약을 정상적으로 넘긴다.
      for (auto & t : tasks_) {
        if (!t.moving || t.idx < 1 || t.idx >= t.path.size()) { continue; }
        if (is_node_blocked(t.path[t.idx])) { continue; }   // 아직 막혀 있다
        t.wait_logged = false;   // 다시 시도한다 — 다음 WAIT 는 새로 로그를 남긴다
      }
    }
    }

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
      // 소식이 끊긴 로봇에는 새 순회를 주지 않는다 — 근거는 `state_stale` 머리말.
      if (state_stale(r.name)) { continue; }
      bool has = false;
      for (const auto & t : tasks_) { if (t.robot == r.name) { has = true; break; } }
      if (has) { continue; }
      if (is_held(r.name)) { continue; }   // 주문 다리 사이 — 순회로 떠나면 안 된다
      const std::string m = mode_of(r.name);
      if (m == "PATROL" && patrol_route_.size() >= 2) { start_patrol(r); }
      else if (m == "SECURITY_PATROL" && security_patrol_route_.size() >= 2) { start_security_patrol(r); }
    }

    // ── 그냥 서 있는 로봇도 자기 자리를 쥔다 ────────────────────────────────
    //
    // [2026-08-03] 예약 체계 전체가 "모든 로봇이 자기가 선 정점을 쥐고 있다" 를 전제해
    // 왔는데, **그걸 보장하는 곳이 없었다**(codex 판정 P0). claim 은 배차·순회·정지
    // 때만 했고(`:699` `:2026` `:2056` `:2142`) 그 반환값마저 버린다. 그래서 대기 중인
    // 로봇이 서 있는 정점은 장부상 비어 있었고, **다른 로봇이 그리로 배차되면 그대로
    // 밀고 들어간다.** 계획 쪽도 `is_immobile` 인 로봇만 장애물로 넣는다(`:735-739`).
    //
    // 매 틱 자기 자리를 다시 claim 하고, 옛 자리는 놓는다(로봇이 손으로 밀려 옮겨질 수도
    // 있다). 실패하면(남이 쥔 자리) 아무것도 안 한다 — 이미 겹쳐 있는 상태이므로
    // 여기서 뺏어 봐야 장부만 흔들린다.
    //
    // ⚠️ task 가 있는 로봇은 건드리지 않는다. 그쪽 예약은 task 흐름(출발·도착·정리)이
    //    관리하고, 여기서 겹쳐 잡으면 도착 해제와 싸운다.
    // ⚠️ 소식이 끊긴 로봇은 잡지 않는다 — 어디 있는지 모르는 로봇으로 길을 막게 된다.
    for (auto & kv : robots_) {
      const std::string & name = kv.first;
      if (state_stale(name)) { continue; }
      bool has = false;
      for (const auto & t : tasks_) { if (t.robot == name) { has = true; break; } }
      if (has) { continue; }
      const int v = graph_.nearest(kv.second.x, kv.second.y);
      if (v < 0) { continue; }
      if (traffic_->request_move(name, v, v, kStopPrio) != MoveDecision::GRANT) { continue; }
      for (const auto & no : traffic_->occupancy()) {   // 옛 자리 정리(한 대는 한 자리)
        if (no.second == name && no.first != v) { traffic_->release(name, no.first); }
      }
    }

    // task 가 이번 틱에 지워졌는지 센다 — 아래 `/fms/plan` 갱신 판정용.
    const std::size_t tasks_before = tasks_.size();
    for (auto it = tasks_.begin(); it != tasks_.end();) {
      ActiveTask & t = *it;
      RobotInfo & r = robots_[t.robot];

      // ── 소식이 끊긴 로봇의 task 는 **거두고 예약도 푼다** ────────────────
      //
      // [2026-08-02] `state_stale` 은 **새 배정만** 막고 기존 task 는 놔뒀다(그 주석의
      // "해제는 사람이 확인 후 — 후속 과제"). 그런데 실측에서 그 유령에 재계획이
      // 계속 돌고 노드 예약이 유지됐다 — 살아 있는 로봇의 길이 그만큼 막힌다.
      //
      // ⚠️ `kGhostTaskSec`(60초)는 `kRobotStaleSec`(10초)보다 훨씬 길다. 근거는 그 상수
      //    머리말 — 예약 해제는 되돌릴 수 없어서 브릿지가 잠깐 끊긴 정도로 풀면 안 된다.
      //
      // ⚠️ 배차 task 도 거둔다. 로봇이 사라진 채로 주문을 붙들고 있으면 그 주문은
      //    영영 안 끝난다. `FAILED` 로 알려야 orchestrator 가 다시 배차할 수 있다.
      if (state_stale_for(t.robot, kGhostTaskSec)) {
        RCLCPP_WARN(get_logger(),
                    "[%s] %s ⚠ 소식 끊긴 지 %.0fs 초과 → 예약 해제·task 정리 "
                    "(로봇이 돌아오면 새로 배정됩니다)",
                    t.id.c_str(), t.robot.c_str(), kGhostTaskSec);
        if (t.idx < t.path.size()) { traffic_->release_node(t.robot, t.path[t.idx]); }
        if (t.idx >= 1) { traffic_->release_node(t.robot, t.path[t.idx - 1]); }
        r.busy = false; r.task_id.clear();
        publish_task_state(t.id, t.patrol ? "CANCELLED" : "FAILED", t.robot);
        ++state_gen_;
        it = tasks_.erase(it);
        continue;
      }

      // ── 순회 task 는 로봇이 순회를 그만두면 **여기서 거둔다** ──────────────
      //
      // [2026-08-02] 순회는 `t.patrol` 이라 끝에 닿아도 "1바퀴 → 계속" 으로 순환한다.
      // 즉 **COMPLETED 로 지워지는 길이 없다.** 그래서 로봇이 IDLE·INTERACTING 으로
      // 떨어져도 task 가 남고, `publish_routes()` 는 조건 없이 전부 발행하므로
      // **대기 중인 로봇에 경로가 계속 뜬다**(사용자 실측: 로봇 「대기」인데 지도에
      // 순회 경로가 그대로였다).
      //
      // ⚠️ 화면에서 거르면 안 된다 — 그러면 "관제가 실제로 어떤 상태인가" 가 안 보인다.
      //    실제로 순회를 그만둔 것이므로 **관제가 자기 장부를 정리**하는 것이 맞다.
      //
      // ⚠️ 예약 노드를 반드시 같이 푼다. 안 풀면 그 정점이 영원히 잠겨 다른 로봇이
      //    못 지난다(위 stuck 분기가 같은 이유로 release_node 를 부른다).
      //
      // ⚠️ 배차 task(`!t.patrol`)는 건드리지 않는다. 주문은 로봇이 잠깐 응대
      //    (INTERACTING)로 빠져도 살아 있어야 하고, 끝내는 주체는 orchestrator 다.
      if (t.patrol) {
        const std::string m = mode_of(t.robot);
        const bool still_patrolling =
          (m == "PATROL") || (m == "SECURITY_PATROL") || m.empty();
        if (!still_patrolling) {
          RCLCPP_INFO(get_logger(), "[%s] %s 순회 중단(상태 %s) → 예약 해제·task 정리",
                      t.id.c_str(), t.robot.c_str(), m.c_str());
          if (t.idx < t.path.size()) { traffic_->release_node(t.robot, t.path[t.idx]); }
          if (t.idx >= 1) { traffic_->release_node(t.robot, t.path[t.idx - 1]); }
          r.busy = false; r.task_id.clear();
          publish_task_state(t.id, "CANCELLED", t.robot);
          ++state_gen_;
          it = tasks_.erase(it);
          continue;
        }
      }

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
        // 도착: 목표 노드는 그대로 쥔 채, **떠나온 정점을 여기서 놓는다.**
        //
        // 이 한 줄이 간선(레인) 예약이다 — 출발부터 도착까지 두 끝점을 다 쥐고 있었으므로
        // 그 사이 아무도 그 레인에 들어오지 못한다. 예전에는 GRANT 순간에 놓아서
        // 레인이 무방비였다(`reservation_deadlock.hpp` 머리말 참고).
        //
        // ⚠️ 선행통과(prefetch)에서도 안전하다. 여기 오는 시점이면 로봇은 목표 정점
        //    반경 안이라 떠나온 정점에서 충분히 멀다 — 그때 놓는 것이 정확하다.
        //    (예전 코드는 로봇이 아직 출발 정점 **위에 있을 때** 놓았다.)
        // ⚠️ 같은 정점이면(경로에 중복 정점) 놓지 않는다 — 서 있는 자리를 잃는다.
        if (t.idx >= 1 && t.path[t.idx - 1] != t.path[t.idx]) {
          traffic_->release_node(t.robot, t.path[t.idx - 1]);
        }
        RCLCPP_INFO(get_logger(), "[%s] %s %s v%d", t.id.c_str(), t.robot.c_str(),
                    final_node ? "도착" : (reach > arrive_radius_ ? "선행통과" : "통과"),
                    t.path[t.idx]);
        // 도달한 정점의 인덱스. **올리기 전** 값이라야 "방금 지나온 간선" 을 가리킨다 —
        // 근거는 `traversed_edge_to_credit` 의 ⚠️ 주석.
        const std::size_t arrived_idx = t.idx;
        t.idx++;
        t.moving = false;
        // ⚠️ **도달도 "상태가 바뀐 것" 이다.** 탐색은 콜백 밖에서 도는데(hand_to_planner)
        //    그동안 로봇이 한 칸 더 가면, 돌아온 시간표는 **이미 지나온 정점에서 시작한다.**
        //    `apply_planner_result` 의 세대 검사가 그런 결과를 버리는 장치인데, 예전에는
        //    task 생성·삭제에서만 세대를 올려 도달은 통과해 버렸다. 그대로 적용하면
        //    `t.idx = 1` 로 되감기며 로봇에게 **왔던 길을 되돌아가라**고 낸다.
        //    (codex 2026-08-02 적대적 검토 2(c). 순회 랩 꼬리 버그와 같은 자리에서 겹친다.)
        ++state_gen_;
        // 마감 안에 왔으면 **방금 지나온** 간선의 벌점 기록을 지운다 — 한 번의 사고로
        // 영구히 미움받지 않게. 그 간선에서 이미 늦었으면 지우지 않는다.
        // 판정은 `fleet_task.hpp` 의 순수 함수가 한다(범위 검사 포함) — 거기 ⚠️ 주석에
        // 예전 코드가 어떻게 세 가지를 한꺼번에 틀렸는지 적어 뒀다.
        int kept_from = 0, kept_to = 0;
        if (traversed_edge_to_credit(t.path, arrived_idx, t.missed_from, t.missed_to,
                                     kept_from, kept_to)) {
          note_deadline_kept(kept_from, kept_to);
        }
        // 도달했으니 이번 통과는 끝났다 — 다음 통과에서 다시 셀 수 있게 푼다.
        t.missed_from = -1;
        t.missed_to = -1;
        t.reroutes = 0;   // 노드 도달 = 진전 → 우회 카운터 리셋
        replan_streak_ = 0;   // 진전 → 재계획 backoff 도 원복
        t.wait_ticks = 0;   // 노드 도달 = 진전 → 타임드 우회 카운터 리셋
        t.plan_excluded = false;   // 진전이 있었다 — 마감 감시를 다시 켠다
        // 다음 경로도 방금 도착한 정점을 기준으로 시간표를 새로 잡는다.
        //
        // `publish_routes()`는 매 틱 현재 `t.idx`부터 내보내지만, FleetPlan은 재계획할
        // 때만 바뀐다. 예전에는 순회만 위 plan_end_idx에서 재계획했고 WORKING은 최초
        // 계획의 도착 시각을 끝까지 들고 갔다. 그래서 지도 선은 다음 노드로 넘어갔는데
        // 예약 표·지연 카운트다운은 지나온 노드에 남아, 작업 중일 때만 관제가 늦게
        // 반응했다. 성공적인 노드 도달은 상태 세대를 이미 올린 뒤이므로, 여기서 워커에
        // 새 스냅샷을 넘기면 실행 루프를 막지 않고 현재 위치 기준 시간표가 나온다.
        //
        // 마지막 정점은 아래에서 task를 완료·랩 재생성하므로 계획을 만들지 않는다.
        if (refresh_plan_after_arrival(t.patrol, t.idx, t.path.size())) {
          replan_all_routes();
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
            ++state_gen_;
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
        // ⚠️ **차단된 정점에는 통행을 안 준다.** 교통 플러그인은 `blocked_until_` 을
        //    모른다(그 표는 계획 스냅샷에만 실린다). 여기서 막지 않으면 사람이 서 있는
        //    정점으로 다른 로봇을 들여보낸다 — 재계획이 적용되기 전, CBS 가 해를 못
        //    찾았을 때, 반응형 폴백 중 전부 해당한다(codex 검토 P0).
        //    기다리게만 한다(예약은 안 건드린다) — 유효시간이 지나면 저절로 풀린다.
        //
        //    ⚠️ **WAIT 로 다룬다 — 건너뛰면 안 된다.** 처음엔 여기서 `continue` 로 빠져
        //       나갔는데, 그러면 아래 WAIT 경로의 **타임드 우회**
        //       (`kRerouteWaitTicks` 뒤 `dijkstra(cur, goal, next)` 로 그 정점을 피해
        //       돌아가는 장치)를 통째로 건너뛴다. 차단된 정점 앞에서 로봇이 영원히
        //       기다리기만 하고 우회를 안 한다. `request_move` 를 안 부르면서 판정만
        //       WAIT 로 두면 그 장치가 그대로 산다.
        const bool blocked_ahead =
          (cur != next) && is_node_blocked(next, t.robot);
        MoveDecision dec =
          blocked_ahead ? MoveDecision::WAIT
                        : traffic_->request_move(t.robot, cur, next, compute_priority(t.robot, t));
        if (dec == MoveDecision::GRANT) {
          // ⚠️ **출발 노드를 여기서 놓지 않는다.** 예전에는 GRANT 즉시 놓았고, 그래서
          //    주행 중 로봇은 목표 정점 하나만 쥐었다 — 레인은 아무도 안 지켰다.
          //    지금은 도착 분기(아래 `t.moving && d < reach`)에서 놓는다. 그동안 두
          //    끝점을 다 쥐므로 그 레인에 남이 못 들어온다(= 간선 예약).
          //    근거와 대가는 `reservation_deadlock.hpp` 머리말.
          // full_path 면 남은 정점을 전부 실어 보낸다 — 로봇이 간선을 따라 멈춤 없이 간다.
          // ⚠️ 예약(traffic)은 여전히 **다음 한 칸**만 잡는다. full_path 로 보낸 그 뒤
          //    정점·간선은 예약 밖이라 다중 로봇에서 위험하다(codex P0).
          //    그때까지는 `-p full_path:=false`(기본값) 로 한 노드씩 간다.
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
            // ⚠️ 원인이 사람이면 최상위로 올리지 않는다.
            //
            // escalation 의 뜻은 "주변이 비켜 준다" 인데, 막은 것이 사람이면 온 플릿이
            // 양보해도 안 비킨다. 그러면 헛된 우선순위 상승이 플릿 전체를 흔들 뿐이다.
            // 사람 차단은 유효시간이 지나면 저절로 풀리므로 기다리는 편이 낫다.
            //
            // 이 task 의 남은 경로에 "사람" 사유로 막힌 정점이 있나. 그때만 escalation 을
            // 끈다(플릿 전체에 사람 차단이 하나라도 있으면 다 끄는 것은 과하다).
            bool blocked_by_person = false;
            for (size_t i = t.idx; i < t.path.size(); ++i) {
              for (const auto & kv : blocked_reason_) {
                if (kv.first.first == t.path[i] && kv.second == "person") {
                  blocked_by_person = true; break;
                }
              }
              if (blocked_by_person) { break; }
            }
            if (!t.stuck && !blocked_by_person) {   // 우회 불가 or 우회 반복초과(livelock) → 우선순위 최상위 escalate(주변이 비켜줌) 후 대기
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
    // 활성 task 가 하나도 없으면 지킬 시간표도 없다. 래치 토픽이라 여기서 비워 주지
    // 않으면 화면이 마지막 계획을 몇 시간이고 현재처럼 띄운다 — `clear_plan_once` 머리말.
    if (tasks_.empty()) {
      clear_plan_once("활성 작업 없음");
    } else if (tasks_.size() != tasks_before) {
      // ⚠️ [2026-08-03] **한 대만 빠져도 `/fms/plan` 을 다시 낸다.**
      //
      //   예전에는 전부 비었을 때만 비웠다. 그래서 순회 중단으로 한 대의 task 만
      //   지워지고 다른 로봇이 남아 있으면, **취소된 로봇의 옛 시간표가 래치 토픽에
      //   그대로 살아** 관제가 그 카운트다운을 계속 흘렸다(0 → -10초). 실행 쪽은 task 가
      //   없어 감시도 안 하는데 화면만 세고 있는 상태다(codex 지적 P0).
      //
      //   재계획을 요청해 두면 다음 틱의 `service_replan_requests` 가 남은 로봇만으로
      //   다시 짜서 발행한다 — 사라진 로봇 항목이 그때 없어진다.
      request_replan("작업 정리");
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
    // 복귀는 ActiveTask가 없어서 기존에는 UI에 경로가 비어 있었다. 실제 복귀
    // 실행 주체는 libi_modes이므로 FMS는 현재 위치와 복귀 waypoint만 시각화한다.
    for (const auto & [name, robot] : robots_) {
      if (mode_of(name) != "RETURNING" || state_stale(name) || has_task_for(name)) { continue; }
      if (return_goal_node_ < 0 || static_cast<size_t>(return_goal_node_) >= graph_.size()) { continue; }
      libi_fleet_msgs::msg::RobotRoute r;
      r.robot = name;
      r.xs.push_back(robot.x);
      r.ys.push_back(robot.y);
      const int start = graph_.nearest(robot.x, robot.y);
      const auto path = graph_.dijkstra(start, return_goal_node_);
      for (int v : path) {
        const Vertex & waypoint = graph_.vertex(v);
        r.xs.push_back(waypoint.x);
        r.ys.push_back(waypoint.y);
      }
      m.routes.push_back(std::move(r));
    }
    route_pub_->publish(m);
  }

  bool has_task_for(const std::string & robot) const
  {
    for (const auto & t : tasks_) {
      if (t.robot == robot) { return true; }
    }
    return false;
  }

  // 각 로봇의 최종 목적지 발행(배차 task 만; 순회는 목적지가 없어 빠진다).
  void publish_goals()
  {
    libi_fleet_msgs::msg::FleetGoals m;
    for (const auto & t : tasks_) {
      if (t.path.empty()) { continue; }
      m.robots.push_back(t.robot);
      // ⚠️ 순회를 **건너뛰지 않는다.** 랩에는 끝이 없지만 "지금 어디로 가는 중" 은 늘 있고,
      //    순회에 붙는 동안 그 값이 곧 **진입점**이다. 예전에는 통째로 건너뛰어서 관제 표의
      //    「목표」 칼럼이 순회 로봇에 늘 `—` 였다 — 진입점을 어디로 잡았는지 화면 어디에도
      //    안 나오고 fleet_node 로그에만 남았다. 배차 task 는 예전대로 최종 목적지다.
      m.goals.push_back(t.patrol ? t.path[std::min(t.idx, t.path.size() - 1)]
                                 : t.path.back());
    }
    for (const auto & [name, robot] : robots_) {
      (void)robot;
      if (mode_of(name) != "RETURNING" || state_stale(name) || has_task_for(name)) { continue; }
      if (return_goal_node_ < 0 || static_cast<size_t>(return_goal_node_) >= graph_.size()) { continue; }
      m.robots.push_back(name);
      m.goals.push_back(return_goal_node_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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

  // 이 정점이 지금 막혀 있나. `active_blocks()` 와 같은 표를 보지만 **정리는 안 한다** —
  // 매 틱 이동 판단에서 불리므로 부작용이 없어야 한다(정리는 on_timer 의 sweep 몫).
  //
  // ⚠️ [2026-08-03] 이게 없어서 **반응형 진입 게이트가 차단을 모르고 있었다.**
  //    차단 정점은 CBS 스냅샷(`snap.blocked`)에만 들어가 진입 간선이 끊겼는데,
  //    `request_move()` 는 그 표를 안 본다. 그래서 재계획이 적용되기 전이나 CBS 가
  //    해를 못 찾아 반응형으로 떨어진 동안, **다른 로봇이 사람이 서 있는 정점으로
  //    GRANT 를 받을 수 있었다**(codex 검토 P0).
  bool is_node_blocked(int node, const std::string & robot = "") const
  {
    const double now = now_sec();
    for (const auto & kv : blocked_until_) {
      if (kv.first.first != node || now >= kv.second) { continue; }
      const auto reason = blocked_reason_.find(kv.first);
      const bool own_dock_lock =
        reason != blocked_reason_.end() && reason->second == "shelf_dock" &&
        kv.first.second.rfind("dock:", 0) == 0 &&
        norm_robot_name(kv.first.second.substr(5)) == norm_robot_name(robot);
      if (!own_dock_lock) { return true; }
    }
    return false;
  }

  // 도킹 소유자가 향하는 최종 정점은 CBS의 전역 차단 목록에서만 제외한다.
  // 다른 로봇은 is_node_blocked(next, robot)에서 여전히 멈춘다.
  bool is_owned_shelf_dock_goal(int node) const
  {
    const double now = now_sec();
    for (const auto & kv : blocked_until_) {
      if (kv.first.first != node || now >= kv.second) { continue; }
      const auto reason = blocked_reason_.find(kv.first);
      if (reason == blocked_reason_.end() || reason->second != "shelf_dock" ||
          kv.first.second.rfind("dock:", 0) != 0) {
        continue;
      }
      const auto owner = norm_robot_name(kv.first.second.substr(5));
      for (const auto & task : tasks_) {
        if (norm_robot_name(task.robot) == owner && !task.path.empty() &&
            task.path.back() == node) {
          return true;
        }
      }
    }
    return false;
  }

  // 유효시간이 지난 차단을 지우고, 살아 있는 정점 집합(중복 없이)을 돌려준다.
  std::vector<int> active_blocks()
  {
    const double now = now_sec();
    std::set<int> nodes;
    for (auto it = blocked_until_.begin(); it != blocked_until_.end(); ) {
      if (now >= it->second) {
        RCLCPP_INFO(get_logger(), "[block] 정점 %d owner=%s 유효시간 만료 — 자동 해제",
                    it->first.first, it->first.second.c_str());
        blocked_reason_.erase(it->first);
        it = blocked_until_.erase(it);
      } else {
        nodes.insert(it->first.first);
        ++it;
      }
    }
    return std::vector<int>(nodes.begin(), nodes.end());
  }

  // ── 플래너 워커 ────────────────────────────────────────────────────────
  //
  // 스냅샷 하나만 들고 있는다(큐가 아니다). 새 요청이 오면 **덮어쓴다** — 낡은 스냅샷으로
  // 계산해 봐야 그 결과는 어차피 버려진다. 계획은 "지금 상태"에 대한 답이어야 한다.
  bool hand_to_planner(PlanSnapshot && snap, const std::string & why = "")
  {
    {
      std::lock_guard<std::mutex> lk(planner_mu_);
      // graph_ 포인터는 노드 수명 내내 유효하다(멤버). 스냅샷은 값 복사라 안전하다.
      pending_snap_ = std::move(snap);
      pending_gen_ = state_gen_;
      pending_reason_ = why;
      pending_ready_ = true;
    }
    planner_cv_.notify_one();
    return true;
  }

  void planner_loop()
  {
    for (;;) {
      PlanSnapshot snap;
      uint64_t gen = 0;
      std::string why;
      {
        std::unique_lock<std::mutex> lk(planner_mu_);
        planner_cv_.wait(lk, [this] { return pending_ready_ || planner_stop_; });
        if (planner_stop_) { return; }
        snap = std::move(pending_snap_);
        gen = pending_gen_;
        why = pending_reason_;
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
        result_gen_ = gen;
        result_reason_ = why;
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
    ++state_gen_;
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
  //
  // 알맹이는 `patrol_cycle.cpp` 의 `patrol_path_from` 이다 — 그래프와 정점 인덱스만
  // 있으면 되는 순수 계산이라, 노드 밖으로 빼서 시험할 수 있게 했다. 시험이 지키는 것은
  // **연속한 두 정점이 언제나 navgraph 간선**이라는 것이다. 그게 깨지면 교통관제가
  // 예약하지 않은 정점을 로봇이 지나간다(그 함수 머리말 참고).
  std::vector<int> make_patrol_path(const RobotInfo & r, int avoid_first,
                                    const std::vector<int> & route) const
  {
    return patrol_path_from(graph_, graph_.nearest(r.x, r.y), route, avoid_first);
  }

  // 로봇을 주간 순회 루프에 태워 무한 순회 시작.
  void start_patrol(RobotInfo & r)
  {
    std::vector<int> path;
    // 충전 완료 직후에는 충전소 정점에 그대로 둔 채 일반 순회 루프를
    // 시작하면 안 된다. costmap 경계 안에서 첫 순회 goal을 다시 충전소로
    // 받거나, 출구 방향이 뒤집혀 멈추는 현장 문제가 있었다.
    // CHARGING 이탈 시 한 번만 래치하고, 충전소통로(v17)까지 빠져나온 뒤
    // 다음 tick부터 기존 patrol_route를 사용한다.
    auto exit_it = charging_exit_pending_.find(r.name);
    const bool charging_exit = exit_it != charging_exit_pending_.end() && exit_it->second;
    if (charging_exit) {
      const int start = graph_.nearest(r.x, r.y);
      const int corridor = return_goal_node_;  // arte2: 충전소통로(v17)
      if (start >= 0 && corridor >= 0 && corridor < graph_.size() && start != corridor) {
        path = graph_.dijkstra(start, corridor);
      }
      if (path.size() >= 2) {
        exit_it->second = false;
        RCLCPP_INFO(get_logger(), "[%s] 충전 완료 출구: 충전소통로(v%d) 직행",
                    r.name.c_str(), corridor);
      } else {
        exit_it->second = false;
        RCLCPP_WARN(get_logger(), "[%s] 충전소통로(v%d) 경로 생성 실패 → 일반 순회로 전환",
                    r.name.c_str(), corridor);
      }
    }
    if (path.size() < 2) {
      path = make_patrol_path(r, -1, patrol_route_);
    }
    if (path.size() < 2) { return; }
    r.busy = true;
    std::string tid = "P-" + r.name;
    r.task_id = tid;
    ActiveTask t; t.id = tid; t.robot = r.name; t.path = path;
    t.idx = 1; t.moving = false; t.patrol = true;   // 순회는 최저 tier
    t.start_seq = ++task_seq_;
    traffic_->request_move(r.name, path[0], path[0], compute_priority(r.name, t));   // 진입점 점유
    tasks_.push_back(t);
    // ⚠️ **세대는 `replan_all_routes` 보다 먼저 올린다.** 순서가 뒤바뀌면 스냅샷을 세대 G 로
    //    잡아 놓고 곧바로 G+1 로 올려 버려서, 워커가 돌려준 시간표를 `apply_planner_result`
    //    가 "계산 중에 상태가 바뀌었다" 며 **매번** 버린다. 실측 2026-08-02(sim): 순회를
    //    시작해도 `/fms/plan` 이 한 번도 안 나가고, 30초 뒤 지연 강등으로 재계획이 돌고
    //    나서야 seq 1 이 처음 나왔다. 그동안 관제 화면에는 **합류 구간 예약이 통째로
    //    안 보인다** — 실제로는 예약이 걸려 있는데 화면만 비어 있는 상태다.
    //    `on_submit` 은 원래 이 순서(먼저 올리고 나중에 계획)라 배차에서는 안 드러났다.
    ++state_gen_;
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
    ++state_gen_;          // 세대 먼저 — 순서가 뒤바뀌면 시간표가 버려진다(start_patrol 주석)
    replan_all_routes();   // 주간 순회와 같은 이유 — start_patrol 주석 참고
    publish_task_state(tid, "SECURITY_PATROL", r.name);
    RCLCPP_INFO(get_logger(), "[%s] %s 보안순회 시작 (시작 v%d, %zu nodes)",
                tid.c_str(), r.name.c_str(), path[0], path.size());
  }

  void on_set_plugins(const std::shared_ptr<SetPlugins::Request> req,
                      std::shared_ptr<SetPlugins::Response> res)
  {
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
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
    std::lock_guard<std::recursive_mutex> lk(state_mu_);
    const std::string robot = json_str_field(msg->data, "robot_id");
    const std::string state = json_str_field(msg->data, "current_state");
    if (robot.empty() || state.empty() || !kLibiModesStates.count(state)) { return; }
    const std::string before = mode_of(robot);
    if (before == state) { return; }   // 변화 없으면 무시(매 발행마다 처리 방지)
    if (before == "CHARGING" && state != "CHARGING") {
      charging_exit_pending_[robot] = true;
    }
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
  // 간선별 (연속 마감 실패 횟수, 마지막 실패 시각). note_deadline_miss/kept 가 관리한다.
  std::map<std::pair<int, int>, std::pair<int, double>> edge_miss_;
  int replan_cooldown_ticks_{0};      // 재계획 최소 간격(틱). 0 = 즉시(:179 머리말)
  int replan_cooldown_{0};
  bool replan_requested_{false};      // 도착 마감 초과로 fleet_node 가 스스로 요청
  //: `request_replan` 이 남긴 사유. `service_replan_requests` 가 소비한다.
  std::string requested_why_;
  int replan_streak_{0};              // 진전 없이 이어진 재계획 횟수(backoff 지수)
  double arrive_radius_{kArriveDefault};   // 도착 판정 반경(m) — 맵 축척마다 다름
  double prefetch_radius_{kPrefetchDefault};  // 경유 노드 선행 통과 반경(m). 0 이면 꺼짐
  bool full_path_{false};                  // true 면 남은 정점 전부 전송(단일 로봇 디버깅용)
  int resend_ticks_{7};                    // 이동 중 경로 재발행 주기(틱). 0=끔
  int return_goal_node_{17};                // RETURNING UI 표시용 복귀 waypoint
  bool patrol_{false};
  std::vector<int> patrol_route_;
  std::vector<int> security_patrol_route_;   // 야간 보안순회 루프(CCW 정규화)
  std::map<std::string, std::string> robot_mode_;   // 로봇 → PATROL|IDLE|STOP|CHARGE
  // CHARGING 이탈 후 충전소통로(v17) 직행을 한 번만 수행하는 래치.
  std::map<std::string, bool> charging_exit_pending_;
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
  //: 마지막으로 낸 시간표가 비어 있었나. 래치 토픽이라 "비었다" 도 한 번은 내야 하고,
  //  이미 냈으면 또 낼 필요가 없다(`clear_plan_once`). 시작 시점은 아무것도 안 낸
  //  상태이므로 true — 첫 계획이 나가기 전에 빈 계획을 내지 않는다.
  bool last_plan_empty_{true};
  rclcpp::Publisher<libi_fleet_msgs::msg::FleetRoutes>::SharedPtr route_pub_;
  rclcpp::Subscription<libi_fleet_msgs::msg::RobotHold>::SharedPtr hold_sub_;
  rclcpp::Subscription<libi_fleet_msgs::msg::NodeBlock>::SharedPtr node_block_sub_;

  // ── [2026-08-01] 공유 상태 잠금 ──────────────────────────────────────────
  //
  // 예전에는 `rclcpp::spin()` 단일 스레드라 잠금이 없어도 안전했다. MultiThreadedExecutor
  // 로 옮기면서 `tasks_`(19곳)·`robots_`(23곳)·`robot_mode_`·`hold_until_` 을 여러
  // 콜백이 동시에 만질 수 있게 됐다.
  //
  // **콜백 진입점 9곳에만** 건다. 내부 헬퍼는 전부 그 아래에서 불리므로 자동으로 덮인다 —
  // 헬퍼마다 거는 것보다 빠뜨릴 자리가 적다.
  //
  // ⚠️ recursive 인 이유: 진입점끼리 서로 부른다(on_submit → replan_all_routes → …).
  //    non-recursive 로 두면 그 경로에서 자기 자신에 걸려 굳는다.
  // ⚠️ **플래너 워커는 이 잠금을 잡지 않는다.** 거기가 긴 구간이고, 잡으면 애초에
  //    콜백 밖으로 뺀 의미가 사라진다. traffic_ 는 자기 잠금을 따로 갖고 있다.
  mutable std::recursive_mutex state_mu_;

  // 콜백그룹 — 서로 기다리지 않게 나눈다. 각 그룹은 MutuallyExclusive 라 그룹 안에서는
  // 직렬이고, 그룹끼리만 병렬이다. 공유 상태는 state_mu_ 가 지키므로 경쟁은 없다.
  rclcpp::CallbackGroup::SharedPtr cbg_timer_;   // 제어 루프 — 가장 늦으면 안 된다
  rclcpp::CallbackGroup::SharedPtr cbg_srv_;     // 서비스(배차·모드·리로드)
  rclcpp::CallbackGroup::SharedPtr cbg_sub_;     // 구독(로봇 상태·FSM·붙잡기)

  // 플래너 워커 — 탐색을 executor 스레드 밖으로 뺀다.
  std::thread planner_thread_;
  std::mutex planner_mu_;
  std::condition_variable planner_cv_;
  // ⚠️ **세대번호.** 탐색이 도는 동안 배차·취소·모드변경이 상태를 바꿀 수 있다. 이름으로
  //    task 를 다시 찾는 것만으로는 "그 사이 다른 일을 받은 로봇" 에 낡은 시간표를 씌우게
  //    된다. 스냅샷을 만든 세대와 결과를 적용하는 세대가 같을 때만 반영한다.
  //    (codex 지적: "결과 반영 시 계획 세대번호/로봇 상태를 재검증하라")
  uint64_t state_gen_{0};        // 상태가 바뀔 때마다 오른다
  uint64_t pending_gen_{0};
  uint64_t result_gen_{0};
  PlanSnapshot pending_snap_;
  //: 이번 재계획의 사유. 계획 요청과 결과에 같이 실려 `/fms/plan.reason` 이 된다.
  std::string pending_reason_;
  std::string result_reason_;
  PlanSnapshot result_snap_;
  std::vector<PlannedRoute> result_routes_;
  bool pending_ready_{false};
  bool result_ready_{false};
  bool planner_stop_{false};
  std::map<std::string, double> hold_until_;   // 로봇 → 붙잡기 만료 시각(steady 초)
  // ── 정점 차단 (2026-08-03) ────────────────────────────────────────────
  // 사람이 막았거나 도킹이 잡아 둔 정점. 유효시간이 지나면 스스로 푼다
  // (RobotHold 와 같은 이유 — 푸는 쪽이 죽어도 길이 영영 막히면 안 된다).
  // (정점, owner) 로 나눠 잡는다 — 같은 정점을 사람 차단과 서가 잠금이 함께 잡을 수
  // 있어, 소유자별로 따로 풀어야 한다(한쪽의 ttl<=0 이 남의 차단까지 지우면 안 된다).
  std::map<std::pair<int, std::string>, double> blocked_until_;        // (정점,owner) → 만료 시각(steady 초)
  std::map<std::pair<int, std::string>, std::string> blocked_reason_;  // (정점,owner) → 사유
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
  // ⚠️ 단일 스레드 spin 이 아니다. 예전에는 150 ms 타이머와 서비스 4개, 구독 6개가 한
  //    스레드를 놓고 줄을 섰다 — 서비스 하나가 느리면 그동안 도착 판정도 통행 허가도
  //    멈춘다. 콜백그룹을 나눠(아래 생성자) 서로 기다리지 않게 한다.
  //    공유 상태는 state_mu_ 하나로 지킨다.
  auto node = std::make_shared<libi_fleet::FleetNode>();
  rclcpp::executors::MultiThreadedExecutor exec;
  exec.add_node(node);
  exec.spin();
  rclcpp::shutdown();
  return 0;
}
