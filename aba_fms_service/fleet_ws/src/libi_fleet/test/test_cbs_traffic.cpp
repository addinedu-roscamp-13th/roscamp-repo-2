// CbsTraffic 실행 게이트 회귀 테스트.
//
// 계획(cbs_planner)이 맞는 것과 **그 계획대로 통과를 열어 주는 것**은 다른 문제다.
// 여기서 잡으려는 것은 후자에서 조용히 틀리는 경우다:
//   · 계획보다 이른 요청을 GRANT 해 버리는가 (시간 게이트가 실제로 잠기는가)
//   · 시간이 되면 열리는가
//   · 계획이 열어 줘도 남이 물리적으로 잡고 있으면 막는가 (안전망이 계획보다 우선)
//   · 계획에 없는 로봇/이동이 오면 멈추지 않고 반응형으로 내려가는가
//
// 틱을 0.02초로 줄여(LIBI_CBS_TICK_SEC) 실제 시간 대기를 짧게 만든다. 게이트가 진짜
// steady_clock 을 보는지까지 함께 검증된다 — 가짜 시계를 주입하면 그건 못 잡는다.

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

#include "libi_fleet/cbs_traffic.hpp"
#include "libi_fleet/navgraph.hpp"

using libi_fleet::MoveDecision;
using libi_fleet::PlanRequest;
using libi_fleet::PlanSnapshot;

namespace
{

constexpr double kTickSec = 0.02;

// 각 테스트가 자기 튜닝값으로 인스턴스를 만든다. 값은 생성자에서 읽으므로 **만들기 전에** 건다.
void set_env(int clearance, int slack, int drift_limit)
{
  setenv("LIBI_CBS_TICK_SEC", "0.02", 1);
  setenv("LIBI_CBS_SPEED_MPS", "0.15", 1);
  setenv("LIBI_CBS_CLEARANCE", std::to_string(clearance).c_str(), 1);
  setenv("LIBI_CBS_SLACK", std::to_string(slack).c_str(), 1);
  setenv("LIBI_CBS_DRIFT_LIMIT", std::to_string(drift_limit).c_str(), 1);
  unsetenv("LIBI_CBS_TURN_RAD_S");
  unsetenv("LIBI_CBS_NODE_STOP_TICKS");
}

// 실제 시간을 기다려야 하는 테스트용. 모델 속도를 크게 올려 계획 길이를 몇 틱으로 줄인다.
//
// ⚠️ 틱 길이(TICK_SEC)를 줄이는 것으로는 안 된다 — 그러면 같은 실제 시간이 **더 많은 틱**이
//    될 뿐 기다리는 초는 그대로다(실측: 37초). 줄여야 하는 건 모델상 주행/회전 **시간**이다.
void set_env_fast_model()
{
  setenv("LIBI_CBS_SPEED_MPS", "20.0", 1);     // 주행 시간 ≈ 0
  setenv("LIBI_CBS_TURN_RAD_S", "100.0", 1);   // 회전 시간 ≈ 0
  setenv("LIBI_CBS_NODE_STOP_TICKS", "1", 1);  // 간선당 1틱만 남는다
}

void sleep_ticks(double ticks)
{
  std::this_thread::sleep_for(std::chrono::duration<double>(ticks * kTickSec));
}

libi_fleet::Navgraph load_graph()
{
  libi_fleet::Navgraph g;
  EXPECT_TRUE(g.load(TEST_NAVGRAPH_PATH, "L1")) << "테스트 navgraph 로드 실패";
  return g;
}

PlanSnapshot make_snapshot(const libi_fleet::Navgraph & g, std::vector<PlanRequest> robots)
{
  PlanSnapshot s;
  s.graph = &g;
  s.robots = std::move(robots);
  return s;
}

}  // namespace

