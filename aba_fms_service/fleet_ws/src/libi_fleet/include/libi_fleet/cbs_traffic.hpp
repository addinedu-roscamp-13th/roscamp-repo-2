#pragma once
// 교통관제 = CBS + 가중 Space-Time A* **시간 계획** + 실행 게이트.
//
// ReservationDeadlock 이 "이번 한 칸 들어가도 되나"를 그때그때 판정하는 반응형이라면,
// 이쪽은 **출발 전에 전 로봇의 시간표를 만들고** 그 시간표대로 통과를 열어 준다.
//
//   replan(snapshot)  ─ 전 로봇 start/goal + navgraph → 무충돌 시간표(경로+도착틱)
//   request_move(...) ─ 지금 틱이 계획상 출발 시각에 닿았나 → GRANT / 아직이면 WAIT
//
// ## 틱은 진짜 시간이다
// 간선 소요를 `ticks_for(레인 길이, 속도, 틱길이)` 로 넣으므로, 틱 하나가 실제 tick_seconds 다.
// 계획이 "v7 에 12틱" 이라 하면 계획 시작 후 12*tick_seconds 초를 뜻한다.
// 실행 게이트는 steady_clock 으로 잰 경과 시간을 틱으로 바꿔 비교한다.
//
// ## 어긋남을 어떻게 견디나 — 3단
//  ① clearance(계획 여유): 점유 구간을 서로 clearance 틱만큼 벌려서 계획한다.
//     조금 늦어도 계획의 무충돌 성질이 유지된다.
//  ② slack(실행 여유): 계획보다 slack 틱 **일찍** 와도 통과시킨다. 빨리 온 로봇을
//     굳이 세우면 뒤가 밀리기만 한다.
//  ③ 안전망(항상): 시간표가 열어 줘도 **그 노드를 남이 물리적으로 잡고 있으면 WAIT**.
//     계획은 예측이고 점유는 사실이다 — 사실이 이긴다.
//
// ## 계획이 낡으면 반응형으로 되돌아간다
// drift_limit 틱을 넘게 밀리거나(로봇이 안 옴), 계획에 없는 로봇이 물어보거나,
// 계획에 없는 이동(우회·수동 정지 후 재출발)이면 시간표를 버리고 **ReservationDeadlock 과
// 똑같이** 판정한다. 그래서 계획이 깨져도 교착으로 멈추지 않는다.
// (그 판정을 여기서 다시 구현하지 않고 그 클래스를 품어서 위임한다 — 교착 판정이 두 곳으로
//  갈라지면 한쪽만 고쳐진다.)
//
// ## DEADLOCK 을 계획 실패 신호로 쓰지 않는 이유
// fleet_node 는 DEADLOCK 을 받으면 **로컬 Dijkstra 우회**를 한다(fleet_node.cpp:534-543).
// 그러면 그 로봇만 시간표 밖으로 나가 전역 무충돌이 깨진다. 그래서 계획 모드에서는
// GRANT/WAIT 만 낸다. DEADLOCK 은 폴백(반응형)으로 내려간 뒤에만 나온다 — 그때는
// 우회가 맞는 대응이다.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#include "libi_fleet/cbs_planner.hpp"
#include "libi_fleet/reservation_deadlock.hpp"
#include "libi_fleet/traffic_base.hpp"

