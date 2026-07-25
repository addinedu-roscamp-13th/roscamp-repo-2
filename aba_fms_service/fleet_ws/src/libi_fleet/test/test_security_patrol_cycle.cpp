#include <algorithm>
#include <gtest/gtest.h>
#include <vector>

#include "libi_fleet/navgraph.hpp"
#include "libi_fleet/patrol_cycle.hpp"
#include "libi_fleet/security_patrol_cycle.hpp"

using libi_fleet::Navgraph;
using libi_fleet::right_hand_boundary_cycle;
using libi_fleet::security_patrol_boundary_cycle;
using libi_fleet::signed_area_2x;

// 실제 지도 yaml 경로는 CMake 에서 TEST_NAVGRAPH_PATH 로 주입.
static std::string kNavgraph() { return std::string(TEST_NAVGRAPH_PATH); }

// 위임: 지금 security 는 주간 순회와 동일 알고리즘(right_hand_boundary_cycle)이어야 한다.
TEST(SecurityPatrolCycle, DelegatesToRightHandBoundary) {
  Navgraph g;
  ASSERT_TRUE(g.load(kNavgraph(), "L1"));
  EXPECT_EQ(security_patrol_boundary_cycle(g), right_hand_boundary_cycle(g));
}

// CCW 판정: signed_area_2x 부호가 방향을 나타내고, 루트를 뒤집으면 부호가 반대가 된다.
TEST(SecurityPatrolCycle, SignedAreaFlipsOnReverse) {
  Navgraph g;
  ASSERT_TRUE(g.load(kNavgraph(), "L1"));
  auto cyc = right_hand_boundary_cycle(g);
  ASSERT_GE(cyc.size(), 3u);

  const double a = signed_area_2x(g, cyc);
  EXPECT_NE(a, 0.0);                       // 실제 폐곡선이므로 면적 0 아님

  std::vector<int> rev(cyc.rbegin(), cyc.rend());
  const double a_rev = signed_area_2x(g, rev);
  EXPECT_NEAR(a_rev, -a, 1e-9);            // 뒤집으면 부호 반대(크기 동일)
}

// CCW 정규화 규칙: |면적| 은 방향과 무관, 부호만 방향. 3점 미만은 방향 개념 없음(0).
TEST(SecurityPatrolCycle, DegenerateRouteHasZeroArea) {
  Navgraph g;
  ASSERT_TRUE(g.load(kNavgraph(), "L1"));
  EXPECT_EQ(signed_area_2x(g, {}), 0.0);
  EXPECT_EQ(signed_area_2x(g, {0}), 0.0);
  EXPECT_EQ(signed_area_2x(g, {0, 1}), 0.0);
}

// 최근접 진입(make_patrol_path)이 의존하는 Navgraph 기본동작:
//   self(from==to) → 길이 1 경로 [from](빈 경로 아님), 도달가능 → 길이≥2, 도달불가 → 빈 경로.
// make_patrol_path 는 self 를 거리 0 으로, 빈 경로(도달불가)를 skip 으로 다룬다.
TEST(SecurityPatrolCycle, DijkstraSelfAndReachable) {
  Navgraph g;
  ASSERT_TRUE(g.load(kNavgraph(), "L1"));
  ASSERT_GT(g.size(), 1);

  auto self = g.dijkstra(0, 0);
  EXPECT_FALSE(self.empty());                 // self 는 빈 경로가 아니어야 한다(거리 0 처리 근거)

  // 경계순회에 인접한 두 노드는 서로 도달 가능해야 한다(연결 그래프).
  auto cyc = right_hand_boundary_cycle(g);
  ASSERT_GE(cyc.size(), 2u);
  auto p = g.dijkstra(cyc[0], cyc[1]);
  EXPECT_GE(p.size(), 2u);
  EXPECT_GT(g.path_cost(p), 0.0);
}