// ── 계획이 서고, 경로를 **돌려주는가** ────────────────────────────────
//
// 이게 이 설계의 핵심이다. void 훅이었다면 플러그인이 대기소로 비켜 가는 경로를 골라도
// fleet_node 는 그걸 모른 채 자기 dijkstra 경로를 계속 발행한다 — 계획이 무의미해진다.
TEST(CbsTraffic, ReplanReturnsRoutes)
{
  set_env(1, 1, 10);
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();
  ASSERT_GE(g.size(), 4) << "테스트 navgraph 가 너무 작다";

  EXPECT_TRUE(tr.plans_routes()) << "계획형이라고 밝혀야 fleet_node 가 snapshot 을 만든다";

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}, {"B", 3, 0, 0}});
  const auto routes = tr.replan(snap);

  ASSERT_EQ(routes.size(), 2u) << "두 대 모두 계획이 나와야 한다";
  for (const auto & r : routes) {
    EXPECT_GE(r.path.size(), 2u);
    EXPECT_EQ(r.path.size(), r.arrive_tick.size()) << "경로와 도착틱 길이가 같아야 한다";
    for (size_t i = 1; i < r.arrive_tick.size(); ++i) {
      EXPECT_GT(r.arrive_tick[i], r.arrive_tick[i - 1]) << "도착틱은 단조 증가해야 한다";
    }
  }
  EXPECT_EQ(routes[0].robot, "A");
  EXPECT_EQ(routes[1].robot, "B");
}

// ── 🔴 회귀: 계획보다 이른 요청은 막아야 한다 ────────────────────────
//
// 시간표를 만들어 놓고 게이트가 항상 GRANT 면 GrantAllTraffic 과 다를 게 없다.
// 계획상 나중에 떠나기로 한 로봇이 지금 물어보면 WAIT 여야 한다.
TEST(CbsTraffic, GateBlocksBeforePlannedDeparture)
{
  set_env(1, 0, 1000);            // slack 0 — 딱 계획 시각에만 열린다
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}, {"B", 3, 0, 0}});
  const auto routes = tr.replan(snap);
  ASSERT_EQ(routes.size(), 2u);

  // 계획상 **가장 늦게 출발하는** 칸을 찾는다. 그 칸을 지금 물어보면 아직 이르다.
  int best_robot = -1, from = -1, to = -1, depart = -1;
  for (size_t i = 0; i < routes.size(); ++i) {
    for (size_t k = 0; k + 1 < routes[i].path.size(); ++k) {
      // 이 칸의 출발 시각 = 다음 정점 도착틱 - 간선 소요. 간선 소요를 모르므로
      // 보수적으로 "다음 도착틱" 을 상한으로 쓴다 — 그보다 이른 지금은 확실히 이르다.
      const int d = routes[i].arrive_tick[k + 1];
      if (d > depart) {
        depart = d;
        best_robot = static_cast<int>(i);
        from = routes[i].path[k];
        to = routes[i].path[k + 1];
      }
    }
  }
  ASSERT_GE(best_robot, 0);
  ASSERT_GT(depart, 1) << "0틱에 출발하는 칸뿐이면 이 테스트가 의미가 없다";

  const std::string & who = routes[best_robot].robot;
  tr.request_move(who, from, from, 0);   // 현재 노드 claim
  EXPECT_EQ(tr.request_move(who, from, to, 0), MoveDecision::WAIT)
    << "계획상 " << depart << "틱에 갈 칸인데 0틱에 열어 줬다";
}

// ── 시간이 되면 열린다 ────────────────────────────────────────────────
TEST(CbsTraffic, GateOpensWhenTimeComes)
{
  set_env(1, 1, 1000);
  set_env_fast_model();          // 실제로 기다리는 테스트라 계획 길이를 줄인다
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}});
  const auto routes = tr.replan(snap);
  ASSERT_EQ(routes.size(), 1u);
  ASSERT_GE(routes[0].path.size(), 2u);

  const int from = routes[0].path[0], to = routes[0].path[1];
  tr.request_move("A", from, from, 0);
  // 첫 칸은 0틱 출발이라 바로 열려야 한다(단독 로봇).
  EXPECT_EQ(tr.request_move("A", from, to, 0), MoveDecision::GRANT);

  if (routes[0].path.size() >= 3) {
    const int nxt = routes[0].path[2];
    // 충분히 기다린 뒤에는 그 다음 칸도 열려야 한다.
    sleep_ticks(routes[0].arrive_tick[2] + 2.0);
    EXPECT_EQ(tr.request_move("A", to, nxt, 0), MoveDecision::GRANT)
      << "계획 시각이 지났는데도 안 열렸다";
  }
}