namespace libi_fleet
{
namespace
{

// 환경변수로 튜닝값을 읽는다. fleet_node 파라미터로 내리려면 노드 쪽 배선이 필요한데,
// pluginlib 인스턴스는 노드 핸들을 안 받는다. 값이 하드웨어 특성(속도)이라 손댈 일이
// 있으므로 상수로 박지 않는다.
double env_d(const char * key, double dflt)
{
  const char * v = std::getenv(key);
  if (!v || !*v) { return dflt; }
  char * end = nullptr;
  const double d = std::strtod(v, &end);
  return (end && end != v) ? d : dflt;
}
int env_i(const char * key, int dflt)
{
  return static_cast<int>(env_d(key, static_cast<double>(dflt)));
}

}  // namespace

class CbsTraffic : public TrafficBase
{
public:
  // 기본값은 **실물 nav2 파라미터에서 그대로 가져왔다**
  // (pinky_navigation/params/nav2_params.yaml, RegulatedPurePursuit):
  //     desired_linear_vel: 0.07           → speed_mps_    (:190)
  //     rotate_to_heading_angular_vel: 0.5 → turn_rad_s_   (:227)
  //     rotate_to_heading_min_angle: 0.35  → turn_min_rad_ (:222)
  // 짐작이 아니라 로봇이 실제로 지시받는 값이다. 그 파일을 바꾸면 여기도 같이 바꿔야 한다.
  //
  // ⚠️ [2026-08-02] **`turn_rad_s_` 가 0.15 로 적혀 있었다 — 실물의 3.3배 느린 값이다.**
  //    주석은 "실물에서 그대로 가져왔다" 고 단언했는데 회전 각속도만 틀렸다(다른 둘은 일치).
  //    90° 회전을 **10.5초**로 잡았고 실제는 3.14초다. 그 오차가 간선 하나를 19틱으로
  //    부풀렸다 — 실측은 5~8초다.
  //    실측 근거(순회 1랩, `통과 vN` 로그 타임스탬프, arte2):
  //        간선 실측 5~8초(중앙값 ~7초) · 회전+정지 몫 2~3초 · 유효 0.051 m/s
  //    과대평가는 안전 쪽이지만 대가가 있다 — CBS 가 필요 이상으로 로봇을 벌려 놓아
  //    통로가 비었는데도 못 들어가고, `slack_` 을 넘게 일찍 오면 WAIT 로 세워진다.
  CbsTraffic()
  : tick_seconds_(env_d("LIBI_CBS_TICK_SEC", 1.0)),
    speed_mps_(env_d("LIBI_CBS_SPEED_MPS", 0.07)),
    turn_rad_s_(env_d("LIBI_CBS_TURN_RAD_S", 0.5)),
    turn_min_rad_(env_d("LIBI_CBS_TURN_MIN_RAD", 0.35)),
    clearance_(env_i("LIBI_CBS_CLEARANCE", 1)),
    // 예약 시각의 허용 오차는 **비대칭이다 — 일찍 10초, 늦게 5초**(tick_seconds_=1.0 기준).
    //   slack_       = 일찍 온 쪽 — 계획보다 이만큼 빠르면 그냥 통과시킨다
    //   drift_limit_ = 늦게 온 쪽 — 예약 시각을 **이만큼 넘기면** 그 자리에서 컷이다
    //
    // ## 한 간선에 할당되는 시간 (사용자 스펙 2026-08-02)
    //
    //     예약 = 직진(d / speed_mps_) + 회전(Δθ / turn_rad_s_) + node_stop_ticks_
    //     컷   = 예약 + drift_limit_
    //
    //   예: 0.32 m · 90° 회전 → 4.6 + 3.1 + 1 = 약 9초 예약, +5초 = **14초에 컷**.
    //   화면은 그 컷까지 남은 초를 카운트다운하고, 0 이 되는 순간 재계획이 걸린다.
    //
    // ## 왜 5 인가
    //
    // 하한은 **1** 이다. 틱이 1초라 0 으로 두면 0.1초 흔들림도 틱 경계를 넘는 순간
    // 지연이 되어 재계획이 폭주한다(실측 2026-08-02: 2분에 16회, 전부 "초과 0.1s").
    // 실측 오차가 0.1~0.3초이므로 5 면 17배 여유이고, 진짜 막힘은 5초에 잡는다.
    //
    // ⚠️ **관용은 계획이 넉넉할 때만 작아질 수 있다.** 계획이 실물보다 빠르면 로봇은
    //    늘 늦고, 관용을 아무리 줄여도 재계획만 늘 뿐 아무것도 안 고쳐진다.
    //    그 넉넉함은 패딩(`node_stop_ticks_`)이 아니라 **맞는 모델**에서 나와야 한다 —
    //    같은 날 `turn_rad_s_` 를 실물 값으로 고치자 간선 19틱이 약 8틱이 됐고,
    //    실측 5~8초와 맞아 패딩 없이도 여유가 생겼다.
    //
    // ⚠️ 0 이 처음에 멀쩡해 **보였던** 이유: 그때는 커밋 노드(지금 가고 있는 칸)에
    //    마감 자체가 없어서 그 구간이 무감지였다. 그 구멍을 메우자(`commit_deadline_tick`)
    //    0 의 대가가 곧바로 드러났다. 관용은 그 마감이 **실제로 도는 상태**에서 정해야 한다.
    //
    // 반대로 **일찍 온 것은 아무 약속도 깨지 않는다.** 빨리 온 로봇을 세우면 뒤만 밀린다.
    //
    // ⚠️ **화면도 같은 값을 쓴다** — `/fms/plan` 의 `drift_limit`(fleet_node.cpp
    //    `plan_deadline_slack`). 화면의 카운트다운이 0 이 되는 순간과 재계획이 걸리는
    //    순간이 같아야 하므로, 한쪽만 바꾸면 화면이 거짓말을 시작한다.
    //
    // ⚠️ 예전엔 slack_ 도 1 이라 **양쪽 다** 빡빡했다. 그때 계획보다 2초만 빨라도 :233 에서
    //    WAIT 로 세웠고, 실물의 가감속·회전 흔들림이 전부 정지로 바뀌어 멈칫이 쌓였고,
    //    그 멈칫이 반대쪽(지연) 문턱을 넘겨 재계획을 유발했다. 그 되먹임을 끊는 것은
    //    **일찍 온 쪽을 열어 주는 것**이지 늦은 쪽을 봐주는 것이 아니다. slack_ 은 그대로 둔다.
    //
    // ⚠️ 관용 0 은 재계획이 잦아진다는 뜻이고, 그래서 **모델 보정이 선택이 아니라 전제가 된다.**
    //    계획이 실물보다 빠르면(SPEED_MPS 과대·TURN_RAD_S 과대·NODE_STOP_TICKS 과소)
    //    로봇은 늘 늦고 시간표는 늘 버려져 CBS 가 사실상 꺼진 채로 돈다. 재계획이 끊이지
    //    않으면 문턱이 아니라 build_graph 의 소요 시간 모델을 먼저 의심한다(:280 머리말).
    slack_(env_i("LIBI_CBS_SLACK", 10)),
    drift_limit_(env_i("LIBI_CBS_DRIFT_LIMIT", 5)),
    // 간선마다 무조건 더하는 여유(틱). 노드 진입 감속·정지·재출발에 실제로 드는 시간이자,
    // **관용을 작게 유지하기 위한 밑천**이다.
    //
    // ⚠️ [2026-08-02] 1 → 3 으로 올렸다가 **다시 1 로 되돌렸다.**
    //    올린 이유는 "계획이 실물보다 2% 빠르다" 였는데, 그 진단이 틀렸다 —
    //    같은 날 `turn_rad_s_` 가 실물의 3.3배 느리게 박혀 있는 것을 찾았고, 그걸
    //    고치자 계획이 오히려 **넉넉해졌다**(간선 19틱 → 약 8틱, 실측 5~8초).
    //    모델이 맞으면 패딩으로 덮을 게 없다. 패딩은 모델 오차를 가리는 값이라,
    //    남겨 두면 다음 사람이 진짜 오차를 못 본다.
    //
    //    1 은 노드 진입 감속·정지·재출발에 실제로 드는 최소 몫이다(실측 회전+정지 2~3초
    //    중 회전이 3.14초를 차지하므로 나머지가 이 정도다).
    node_stop_ticks_(env_i("LIBI_CBS_NODE_STOP_TICKS", 1))
  {
  }

