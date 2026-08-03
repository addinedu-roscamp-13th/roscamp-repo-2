// 사람이 막은 정점을 **실제로 피해서** 경로가 나오는가.
//
// 사용자 질문 그대로다(2026-08-03): "실제로 사람이 막았을 때 그 간선을 피해서 경로가
// 생성되는거지?" 코드를 읽고 답하는 대신 **실제 지도(arte2)로 계획을 돌려** 확인한다.
//
// 배선: 로봇이 사람 차단을 보고 → `fleet_node` 가 `blocked_until_` 에 넣음 →
//       `replan_all_routes()` 가 `snap.blocked` 로 실음 → `CbsTraffic::replan()` 이
//       `build_graph(ng, blocked)` 로 **차단 정점의 진입 간선을 끊고** CBS 를 돌린다.
// 이 시험은 그 마지막 두 단계(그래프 구성 + 계획)를 실제 지도로 검증한다.
//
// ⚠️ 정점을 **이름이 아니라 구조로** 고른다 — `Navgraph::Vertex` 에는 이름이 없고,
//    지도가 바뀌어도 시험이 따라가야 한다.
//
// ⚠️ 되돌림 확인: `cbs_traffic.hpp` 의 `build_graph` 에서 blocked 검사를 빼면
//    `RouteAvoidsTheBlockedVertex` 와 `BlockedVertexHasNoIncomingEdges` 가 빨개진다.
#include <gtest/gtest.h>

#include <algorithm>
#include <string>
#include <vector>

#include "libi_fleet/cbs_planner.hpp"
#include "libi_fleet/cbs_traffic.hpp"
#include "libi_fleet/navgraph.hpp"

using libi_fleet::CbsTraffic;
using libi_fleet::Navgraph;
using libi_fleet::PlanOptions;
using libi_fleet::Route;
using libi_fleet::TimedGraph;

namespace
{
std::string arte2() { return std::string(TEST_ARTE2_NAVGRAPH_PATH); }

bool contains(const Route & r, int v)
{
  return std::any_of(r.begin(), r.end(), [v](const libi_fleet::Step & s) { return s.v == v; });
}

// 중간 정점이 둘 이상인 경로 하나를 찾는다 — "막을 것" 과 "우회로" 가 둘 다 있어야
// 시험이 뜻을 가진다. 못 찾으면 false(지도가 너무 단순한 것이므로 시험을 건너뛴다).
bool find_case(const Navgraph & g, const TimedGraph & plain, int & start, int & goal, Route & base)
{
  for (int s = 0; s < g.size(); ++s) {
    for (int t = 0; t < g.size(); ++t) {
      if (s == t) { continue; }
      const auto routes = libi_fleet::cbs_plan_timed(plain, {s}, {t}, PlanOptions{});
      if (routes.size() != 1 || routes[0].size() < 4) { continue; }
      start = s; goal = t; base = routes[0];
      return true;
    }
  }
  return false;
}
}  // namespace

// 막힌 정점으로는 **들어가는 간선이 하나도 없어야** 한다.
TEST(BlockedReroute, BlockedVertexHasNoIncomingEdges)
{
  Navgraph g;
  ASSERT_TRUE(g.load(arte2(), "L1"));
  CbsTraffic tr;
  const TimedGraph plain = tr.graph_for(g, {});

  // 진입 간선이 실제로 있는 정점을 하나 고른다(없으면 이 시험이 헛돈다).
  int blocked = -1;
  for (int v = 0; v < static_cast<int>(plain.size()) && blocked < 0; ++v) {
    for (const auto & e : plain[v]) { blocked = e.first; break; }
  }
  ASSERT_GE(blocked, 0);

  const TimedGraph tg = tr.graph_for(g, {blocked});
  for (int v = 0; v < static_cast<int>(tg.size()); ++v) {
    for (const auto & e : tg[v]) {
      EXPECT_NE(e.first, blocked) << "v" << v << " → v" << blocked << " 진입 간선이 남았다";
    }
  }
}

// 나가는 간선은 **남겨야** 한다 — 그 자리에 있는 로봇이 빠져나올 수 있어야 한다.
TEST(BlockedReroute, BlockedVertexKeepsOutgoingEdges)
{
  Navgraph g;
  ASSERT_TRUE(g.load(arte2(), "L1"));
  CbsTraffic tr;
  const TimedGraph plain = tr.graph_for(g, {});

  int blocked = -1;
  for (int v = 0; v < static_cast<int>(plain.size()); ++v) {
    if (!plain[v].empty()) { blocked = v; break; }
  }
  ASSERT_GE(blocked, 0);

  const TimedGraph tg = tr.graph_for(g, {blocked});
  EXPECT_EQ(tg[blocked].size(), plain[blocked].size())
    << "차단 정점에서 나가는 간선까지 끊으면 그 자리의 로봇이 갇힌다";
}

// 실제 계획 — 막힌 정점을 지나지 않는 경로가 나오는가.
TEST(BlockedReroute, RouteAvoidsTheBlockedVertex)
{
  Navgraph g;
  ASSERT_TRUE(g.load(arte2(), "L1"));
  CbsTraffic tr;
  const TimedGraph plain = tr.graph_for(g, {});

  int start = -1, goal = -1;
  Route base;
  ASSERT_TRUE(find_case(g, plain, start, goal, base))
    << "중간 정점이 둘 이상인 경로가 지도에 없다 — 시험할 것이 없다";

  // 원래 경로 **위에 있는** 중간 정점을 막는다. 원래 안 지나는 정점을 막고 통과하면
  // 아무것도 검증하지 않는 시험이 된다.
  const int blocked = base[1].v;
  ASSERT_NE(blocked, start);
  ASSERT_NE(blocked, goal);

  const auto rerouted =
    libi_fleet::cbs_plan_timed(tr.graph_for(g, {blocked}), {start}, {goal}, PlanOptions{});
  if (rerouted.empty()) {
    GTEST_SKIP() << "v" << blocked << " 가 유일 통로라 우회로가 없다 — 이 쌍으로는 검증 불가";
  }
  ASSERT_EQ(rerouted.size(), 1u);
  EXPECT_FALSE(contains(rerouted[0], blocked))
    << "막은 정점 v" << blocked << " 를 그대로 지나간다";
  EXPECT_EQ(rerouted[0].back().v, goal) << "우회해도 목적지는 같아야 한다";
}

// 막힌 정점이 **목적지 자체**면 해가 없어야 한다(들어갈 수 없으므로).
// 이 경우 `replan_all_routes` 는 빈 계획을 내고 반응형으로 떨어진다 — 조용히
// "성공" 으로 보이면 안 되므로 여기서 못 박는다.
TEST(BlockedReroute, BlockingTheGoalYieldsNoPlan)
{
  Navgraph g;
  ASSERT_TRUE(g.load(arte2(), "L1"));
  CbsTraffic tr;
  const TimedGraph plain = tr.graph_for(g, {});

  int start = -1, goal = -1;
  Route base;
  ASSERT_TRUE(find_case(g, plain, start, goal, base));

  const auto routes =
    libi_fleet::cbs_plan_timed(tr.graph_for(g, {goal}), {start}, {goal}, PlanOptions{});
  EXPECT_TRUE(routes.empty()) << "들어갈 수 없는 목적지인데 경로가 나왔다";
}