// ── 🔴 회귀: 계획이 열어 줘도 남이 잡고 있으면 막는다 ────────────────
//
// 계획은 예측이고 점유는 사실이다. 계획대로면 비어 있어야 할 노드에 다른 로봇이
// 실제로 서 있을 수 있다(지연·수동 정지). 그때 계획만 믿고 GRANT 하면 충돌한다.
TEST(CbsTraffic, PhysicalOccupancyBeatsPlan)
{
  set_env(0, 1000, 1000);         // slack 을 크게 — 시간 게이트는 항상 열린 셈
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}});
  const auto routes = tr.replan(snap);
  ASSERT_EQ(routes.size(), 1u);
  ASSERT_GE(routes[0].path.size(), 2u);
  const int from = routes[0].path[0], to = routes[0].path[1];

  // 계획에 없는 제3자가 목표 노드를 먼저 물리적으로 점유한다.
  ASSERT_EQ(tr.request_move("GHOST", to, to, 0), MoveDecision::GRANT);

  tr.request_move("A", from, from, 0);
  EXPECT_EQ(tr.request_move("A", from, to, 0), MoveDecision::WAIT)
    << "남이 점유한 노드를 계획만 믿고 열어 줬다";

  // 비켜 주면 열린다.
  tr.release_node("GHOST", to);
  EXPECT_EQ(tr.request_move("A", from, to, 0), MoveDecision::GRANT);
}

// ── 계획에 없는 로봇은 반응형으로 처리한다(멈추지 않는다) ─────────────
//
// 순회 로봇처럼 계획에 참여하지 않는 주체가 물어볼 수 있다. 그때 "계획에 없으니 WAIT"
// 로 영구히 막으면 그 로봇은 영원히 못 움직인다.
TEST(CbsTraffic, UnknownRobotFallsBackToReactive)
{
  set_env(1, 1, 1000);
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}});
  ASSERT_EQ(tr.replan(snap).size(), 1u);

  // 빈 그래프 노드 두 개를 임의로 쓴다 — 반응형이면 비어 있으니 GRANT 여야 한다.
  EXPECT_EQ(tr.request_move("PATROL-1", 1, 1, 0), MoveDecision::GRANT);
  const MoveDecision d = tr.request_move("PATROL-1", 1, 2, 0);
  EXPECT_NE(d, MoveDecision::WAIT) << "계획 밖 로봇을 영구히 막으면 안 된다";
}

// ── occupancy 는 계획 모드에서도 보고돼야 한다(관제 시각화) ──────────
TEST(CbsTraffic, OccupancyReported)
{
  set_env(1, 1, 1000);
  libi_fleet::CbsTraffic tr;
  ASSERT_EQ(tr.request_move("A", 2, 2, 0), MoveDecision::GRANT);

  const auto occ = tr.occupancy();
  ASSERT_EQ(occ.size(), 1u);
  EXPECT_EQ(occ[0].first, 2);
  EXPECT_EQ(occ[0].second, "A");
}

// ── 🔴 회귀: 시간 게이트가 WAIT 를 낼 때 예약을 쥐고 있으면 안 된다 ──
//
// request_move 는 물리 예약(fallback)을 **먼저** 시도한다 — 남이 이미 쥐고 있는지 봐야 하므로.
// 그런데 그 시도가 성공한 뒤 시간 게이트가 "아직 이르다" 며 WAIT 를 내면, 예약은 이 로봇이
// 쥔 채로 남는다. 그러면 계획상 **먼저** 그 정점을 지나기로 한 로봇이 내 예약에 막힌다 —
// 시간으로 분리해 얻은 이득이 통째로 사라지고, 막힌 로봇이 늦어 재계획까지 유발한다.
//
// (Codex 리뷰에서 지적받아 잡은 결함. 테스트가 WAIT 반환만 보고 점유를 안 봐서 놓쳤다.)
TEST(CbsTraffic, WaitDoesNotHoldReservation)
{
  set_env(1, 0, 1000);            // slack 0 — 계획 시각 전에는 확실히 WAIT
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}, {"B", 3, 0, 0}});
  const auto routes = tr.replan(snap);
  ASSERT_EQ(routes.size(), 2u);

  // 계획상 가장 늦게 지나는 칸을 고른다 — 지금 물어보면 반드시 이르다.
  int who = -1, from = -1, to = -1, latest = -1;
  for (size_t i = 0; i < routes.size(); ++i) {
    for (size_t k = 0; k + 1 < routes[i].path.size(); ++k) {
      if (routes[i].arrive_tick[k + 1] > latest) {
        latest = routes[i].arrive_tick[k + 1];
        who = static_cast<int>(i);
        from = routes[i].path[k];
        to = routes[i].path[k + 1];
      }
    }
  }
  ASSERT_GE(who, 0);
  ASSERT_GT(latest, 1);

  const std::string & name = routes[who].robot;
  tr.request_move(name, from, from, 0);
  ASSERT_EQ(tr.request_move(name, from, to, 0), MoveDecision::WAIT);

  for (const auto & [node, owner] : tr.occupancy()) {
    EXPECT_FALSE(node == to && owner == name)
      << "WAIT 를 내면서 목표 노드 v" << to << " 예약을 쥐고 있다 — 남이 못 지나간다";
  }

  // 같은 노드를 남이 잡을 수 있어야 한다(내가 안 쥐고 있다는 뜻).
  EXPECT_EQ(tr.request_move("OTHER", to, to, 0), MoveDecision::GRANT);
}