  bool plans_routes() const override { return true; }

  // 호출자가 "계획 도착틱" 을 실제 시각으로 바꿀 때 쓴다. 이 값의 주인은 플러그인이다 —
  // fleet_node 가 같은 환경변수를 따로 읽으면 두 곳이 갈라진다.
  double tick_seconds() const override { return tick_seconds_; }

  // 시각화 도구(cbs_viz)가 **같은 시간 모델**을 쓰게 열어 둔다. 뷰어가 규칙을 따로
  // 구현하면 화면과 실제가 갈라진다 — 그러면 뷰어는 검증 도구가 아니라 거짓말이 된다.
  TimedGraph graph_for(const Navgraph & ng, const std::vector<int> & blocked) const
  {
    return build_graph(ng, blocked);
  }
  double speed_mps() const { return speed_mps_; }
  int clearance() const { return clearance_; }
  int drift_limit() const override { return drift_limit_; }

  // 시간표가 못 쓰게 됐다 → fleet_node 가 스냅샷을 다시 모아 replan() 을 부른다.
  bool needs_replan() const override
  {
    std::lock_guard<std::mutex> lk(mu_);
    return replan_wanted_;
  }

  // 마지막으로 계획을 버린 이유. 재계획이 잦을 때 원인을 가르는 유일한 단서다.
  std::string last_demote_reason() const override
  {
    std::lock_guard<std::mutex> lk(mu_);
    return last_demote_ ? last_demote_ : "";
  }

  // ── 계획 ────────────────────────────────────────────────────────────────
  //
  // ⚠️ **탐색은 잠금 밖에서 한다.**
  //    CBS 는 상한(max_nodes/max_expansions)까지 수만 번 확장할 수 있다. 그동안 뮤텍스를
  //    쥐고 있으면 request_move 가 전부 막혀 **로봇 전체가 선다** — 계획하려다 운행을
  //    멈추는 셈이다. 잠금은 결과를 갈아 끼우는 순간에만 잡는다.
  //
  //    부수 효과가 하나 있는데 오히려 낫다: 탐색이 도는 동안 **예전 시간표가 그대로 살아
  //    있다.** 예전 구조는 진입하자마자 plan_ 을 비워서, 계획을 다시 짜는 내내 반응형으로
  //    떨어졌다. 이제는 새 계획이 준비된 순간에만 교체된다.
  //
  //    탐색이 읽는 것은 인자(snap)와 const 튜닝값뿐이라 잠금이 필요 없다.
  std::vector<PlannedRoute> replan(const PlanSnapshot & snap) override
  {
    std::vector<int> starts, goals;
    std::vector<std::string> names;
    std::vector<Route> routes;
    //: 커밋 간선(`committed_from → start`)의 예산(틱). `names` 와 같은 순서. -1 = 모른다.
    //  **여기서 계산하는 것이 요점이다** — `build_graph` 가 이미 회전·정지·slow-edge 까지
    //  넣은 값을 들고 있으므로, 호출자가 거리·속도로 다시 계산하면 모델이 갈라진다.
    std::vector<int> commit_ticks;

    if (snap.graph && !snap.robots.empty()) {
      const TimedGraph g = build_graph(*snap.graph, snap.blocked, snap.slow_edges);
      const int n = static_cast<int>(g.size());
      bool ok = true;
      starts.reserve(snap.robots.size());
      goals.reserve(snap.robots.size());
      names.reserve(snap.robots.size());
      commit_ticks.reserve(snap.robots.size());
      for (const auto & r : snap.robots) {
        if (r.start < 0 || r.goal < 0 || r.start >= n || r.goal >= n) { ok = false; break; }
        starts.push_back(r.start);
        goals.push_back(r.goal);
        names.push_back(r.robot);
        commit_ticks.push_back(edge_cost_ticks(g, r.committed_from, r.start));
      }
      if (ok) {
        PlanOptions opt;
        opt.clearance = clearance_;
        // ⚠️ [2026-08-02] **커밋 예산을 출발 시각으로 넘긴다 — 계획 자체가 그걸 알아야 한다.**
        //
        //   처음엔 `commit_deadline_tick` 으로 결과만 내보내고 계획은 `start` 에서 0틱
        //   출발로 뒀다. 그러면 로봇이 실제로 그 칸을 6~7초 타고 도착해 **다음 간선을
        //   요청하는 순간** `request_move` 가 `now - depart`(depart≈0)를 보고 무조건
        //   지연으로 강등한다. 실기 실측(2026-08-02): 재계획 사유의 90%가 그것이었고
        //   6~7초마다 시간표를 버렸다.
        //
        //   결과 배열만 사후에 밀면 안 된다 — 다른 로봇과의 **충돌 제약이 옛 시간축에
        //   남아** 보장이 깨진다(codex 지적 P0). 출발 시각으로 넣으면 arrive/depart 와
        //   제약이 한 시간축에서 같이 밀린다.
        opt.start_ticks = commit_ticks;
        routes = cbs_plan_timed(g, starts, goals, opt);   // ← 긴 구간. 잠금 없음.
      }
    }

    // ── 여기서부터만 잠근다: 결과 반영 ────────────────────────────────────
    std::lock_guard<std::mutex> lk(mu_);
    replan_wanted_ = false;              // 요청은 여기서 소비된다(성공하든 실패하든)

    if (routes.size() != names.size() || routes.empty()) {
      plan_.clear();
      stale_ = true;                     // 해 없음 → 반응형으로 돈다
      return {};
    }

    plan_.clear();
    epoch_ = std::chrono::steady_clock::now();
    stale_ = false;
    std::vector<PlannedRoute> out;
    out.reserve(routes.size());
    for (size_t i = 0; i < routes.size(); ++i) {
      plan_[names[i]] = routes[i];
      PlannedRoute pr;
      pr.robot = names[i];
      // 커밋 노드에 닿기까지 줄 예산. 호출자가 커밋 간선을 안 실어 보냈으면 -1 이다.
      // (`commit_ticks` 는 `names` 와 같은 순서로만 채워진다 — 위 루프 참고.)
      pr.commit_deadline_tick = (i < commit_ticks.size()) ? commit_ticks[i] : -1;
      for (const auto & s : routes[i]) {
        pr.path.push_back(s.v);
        pr.arrive_tick.push_back(s.arrive);
      }
      out.push_back(std::move(pr));
    }
    return out;
  }