// ── 🔴 회귀: 막다른 정점에서 나가려면 제자리 180도 회전이다 ───────────
//
// 진입로가 왔던 길 하나뿐인 정점(주차장)에서 되돌아 나가는 것은 U턴이다. 예전에는
// "왔던 길 제외" 규칙 때문에 셀 진입방향이 하나도 안 남아 회전시간 0 을 냈다 —
// 막다른 정점이 공짜로 보여 계획이 실제보다 빠르게 나왔다.
TEST(CbsTraffic, DeadEndCostsUTurn)
{
  set_env(1, 1, 1000);
  setenv("LIBI_CBS_NODE_STOP_TICKS", "0", 1);   // 회전 몫만 보려고 정지 여유 제거
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();
  const auto tg = tr.graph_for(g, {});

  // [2026-08-01] **정점 번호를 박지 않는다.** 예전엔 "arte2 의 v0(주차장)" 을 전제했는데
  //   두 겹으로 낡아 있었다: 이 타깃이 로드하는 것은 arte2 가 아니라 new_map 이고,
  //   arte2 에도 `주차장` 정점은 없다(있는 것은 충전소통로·충전소입구·충전소).
  //   그래서 `neighbors(0).size()==1` 이 깨져 테스트가 전제에서 죽었다.
  //   이제 그래프에서 **차수 1인 정점을 찾아** 쓴다. 없으면 이 그래프로는 잴 수 없다.
  int dead = -1;
  for (int i = 0; i < g.size(); ++i) {
    if (g.neighbors(i).size() == 1u) { dead = i; break; }
  }
  if (dead < 0) {
    GTEST_SKIP() << "이 navgraph 에는 막다른 정점이 없다 — U턴 몫을 잴 대상이 없다";
  }
  const int w = g.neighbors(dead)[0];

  int cost = -1;
  for (const auto & [n, c] : tg[dead]) { if (n == w) { cost = c; } }
  ASSERT_GT(cost, 0);

  const auto & a = g.vertex(dead);
  const auto & b = g.vertex(w);
  const double travel = std::hypot(b.x - a.x, b.y - a.y) / tr.speed_mps();
  const int travel_only = libi_fleet::ticks_for(1.0, 1.0 / travel, tr.tick_seconds());

  EXPECT_GT(cost, travel_only)
    << "막다른 정점에서 나가는 비용이 순수 주행시간과 같다 — U턴 시간이 빠졌다";
  unsetenv("LIBI_CBS_NODE_STOP_TICKS");
}