  // ── 실행 게이트 ─────────────────────────────────────────────────────────
  // ⚠️ **fallback_ 에도 넘겨야 한다.** 물리 점유 판정은 그쪽이 한다(request_move 참고).
  //    안 넘기면 CBS 모드에서 근접 규칙이 조용히 꺼진 채로 돈다 — 실제로 그랬다.
  void set_min_separation(const Navgraph & g, double min_sep) override
  {
    TrafficBase::set_min_separation(g, min_sep);
    fallback_.set_min_separation(g, min_sep);
  }

  MoveDecision request_move(const std::string & robot, int from, int to, int priority) override
  {
    std::lock_guard<std::mutex> lk(mu_);

    // ③ 안전망은 계획 모드에서도 항상 먼저다. 점유는 사실, 계획은 예측이다.
    //    (from==to 인 claim 은 fallback 이 그대로 처리한다)
    const MoveDecision phys = fallback_.request_move(robot, from, to, priority);
    if (from == to) { return phys; }
    if (phys == MoveDecision::WAIT) { return MoveDecision::WAIT; }

    if (stale_) { return phys; }         // 폴백(반응형) 모드 — DEADLOCK 도 그대로 통과

    auto it = plan_.find(robot);
    if (it == plan_.end()) {
      // ⚠️ **강등하지 않는다.** 계획에 없는 로봇(순회 등)은 애초에 시간표의 대상이 아니다.
      //    예전엔 여기서 강등해 전체 시간표를 버렸는데, 순회 로봇이 한 칸 움직일 때마다
      //    배달 로봇들의 계획까지 날아갔다 — 시나리오 로그가 "계획에 없는 이동" 으로 도배됐다.
      //    이 로봇만 반응형으로 처리하면 된다. 남의 계획은 그대로 두고,
      //    물리 예약(안전망)이 이 로봇과 계획 로봇 사이를 막아 준다.
      return phys;
    }

    const Route & r = it->second;
    const int step = find_step(r, from, to);
    // 계획에 **있는** 로봇이 계획에 없는 칸을 요청하면 그건 진짜 이탈이다 — 그때는 강등한다.
    if (step < 0) { return demote(phys, "계획 로봇의 경로 이탈"); }

    const int now = now_tick();
    const int depart = r[step].depart;

    // 너무 늦으면 시간표가 의미를 잃는다 → 반응형으로 내려간다.
    if (now - depart > drift_limit_) { return demote(phys, "계획 대비 지연"); }

    // ② 계획보다 slack 만큼 일찍 와도 통과.
    if (now + slack_ < depart) {
      // ⚠️ 방금 ③에서 잡은 예약을 **반드시 놓는다.**
      //    안 놓으면 "나는 대기 중인데 그 노드는 내가 쥐고 있는" 상태가 된다. 계획상 나보다
      //    **먼저** 그 정점을 지나기로 한 로봇이 내 예약에 막혀 못 간다 — 시간으로 분리해
      //    얻은 이득이 통째로 사라지고, 그 로봇은 늦어져 결국 재계획을 유발한다.
      //    (물리 예약을 먼저 시도하는 순서 자체는 유지한다 — 남이 이미 쥐고 있으면 :173 에서
      //     걸러져야 하기 때문이다. 여기서 놓는 것은 **내가 방금 잡은 것**뿐이다.)
      fallback_.release_node(robot, to);
      return MoveDecision::WAIT;
    }

    // 물리 예약은 위에서 이미 GRANT/DEADLOCK 으로 받았다. 계획 모드에서는
    // DEADLOCK 을 올리지 않는다(호출자가 우회해 시간표 밖으로 나가 버린다).
    return MoveDecision::GRANT;
  }