// ── 🔴 회귀: 계획 중에도 게이트가 응답해야 한다 ───────────────────────
//
// 예전엔 replan() 이 뮤텍스를 쥔 채 CBS 를 돌렸다. 탐색은 상한까지 수만 번 확장할 수 있어서,
// 그동안 request_move 가 전부 막히고 **로봇 전체가 선다** — 계획하려다 운행을 멈추는 셈이다.
//
// 여기서 재는 것은 "탐색이 도는 동안 게이트가 계속 답하는가" 다. 정확한 시간이 아니라
// **막히지 않는다**는 성질을 본다(느린 CI 에서도 흔들리지 않게 여유를 크게 뒀다).
TEST(CbsTraffic, GateStaysResponsiveDuringReplan)
{
  set_env(1, 1, 1000);
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  // 먼저 한 번 계획해 둔다 — 탐색 중에도 예전 시간표로 답할 수 있어야 한다.
  ASSERT_EQ(tr.replan(make_snapshot(g, {{"A", 0, 3, 0}})).size(), 1u);

  std::atomic<bool> done{false};
  std::thread planner([&] {
    // ⚠️ **횟수가 아니라 시간으로 묶는다.** 계획 한 번의 비용은 지도와 틱 길이에 달려
    //    있어 고정 횟수로 두면 테스트 길이가 그것들을 따라 널뛴다 — 실제로 arte2 에
    //    레인 하나를 나눴더니(9-13 을 9-10-13 으로) 회전비용이 붙어 한 번이 3 ms 에서
    //    8.9 초가 됐고, 20회 고정이던 이 테스트가 3분으로 늘어 ament 기본 제한(60초)에
    //    걸렸다. 재는 것은 "계획이 도는 동안" 게이트가 열려 있느냐지, 계획 횟수가 아니다.
    //    (실운영 틱 1.0 초에서는 같은 계획이 1 ms 다 — 느린 것은 이 파일의 0.02 초뿐이다.)
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    do {
      tr.replan(make_snapshot(g, {{"A", 0, 3, 0}, {"B", 3, 0, 0}, {"C", 1, 2, 0}}));
    } while (std::chrono::steady_clock::now() < until);
    done = true;
  });

  int calls = 0;
  double worst = 0.0;
  const auto t0 = std::chrono::steady_clock::now();
  while (!done) {
    const auto before = std::chrono::steady_clock::now();
    tr.request_move("A", 0, 1, 0);       // 결과는 상관없다 — 돌아오기만 하면 된다
    worst = std::max(worst, std::chrono::duration<double>(
                              std::chrono::steady_clock::now() - before).count());
    calls++;
    if (std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() > 20.0) {
      break;   // 안전장치 — 여기 걸리면 계획이 안 끝나는 것이다
    }
  }
  planner.join();

  // ⚠️ 판정 기준을 "한 번이 N초 안에 온다" 로 두면 안 된다 — 계획 한 번이 수십 ms 라
  //    옛 코드(잠금 보유)도 그 기준을 통과한다. 실제로 갈리는 것은 **처리량**이다:
  //      잠금 보유 → 계획 20회 사이의 틈에서만 답하므로 수십 회 수준
  //      잠금 밖   → 게이트가 마이크로초라 수천~수만 회
  EXPECT_GT(calls, 1000) << "계획이 도는 동안 게이트가 " << calls
                         << "번밖에 못 돌았다 — 탐색이 잠금을 쥐고 있다는 뜻";
  EXPECT_LT(worst, 0.5) << "게이트 한 번이 " << worst << "초 막혔다";
}

// ── 스냅샷이 온전치 않으면 계획하지 않는다 ────────────────────────────
TEST(CbsTraffic, BadSnapshotPlansNothing)
{
  set_env(1, 1, 1000);
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  EXPECT_TRUE(tr.replan(make_snapshot(g, {})).empty()) << "로봇이 없으면 계획도 없다";
  EXPECT_TRUE(tr.replan(make_snapshot(g, {{"A", -1, 3, 0}})).empty()) << "정점 범위 밖";
  EXPECT_TRUE(tr.replan(make_snapshot(g, {{"A", 0, 99999, 0}})).empty()) << "정점 범위 밖";

  PlanSnapshot no_graph;
  no_graph.robots = {{"A", 0, 3, 0}};
  EXPECT_TRUE(tr.replan(no_graph).empty()) << "그래프가 없으면 계획할 수 없다";
}

// ── 🔴 회귀: 근접 정점 규칙이 CbsTraffic 을 **통과해서** 걸리는가 ─────────
//
// CbsTraffic 은 물리 점유 판정을 자기 안의 fallback_(ReservationDeadlock)에 위임한다.
// set_min_separation 을 바깥 객체에만 걸면 fallback_ 은 설정을 못 받아 규칙이 **조용히
// 꺼진 채로** 돈다. 실제로 그랬고, sim 에서 "규칙을 넣었는데 최소거리가 그대로"로 나타났다.
// 그건 규칙이 무력한 게 아니라 규칙이 전달되지 않은 것이었다.
//
// ⚠️ 이 규칙은 **노드 충돌만** 막는다. 간선을 지나는 동안 쓸고 가는 통로는 보호하지 않는다.
TEST(CbsTraffic, MinSeparationReachesPhysicalFallback)
{
  set_env(1, 1, 1000);
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  // 서로 다른 두 정점 중 **가장 가까운** 쌍을 찾는다. 지도가 바뀌어도 따라온다.
  int a = -1, b = -1;
  double best = 1e9;
  for (int i = 0; i < g.size(); ++i) {
    for (int j = i + 1; j < g.size(); ++j) {
      const double d = std::hypot(g.vertex(i).x - g.vertex(j).x,
                                  g.vertex(i).y - g.vertex(j).y);
      if (d < best) { best = d; a = i; b = j; }
    }
  }
  ASSERT_GE(a, 0);

  // 그 간격보다 **큰** 최소이격을 주면 두 정점은 동시 점유 불가여야 한다.
  tr.set_min_separation(g, best + 0.01);
  ASSERT_EQ(tr.request_move("R1", a, a, 0), MoveDecision::GRANT) << "먼저 온 로봇은 선다";
  EXPECT_EQ(tr.request_move("R2", b, b, 0), MoveDecision::GRANT)
    << "현재 위치 주장(from==to)은 막지 않는다 — 막아도 로봇이 안 비켜지고 기동만 막힌다";

  // 진입은 막혀야 한다. 이게 fallback_ 까지 설정이 갔다는 증거다.
  libi_fleet::CbsTraffic tr2;
  tr2.set_min_separation(g, best + 0.01);
  ASSERT_EQ(tr2.request_move("R1", a, a, 0), MoveDecision::GRANT);
  const int far = (a + g.size() / 2) % g.size();
  EXPECT_EQ(tr2.request_move("R2", far, b, 0), MoveDecision::WAIT)
    << "v" << a << " 를 잡은 로봇이 있는데 " << best << "m 옆 v" << b << " 로 들여보냈다";

  // 규칙을 끄면 예전과 같아야 한다(회귀 방지).
  libi_fleet::CbsTraffic tr3;
  tr3.set_min_separation(g, 0.0);
  ASSERT_EQ(tr3.request_move("R1", a, a, 0), MoveDecision::GRANT);
  EXPECT_EQ(tr3.request_move("R2", far, b, 0), MoveDecision::GRANT)
    << "규칙을 껐는데도 막혔다";
}

// ── 반복 지연 간선: **막지 않고 비싸게** ─────────────────────────────────────
//
// 실기 증상(2026-08-02): "지연 +24.9s 인데 재경로를 안 찾는다".
// 찾긴 찾는데 회피 대상(`blocked`)에 ERROR 로봇만 들어가서 **같은 길이 다시 나왔다.**
// fleet_node 가 마감을 반복해 못 지킨 간선을 `slow_edges` 로 실어 주고, 계획이 그
// 관측을 비용에 되먹인다.

TEST(CbsTrafficSlowEdge, PenalisedEdgeMakesThePlannerPickAnother) {
  set_env(/*clearance=*/1, /*slack=*/1, /*drift_limit=*/10);
  libi_fleet::Navgraph g;
  ASSERT_TRUE(g.load(TEST_NAVGRAPH_PATH, "L1"));
  libi_fleet::CbsTraffic t;

  PlanSnapshot snap;
  snap.graph = &g;
  PlanRequest r; r.robot = "a"; r.start = 0; r.goal = 4; r.priority = 0;
  snap.robots.push_back(r);

  const auto before = t.replan(snap);
  ASSERT_EQ(before.size(), 1u);
  ASSERT_GE(before[0].path.size(), 2u);
  const int first_hop = before[0].path[1];

  // 그 첫 홉이 반복해서 마감을 못 지켰다고 알려 준다.
  snap.slow_edges.push_back({0, first_hop, 10});
  const auto after = t.replan(snap);
  ASSERT_EQ(after.size(), 1u);
  ASSERT_GE(after[0].path.size(), 2u);
  EXPECT_NE(after[0].path[1], first_hop) << "벌점을 줬는데 같은 길을 다시 냈다";
  EXPECT_EQ(after[0].path.back(), 4) << "목적지는 그대로여야 한다";
}

// ⚠️ **막는 것과 다르다.** 대안이 없으면 그 길로 그냥 간다 — 여기서 끊어 버리면
//    시간표가 통째로 실패하고("시간표를 세우지 못했습니다") 편대 전체가 반응형으로
//    내려간다. 느린 길이라도 있는 길이 없는 길보다 낫다.
TEST(CbsTrafficSlowEdge, PenaltyNeverDisconnectsTheGraph) {
  set_env(1, 1, 10);
  libi_fleet::Navgraph g;
  ASSERT_TRUE(g.load(TEST_NAVGRAPH_PATH, "L1"));
  libi_fleet::CbsTraffic t;

  PlanSnapshot snap;
  snap.graph = &g;
  PlanRequest r; r.robot = "a"; r.start = 19; r.goal = 0; r.priority = 0;
  snap.robots.push_back(r);

  const auto before = t.replan(snap);
  ASSERT_EQ(before.size(), 1u);
  const int first_hop = before[0].path[1];

  // 19→0 은 이 첫 홉 말고 길이 없다(실측: 막으면 도달 불가).
  snap.slow_edges.push_back({19, first_hop, 1000});
  const auto after = t.replan(snap);
  ASSERT_EQ(after.size(), 1u) << "대안이 없는데 계획을 포기했다 — 벌점이 통행금지가 됐다";
  EXPECT_EQ(after[0].path[1], first_hop);
  EXPECT_EQ(after[0].path.back(), 0);
}