  void release(const std::string & robot, int node) override
  {
    std::lock_guard<std::mutex> lk(mu_);
    fallback_.release(robot, node);
  }

  void release_node(const std::string & robot, int node) override
  {
    std::lock_guard<std::mutex> lk(mu_);
    fallback_.release_node(robot, node);
  }

  std::vector<std::pair<int, std::string>> occupancy() const override
  {
    std::lock_guard<std::mutex> lk(mu_);
    return fallback_.occupancy();
  }

private:
  // navgraph → 가중 그래프. 레인 길이를 실제 주행 소요 틱으로 바꾼다.
  // blocked 정점은 아예 들어가지 못하게 진입 간선을 끊는다(영구 장애물).
  //
  // ⚠️ 순항속도만으로는 실물 시간이 안 맞는다. 로봇은 노드마다 **감속·정지하고 방향을 튼다**
  //    (prefetch_radius 가 꺼져 있으면 특히). 그 시간이 짧은 레인에서는 주행 시간보다 크다.
  //    각 간선에 node_stop_ticks_ 를 더해 그 몫을 잡는다 — 회전각까지 반영하려면 간선쌍
  //    (진입방향, 진출방향) 단위로 확장해야 하므로, 우선 정점당 상수로 근사한다.
  //
  //    이 값들은 **재보정하라고 있는 손잡이**다. 실측이 유일한 최종 근거다:
  //      실제 한 레인 통과 시간을 재고 → SPEED_MPS / TURN_RAD_S / NODE_STOP_TICKS 를 맞춘다.
  //    계획이 실물보다 빠르면 로봇이 계속 늦어 강등되고, 느리면 통로를 오래 잠근다.
  //
  // 회전 시간은 **navgraph 기하에서 계산한다** — 들어온 방향과 나갈 방향의 사잇각을 재서
  // rotate_to_heading_min_angle 을 넘으면 각속도로 나눈다. nav2 가 실제로 하는 일과 같은 모델이다.
  // 들어오는 방향이 여럿이면 평균을 쓴다.
  // ponytail: 진입방향 평균 근사. 정확히 하려면 (진입간선, 진출간선) 쌍으로 그래프를
  //   확장해야 한다(정점 수 ×평균차수). 교차로가 병목이 되면 그때 승급.
  /// `from → to` 간선의 통과 비용(틱). 그 간선이 없으면 -1.
  ///
  /// `build_graph` 가 이미 넣어 둔 값을 그대로 읽는다 — 거리·회전·노드 정지·slow-edge
  /// 벌점이 전부 반영돼 있다. 이 값을 밖에서 다시 계산하려 들면(예: 거리/속도) 모델이
  /// 갈라지고, 갈라진 순간 화면·판정·플래너가 서로 다른 말을 하기 시작한다.
  static int edge_cost_ticks(const TimedGraph & g, int from, int to)
  {
    if (from < 0 || to < 0 || from >= static_cast<int>(g.size())) { return -1; }
    for (const auto & e : g[from]) {
      if (e.first == to) { return e.second; }
    }
    return -1;
  }