// 빈 slow_edges 는 예전과 완전히 같아야 한다.
TEST(CbsTrafficSlowEdge, EmptyListChangesNothing) {
  set_env(1, 1, 10);
  libi_fleet::Navgraph g;
  ASSERT_TRUE(g.load(TEST_NAVGRAPH_PATH, "L1"));
  libi_fleet::CbsTraffic t1, t2;

  PlanSnapshot a; a.graph = &g;
  PlanRequest r; r.robot = "a"; r.start = 0; r.goal = 4;
  a.robots.push_back(r);
  PlanSnapshot b = a;
  b.slow_edges.clear();

  const auto pa = t1.replan(a);
  const auto pb = t2.replan(b);
  ASSERT_EQ(pa.size(), pb.size());
  EXPECT_EQ(pa[0].path, pb[0].path);
  EXPECT_EQ(pa[0].arrive_tick, pb[0].arrive_tick);
}

// ── 🔴 회귀: 지연 관용은 **0** 이다 ───────────────────────────────────
//
// 예약 시각을 넘긴 시간표는 그 순간부터 남에게 틀린 통과 허가를 내주고 있다.
// 그래서 봐주는 폭이 없다 — 계획 시각을 지나 요청이 오면 그 자리에서 강등하고
// 재계획을 건다.
//
// ⚠️ **강등해도 로봇은 서지 않는다.** 판정은 반응형(물리 예약)으로 내려갈 뿐이라
//    결정은 GRANT 일 수 있다. 그래서 이 시험이 보는 것은 결정이 아니라
//    `needs_replan()` 이다 — 결정만 보면 아무것도 검증하지 않는 시험이 된다.
TEST(CbsTraffic, LatenessDemotesWithNoTolerance)
{
  set_env(1, 0, 0);              // ← drift_limit 0
  set_env_fast_model();
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto snap = make_snapshot(g, {{"A", 0, 3, 0}});
  const auto routes = tr.replan(snap);
  ASSERT_EQ(routes.size(), 1u);
  if (routes[0].path.size() < 3 || routes[0].arrive_tick[2] < 1) {
    GTEST_SKIP() << "0틱 출발 칸뿐이라 '늦음' 을 만들 수 없다";
  }
  EXPECT_FALSE(tr.needs_replan()) << "계획 직후인데 이미 재계획을 원한다";

  const int a = routes[0].path[0], b = routes[0].path[1], c = routes[0].path[2];
  tr.request_move("A", a, a, 0);
  ASSERT_EQ(tr.request_move("A", a, b, 0), MoveDecision::GRANT);
  tr.release_node("A", a);

  // b→c 의 계획 출발 시각을 확실히 지나서 물어본다.
  sleep_ticks(routes[0].arrive_tick[2] + 2.0);
  tr.request_move("A", b, c, 0);

  EXPECT_TRUE(tr.needs_replan())
    << "계획 시각을 넘겨 요청했는데 시간표를 그대로 들고 있다";
  EXPECT_EQ(tr.last_demote_reason(), "계획 대비 지연");
}

// 같은 상황에서 관용을 주면 강등하지 않는다 — 위 시험이 문턱을 실제로 보고 있다는 증거.
// (이게 없으면 "sleep 하면 늘 강등" 을 통과시키는 시험일 수도 있다.)
TEST(CbsTraffic, LatenessToleratedWhenDriftLimitSet)
{
  set_env(1, 0, 1000);
  set_env_fast_model();
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto routes = tr.replan(make_snapshot(g, {{"A", 0, 3, 0}}));
  ASSERT_EQ(routes.size(), 1u);
  if (routes[0].path.size() < 3 || routes[0].arrive_tick[2] < 1) {
    GTEST_SKIP() << "0틱 출발 칸뿐이라 '늦음' 을 만들 수 없다";
  }

  const int a = routes[0].path[0], b = routes[0].path[1], c = routes[0].path[2];
  tr.request_move("A", a, a, 0);
  ASSERT_EQ(tr.request_move("A", a, b, 0), MoveDecision::GRANT);
  tr.release_node("A", a);

  sleep_ticks(routes[0].arrive_tick[2] + 2.0);
  tr.request_move("A", b, c, 0);

  EXPECT_FALSE(tr.needs_replan()) << "1000틱을 봐주기로 했는데 강등했다";
}