  TimedGraph build_graph(const Navgraph & ng, const std::vector<int> & blocked,
                         const std::vector<SlowEdge> & slow = {}) const
  {
    const int n = ng.size();
    // 역인접(진입 간선) — 회전각 평균에 필요하다.
    std::vector<std::vector<int>> preds(n);
    for (int v = 0; v < n; ++v) {
      for (int w : ng.neighbors(v)) { preds[w].push_back(v); }
    }

    TimedGraph g(n);
    for (int v = 0; v < n; ++v) {
      for (int w : ng.neighbors(v)) {
        if (std::find(blocked.begin(), blocked.end(), w) != blocked.end()) { continue; }
        const Vertex & a = ng.vertex(v);
        const Vertex & b = ng.vertex(w);
        const double dist = std::hypot(b.x - a.x, b.y - a.y);
        const double travel = (speed_mps_ > 0.0) ? dist / speed_mps_ : 0.0;
        const double turn = turn_seconds(ng, preds[v], v, w);
        const int ticks = ticks_for(1.0, 1.0 / std::max(1e-9, travel + turn), tick_seconds_);
        // 반복해서 마감을 못 지킨 간선은 **비싸게** 만든다(막지 않는다 — SlowEdge 머리말).
        int extra = 0;
        for (const auto & e : slow) {
          if (e.from == v && e.to == w) { extra = e.extra_ticks; break; }
        }
        g[v].push_back({w, ticks + node_stop_ticks_ + extra});
      }
    }
    return g;
  }