// 기본값이 0 인가. 환경변수를 안 주면 이 정책이 그대로 적용돼야 한다.
TEST(CbsTraffic, DefaultDriftLimitIsZero)
{
  unsetenv("LIBI_CBS_DRIFT_LIMIT");
  libi_fleet::CbsTraffic tr;
  EXPECT_EQ(tr.drift_limit(), 0) << "지연 관용 기본값이 0 이 아니다";
}

// ── 커밋 간선 예산 (`commit_deadline_tick`) ─────────────────────────────
//
// 로봇이 **가고 있는 중**에 재계획하면 계획은 그 목적지 정점을 t=0 으로 잡는다.
// 그런데 로봇은 아직 도착 전이라, 0 을 마감으로 쓰면 계획이 서자마자 초과가 된다.
// 그래서 예전엔 `fleet_node` 가 그 마감을 통째로 버렸고(센티널 -1), 그 구간이
// **무감지**가 됐다 — sim 실측(2026-08-02)에서 25초 지연을 주입한 로봇의 마감 초과
// 경고가 0건이었다.
//
// 답은 0 도 -1 도 아닌 **그 간선의 예산**이고, 그 값은 이 플러그인이 계산해야 한다.
// `fleet_node` 가 거리·속도로 다시 계산하면 회전·노드 정지·slow-edge 가 빠져
// 모델이 갈라진다.

// 커밋 간선을 안 실어 보내면 예전 그대로 -1 이다 — 기존 호출자는 아무 영향 없다.
TEST(CbsTraffic, NoCommitEdgeMeansNoDeadline)
{
  set_env(1, 0, 0);
  set_env_fast_model();
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto routes = tr.replan(make_snapshot(g, {{"A", 0, 3, 0}}));
  ASSERT_EQ(routes.size(), 1u);
  EXPECT_EQ(routes[0].commit_deadline_tick, -1)
    << "커밋 간선을 안 줬는데 마감을 지어냈다";
}

// 커밋 간선을 실어 보내면 **그 간선의 실제 비용**이 돌아온다.
// 값을 상수로 박지 않고 `graph_for()` 로 대조한다 — 그래야 소요 모델을 고쳐도
// 이 시험이 같이 따라온다(박아 두면 모델이 바뀔 때 조용히 거짓말을 한다).
TEST(CbsTraffic, CommitEdgeBudgetMatchesTheTimeModel)
{
  set_env(1, 0, 0);
  set_env_fast_model();
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  const auto & nbrs = g.neighbors(0);
  ASSERT_FALSE(nbrs.empty()) << "정점 0 에 나가는 간선이 없다";
  const int from = 0, to = nbrs[0];

  // 지금 0 → to 간선을 타고 가는 중이라고 알린다.
  PlanRequest pr;
  pr.robot = "A";
  pr.start = to;
  pr.goal = 3;
  pr.committed_from = from;
  const auto routes = tr.replan(make_snapshot(g, {pr}));
  ASSERT_EQ(routes.size(), 1u);

  const auto tg = tr.graph_for(g, {});
  int expected = -1;
  for (const auto & e : tg[from]) { if (e.first == to) { expected = e.second; } }
  ASSERT_GT(expected, 0) << "시험 전제가 깨졌다 — 그 간선의 비용이 없다";

  EXPECT_EQ(routes[0].commit_deadline_tick, expected)
    << "커밋 간선 예산이 플래너의 소요 모델과 다르다";
  EXPECT_GT(routes[0].commit_deadline_tick, routes[0].arrive_tick[0])
    << "계획상 도착틱(0)과 같으면 계획이 서자마자 초과가 된다 — 그게 옛 churn 이다";
}

// 인접하지 않은 정점을 커밋 간선이라고 주면 마감을 지어내지 않는다.
TEST(CbsTraffic, UnknownCommitEdgeYieldsNoDeadline)
{
  set_env(1, 0, 0);
  set_env_fast_model();
  libi_fleet::CbsTraffic tr;
  const auto g = load_graph();

  PlanRequest pr;
  pr.robot = "A";
  pr.start = 0;
  pr.goal = 3;
  pr.committed_from = 999;          // 그래프 밖
  const auto routes = tr.replan(make_snapshot(g, {pr}));
  ASSERT_EQ(routes.size(), 1u);
  EXPECT_EQ(routes[0].commit_deadline_tick, -1) << "없는 간선의 예산을 지어냈다";
}