  // v 에서 w 로 나갈 때의 제자리 회전 소요(초). 진입 방향들의 평균 사잇각을 쓴다.
  // nav2 의 use_rotate_to_heading 모델과 같다 — 사잇각이 min_angle 이하면 회전 없이 전진.
  double turn_seconds(const Navgraph & ng, const std::vector<int> & preds, int v, int w) const
  {
    if (turn_rad_s_ <= 0.0 || preds.empty()) { return 0.0; }
    const Vertex & pv = ng.vertex(v);
    const Vertex & nv = ng.vertex(w);
    const double out = std::atan2(nv.y - pv.y, nv.x - pv.x);

    // ⚠️ 평균이 아니라 **최댓값**을 쓴다.
    //    어느 방향에서 올지는 계획 시점에 모른다. 평균으로 잡으면 실제 진입각이 평균보다
    //    큰 경우 그만큼 늦고, 늦으면 마감 초과 → 재계획이 돈다.
    //    (실측: 평균을 쓴 첫 시나리오에서 v7→v13 을 12초로 잡았는데 실제 22초가 걸려
    //     3초마다 재계획이 반복됐다. 90도 회전 하나가 1.57/0.15 ≈ 10.5초다.)
    //    최댓값은 직진 통과를 과대평가하지만, 늦는 것보다 **여유 있게 잡는 편이 안전하다** —
    //    일찍 온 로봇은 slack 으로 통과시키면 되고, 늦은 로봇은 계획 자체를 깨뜨린다.
    double worst = 0.0;
    int cnt = 0;
    for (int u : preds) {
      if (u == w) { continue; }              // 되돌아가기(U턴)는 아래에서 따로 센다
      const Vertex & uv = ng.vertex(u);
      const double in = std::atan2(pv.y - uv.y, pv.x - uv.x);
      double diff = std::fabs(out - in);
      while (diff > M_PI) { diff = 2.0 * M_PI - diff; }
      if (diff > turn_min_rad_) { worst = std::max(worst, diff); }
      cnt++;
    }
    // 진입로가 **왔던 길 하나뿐**이면(막다른 정점) 되돌아 나가는 것이 유일한 선택이고,
    // 그건 제자리 180도 회전이다. 예전엔 그 경우 cnt==0 이라 회전시간 0 을 냈다 —
    // 막다른 서가·주차 정점이 공짜로 보여 계획이 실제보다 빠르게 나왔다.
    if (cnt == 0) {
      const bool only_back = std::find(preds.begin(), preds.end(), w) != preds.end();
      return only_back ? (M_PI / turn_rad_s_) : 0.0;
    }
    return worst / turn_rad_s_;
  }

  int now_tick() const
  {
    const auto dt = std::chrono::steady_clock::now() - epoch_;
    const double sec = std::chrono::duration<double>(dt).count();
    return static_cast<int>(sec / tick_seconds_);
  }

  // 계획에서 from→to 로 넘어가는 칸의 인덱스. 없으면 -1.
  static int find_step(const Route & r, int from, int to)
  {
    for (size_t i = 0; i + 1 < r.size(); ++i) {
      if (r[i].v == from && r[i + 1].v == to) { return static_cast<int>(i); }
    }
    return -1;
  }

  // 계획 모드에서 반응형으로 내려간다.
  //
  // 예전에는 여기서 끝이었다 — 한 번 밀리면 **영영** 반응형으로 남았다. 장애물·지체는
  // 정상 운영에서 늘 일어나므로, 그때마다 계획으로 되돌아올 길이 있어야 한다.
  // replan_wanted_ 를 세워 두면 fleet_node 가 다음 틱에 스냅샷을 새로 모아 replan() 을 부른다.
  MoveDecision demote(MoveDecision phys, const char * why)
  {
    stale_ = true;
    plan_.clear();
    replan_wanted_ = true;
    last_demote_ = why;   // 호출자가 로그로 드러낸다 — 왜 계획을 버렸는지 모르면 못 고친다
    return phys;
  }

  mutable std::mutex mu_;
  ReservationDeadlock fallback_;                  // 안전망 겸 폴백(교착 판정 포함)
  std::map<std::string, Route> plan_;
  std::chrono::steady_clock::time_point epoch_{};
  bool stale_{true};
  bool replan_wanted_{false};
  const char * last_demote_{nullptr};

  const double tick_seconds_;
  const double speed_mps_;
  const double turn_rad_s_;
  const double turn_min_rad_;
  const int clearance_;
  const int slack_;
  const int drift_limit_;
  const int node_stop_ticks_;
};

}  // namespace libi_fleet
